"""End-to-end durability tests: EngineeringRun history survives a restart.

These tests drive the real ``EngineeringRunExecutor`` with a durable store, then
read the history back through a brand new store instance and through the
read-only API surface. They assert two things at once:

* history and state are durable across a simulated process restart;
* nothing is resumed automatically -- an interrupted Run stays inert.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.agents.registry import AgentRegistry
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import EventType, Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import RevisionLoopExecutor
from app.orchestrator.run import RunExecutor
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile

GOOD = "accepted fixture"


class _FixtureAgent:
    name = "fixture"
    provider_name = "fixture"

    def __init__(self, contents, *, path: str = "result.txt") -> None:
        self.contents = list(contents)
        self.path = path
        self.calls = 0

    def run(self, task):
        if task.context.get("forge_tool_results"):
            return TaskResult(task.id, True, output="tool round complete")
        revision = None
        for item in task.context.get("forge_execution_context", {}).get("items", []):
            if item.get("kind") == "task_context":
                revision = json.loads(item["content"]).get("forge_revision")
                break
        index = revision["attempt_number"] if revision else 0
        content = self.contents[min(index, len(self.contents) - 1)]
        self.calls += 1
        return TaskResult(
            task.id,
            True,
            output="fixture proposal",
            tool_invocations=[
                ToolInvocation(
                    WriteProjectFile.TOOL_ID,
                    {"relative_path": self.path, "content": content, "overwrite": True},
                    f"fixture-write-{self.calls}",
                )
            ],
        )


class _Resolver:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="durable-resolver",
        )


class _ExplodingStore:
    """Store double that fails every write, to prove persistence is best-effort."""

    def bind_run(self, run_id, *, task_id=None):
        return self

    def __call__(self, event_type, data):
        raise RuntimeError("disk on fire")


class EngineeringRunDurabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.ws_root = self.base / "ws"
        self.ws_root.mkdir()
        self.store_root = self.base / "runs"
        self.workspace = Workspace(self.ws_root)
        self.criterion = AcceptanceCriterion(
            "file-content", "File has expected digest", requirement_id="file-created"
        )
        self.expectations = {
            "file-content": VerificationExpectation(
                "result.txt", True, hashlib.sha256(GOOD.encode()).hexdigest()
            )
        }

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _scope_for(self, run_id, *, allowed):
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=ProjectExecutionProfile(
                "durable-profile", allowed_commands=("python",)
            ),
            allowed_tool_ids=frozenset(allowed),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=(self.criterion,),
        )
        scope.freeze()
        return scope

    def _make_executor(self, contents):
        agent = _FixtureAgent(contents)
        agents = AgentRegistry()
        agents.register(agent)
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        run_executor = RunExecutor(
            Orchestrator(agents, default_provider="fixture"),
            tool_executor=ToolExecutor(
                registry,
                approval_policy=ApprovalPolicy(),
                approval_resolver=_Resolver(),
            ),
        )
        return EngineeringRunExecutor(RevisionLoopExecutor(run_executor))

    def _request(self, *, run_id, store, max_attempts=1, observer=None):
        allowed = (WriteProjectFile.TOOL_ID,)
        return EngineeringRunRequest(
            task=Task(id="durable-task", description="write expected file"),
            workspace=self.workspace,
            snapshot_paths=("result.txt",),
            verification_expectations=self.expectations,
            acceptance_criteria=(self.criterion,),
            max_revision_attempts=max_attempts,
            allowed_tool_ids=allowed,
            run_scope=self._scope_for(run_id, allowed=allowed),
            run_store=store,
            observer=observer,
        )

    # ------------------------------------------------------------------
    def test_engineering_run_history_is_durable_across_restart(self) -> None:
        store = RunStore(self.store_root)
        executor = self._make_executor([GOOD])
        res = executor.execute(self._request(run_id="durable-1", store=store))
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)

        # Simulated restart: only files carry state forward.
        recovered = RunStore(self.store_root).load("durable-1")
        self.assertIsNotNone(recovered)
        self.assertGreater(recovered.event_count, 0)
        self.assertIsNotNone(recovered.snapshot)
        self.assertEqual(recovered.snapshot.task_id, "durable-task")
        self.assertEqual(recovered.snapshot.status, "SUCCESS")
        self.assertIn(0, recovered.snapshot.completed_attempt_indexes)

        names = [e.event_type.name for e in recovered.events]
        self.assertIn("ENGINEERING_RUN_STARTED", names)
        self.assertIn("PROVIDER_SELECTED", names)
        self.assertIn("TOOL_EXECUTION_COMPLETED", names)
        self.assertIn("ENGINEERING_RUN_COMPLETED", names)

    def test_persisted_sequence_is_monotonic(self) -> None:
        store = RunStore(self.store_root)
        executor = self._make_executor([GOOD])
        executor.execute(self._request(run_id="durable-seq", store=store))

        events = RunStore(self.store_root).read_events("durable-seq")
        sequences = [e.sequence_number for e in events]
        self.assertEqual(sequences, list(range(len(sequences))))

    def test_multi_attempt_run_persists_every_attempt(self) -> None:
        store = RunStore(self.store_root)
        executor = self._make_executor(["rejected fixture", GOOD])
        res = executor.execute(
            self._request(run_id="durable-attempts", store=store, max_attempts=2)
        )
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)

        recovered = RunStore(self.store_root).load("durable-attempts")
        self.assertEqual(recovered.snapshot.completed_attempt_indexes, (0, 1))
        revision_events = [
            e for e in recovered.events if e.event_type == EventType.REVISION_COMPLETED
        ]
        self.assertGreaterEqual(len(revision_events), 1)

    def test_persisted_history_contains_no_raw_sensitive_payload(self) -> None:
        store = RunStore(self.store_root)
        executor = self._make_executor([GOOD])
        executor.execute(self._request(run_id="durable-safe", store=store))

        raw = RunStore(self.store_root).events_path("durable-safe").read_text(
            encoding="utf-8"
        )
        for forbidden_key in (
            '"stdout"',
            '"stderr"',
            '"prompt"',
            '"api_key"',
            '"secret"',
            '"password"',
            '"credential"',
        ):
            self.assertNotIn(forbidden_key, raw)

    def test_store_failure_does_not_break_the_run(self) -> None:
        """Persistence is best-effort: a broken store must not fail a Run."""
        executor = self._make_executor([GOOD])
        with self.assertLogs("forge_ai", level="WARNING"):
            res = executor.execute(
                self._request(run_id="durable-broken", store=_ExplodingStore())
            )
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)

    def test_observer_still_works_alongside_persistence(self) -> None:
        seen: list[EventType] = []
        store = RunStore(self.store_root)
        executor = self._make_executor([GOOD])
        res = executor.execute(
            self._request(
                run_id="durable-observer",
                store=store,
                observer=lambda event_type, data: seen.append(event_type),
            )
        )
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertIn(EventType.ENGINEERING_RUN_STARTED, seen)
        self.assertIn(EventType.ENGINEERING_RUN_COMPLETED, seen)
        # Both sinks received the same run.
        recovered = RunStore(self.store_root).load("durable-observer")
        self.assertGreater(recovered.event_count, 0)

    def test_observer_exception_does_not_prevent_persistence(self) -> None:
        store = RunStore(self.store_root)
        executor = self._make_executor([GOOD])

        def explosive(event_type, data):
            raise RuntimeError("observer exploded")

        with self.assertLogs("forge_ai", level="WARNING"):
            res = executor.execute(
                self._request(run_id="durable-explosive", store=store, observer=explosive)
            )
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        recovered = RunStore(self.store_root).load("durable-explosive")
        self.assertGreater(recovered.event_count, 0)

    def test_no_automatic_resume_of_an_interrupted_run(self) -> None:
        """Loading an unfinished Run must not re-run tools or execution.

        The durable record is history. Nothing in the load path may spawn a
        process, write a file, or replay a tool invocation.
        """
        store = RunStore(self.store_root)
        store.bind_run("interrupted-run", task_id="interrupted-task")
        store(EventType.ENGINEERING_RUN_STARTED, {"status": "started"})
        store(
            EventType.TOOL_EXECUTION_STARTED,
            {"tool_id": WriteProjectFile.TOOL_ID, "invocation_id": "inv-pending"},
        )
        store(
            EventType.EXECUTION_REQUESTED,
            {"execution_request_id": "req-pending", "command": "python"},
        )
        # Simulated crash: no ENGINEERING_RUN_COMPLETED, no final snapshot.
        before = sorted(p.name for p in self.ws_root.iterdir())

        # A fresh process reads the history back.
        recovered = RunStore(self.store_root).load("interrupted-run")
        self.assertIsNotNone(recovered)
        self.assertIsNone(recovered.snapshot, "an interrupted run has no final snapshot")
        names = [e.event_type.name for e in recovered.events]
        self.assertIn("TOOL_EXECUTION_STARTED", names)
        self.assertNotIn("ENGINEERING_RUN_COMPLETED", names)

        # No side effect happened as a consequence of reading.
        self.assertEqual(sorted(p.name for p in self.ws_root.iterdir()), before)
        self.assertFalse((self.ws_root / "result.txt").exists())


class RunLookupApiTests(unittest.TestCase):
    """GET /api/runs/{run_id} over the real ASGI app."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store_root = Path(self._tmp.name) / "runs"
        self.store = RunStore(self.store_root)
        self.store.bind_run("api-run", task_id="api-task")
        self.store(EventType.ENGINEERING_RUN_STARTED, {"status": "started"})
        self.store(
            EventType.ENGINEERING_RUN_COMPLETED,
            {"status": "SUCCESS", "stdout": "must-not-leak", "prompt": "must-not-leak"},
        )
        from app.orchestrator.models import Run, RunState, Task

        run = Run(task=Task(id="api-task", description="d"), id="api-run")
        run.state = RunState.COMPLETED
        self.store.record_run_state(
            run,
            status="SUCCESS",
            attempt_number=0,
            completed_attempt_indexes=(0,),
            project_state={"status": "ACCEPTED"},
            accounting={"total_tokens": 10, "total_cost": 0.01},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _service(self):
        from app.api.service import ForgeApiService

        service = ForgeApiService.__new__(ForgeApiService)
        service._run_store = self.store
        return service

    def test_service_returns_persisted_summary(self) -> None:
        record = self._service().get_run_record("api-run")
        self.assertIsNotNone(record)
        self.assertEqual(record["run_id"], "api-run")
        self.assertEqual(record["state"], "COMPLETED")
        self.assertEqual(record["status"], "SUCCESS")
        self.assertEqual(record["task_id"], "api-task")
        self.assertEqual(record["completed_attempt_indexes"], [0])
        self.assertEqual(record["event_count"], 2)
        self.assertEqual(record["last_sequence_number"], 1)
        self.assertEqual(record["accounting"]["total_tokens"], 10)
        self.assertEqual(record["project_state"], {"status": "ACCEPTED"})

    def test_service_does_not_expose_sensitive_metadata(self) -> None:
        blob = json.dumps(self._service().get_run_record("api-run"), default=str)
        self.assertNotIn("must-not-leak", blob)
        self.assertNotIn("stdout", blob)
        self.assertNotIn("prompt", blob)

    def test_unknown_run_id_returns_none(self) -> None:
        self.assertIsNone(self._service().get_run_record("does-not-exist"))

    def test_unsafe_run_id_returns_none(self) -> None:
        for bad in ("../../etc/passwd", "a/b", "", "   "):
            self.assertIsNone(self._service().get_run_record(bad))

    def test_endpoint_returns_200_and_404(self) -> None:
        try:
            from fastapi.testclient import TestClient
        except Exception:  # pragma: no cover - optional dependency
            self.skipTest("fastapi TestClient unavailable")
        from app.api.server import create_api_app

        client = TestClient(create_api_app(service=self._service()))
        ok = client.get("/api/runs/api-run")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["run_id"], "api-run")
        missing = client.get("/api/runs/nope")
        self.assertEqual(missing.status_code, 404)
        # The durable record is read-only: reading twice is stable.
        self.assertEqual(client.get("/api/runs/api-run").json()["event_count"], 2)


if __name__ == "__main__":
    unittest.main()
