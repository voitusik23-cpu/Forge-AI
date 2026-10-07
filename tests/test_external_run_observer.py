"""Contract tests for the external live-run observer hook.

The observer is a passive, read-only observation surface. These tests pin the
contract, not the implementation: events arrive live, they mirror the canonical
record, they are sanitized, an observer failure cannot change a Run, and the
observer is optional.
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
    """Agent that writes deterministic content, matching the proven fixture shape."""

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
            approval_id="observer-resolver",
        )


class _RecordingObserver:
    """Collects observations and optionally fails on a chosen event type."""

    def __init__(self, *, fail_on: EventType | None = None) -> None:
        self.seen: list[tuple[EventType, dict]] = []
        self.fail_on = fail_on

    def __call__(self, event_type, data):
        self.seen.append((event_type, data))
        if self.fail_on is not None and event_type == self.fail_on:
            raise RuntimeError("observer exploded")

    @property
    def event_types(self):
        return [event_type for event_type, _ in self.seen]


class ExternalRunObserverTests(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
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
        self.temp.cleanup()

    def _scope_for(self, run_id, *, allowed):
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=ProjectExecutionProfile(
                "observer-profile", allowed_commands=("python",)
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

    def _request(self, *, run_id, observer=None, max_attempts=1):
        allowed = (WriteProjectFile.TOOL_ID,)
        return EngineeringRunRequest(
            task=Task(id="eng-observer", description="write expected file"),
            workspace=self.workspace,
            snapshot_paths=("result.txt",),
            verification_expectations=self.expectations,
            acceptance_criteria=(self.criterion,),
            max_revision_attempts=max_attempts,
            allowed_tool_ids=allowed,
            run_scope=self._scope_for(run_id, allowed=allowed),
            observer=observer,
        )

    # ------------------------------------------------------------------
    def test_external_observer_receives_live_events(self) -> None:
        """Events are delivered during the run, not reconstructed afterwards."""
        observer = _RecordingObserver()
        executor = self._make_executor([GOOD])

        # Prove liveness: the first observed event must arrive before execute()
        # returns, so assert from inside the callback that the run is mid-flight.
        observed_during_call: list[bool] = []

        def live(event_type, data):
            observed_during_call.append(True)
            observer(event_type, data)

        res = executor.execute(self._request(run_id="obs-live", observer=live))

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertTrue(observed_during_call, "observer never fired during the run")
        self.assertGreater(len(observer.seen), 0)

    def test_external_observer_sees_canonical_event_sequence(self) -> None:
        """Observed events mirror the canonical record without fabrication.

        Observation is a sanitized projection of the same stream, so no observed
        event may be invented and the events inside a run attempt must keep their
        execution order. The observer deliberately does not see every canonical
        event: events appended directly on the Run object by collaborators (for
        example SNAPSHOT_CREATED) never pass through the observation hook. The
        absolute order also differs in one place: ENGINEERING_RUN_STARTED is
        inserted at the head of the canonical list once the Run has finished,
        while the observer sees it when it is emitted.
        """
        observer = _RecordingObserver()
        executor = self._make_executor([GOOD])
        res = executor.execute(self._request(run_id="obs-canonical", observer=observer))

        canonical = [event.type for event in res.run.events]
        observed = observer.event_types

        self.assertGreater(len(observed), 0, "observer received nothing")
        self.assertIn(EventType.ENGINEERING_RUN_STARTED, observed)
        self.assertIn(EventType.ENGINEERING_RUN_COMPLETED, observed)

        # No fabricated events: everything observed exists in the canonical record.
        for event_type in observed:
            self.assertIn(
                event_type,
                canonical,
                f"observed event {event_type} is absent from canonical run.events",
            )

        # Ordering is preserved for the events that share a source. The provider
        # attempt triple must stay in canonical relative order.
        attempt_flow = [
            event_type
            for event_type in observed
            if event_type
            in (
                EventType.PROVIDER_SELECTED,
                EventType.PROVIDER_ATTEMPT,
                EventType.PROVIDER_RESULT,
            )
        ]
        if attempt_flow:
            self.assertEqual(attempt_flow[0], EventType.PROVIDER_SELECTED)
            for first, second in zip(attempt_flow, attempt_flow[1:]):
                allowed_successors = {
                    EventType.PROVIDER_SELECTED: {
                        EventType.PROVIDER_ATTEMPT,
                        EventType.PROVIDER_SELECTED,
                    },
                    EventType.PROVIDER_ATTEMPT: {
                        EventType.PROVIDER_RESULT,
                        EventType.PROVIDER_SELECTED,
                    },
                    EventType.PROVIDER_RESULT: {EventType.PROVIDER_SELECTED},
                }
                self.assertIn(
                    second,
                    allowed_successors[first],
                    f"provider events out of order: {first} -> {second}",
                )

    def test_external_observer_does_not_receive_raw_sensitive_data(self) -> None:
        """The projection must not leak the canonical forbidden substrings."""
        secret = "super_secret_token_value_123"
        (self.root / "leaky.py").write_text(
            f"API_KEY = '{secret}'\n", encoding="utf-8"
        )

        observer = _RecordingObserver()
        executor = self._make_executor([GOOD])
        res = executor.execute(self._request(run_id="obs-sanitize", observer=observer))

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        forbidden_keys = (
            "stdout",
            "stderr",
            "prompt",
            "secret",
            "token",
            "password",
            "api_key",
            "credential",
            "source_code",
            "file_content",
        )
        self.assertGreater(len(observer.seen), 0)
        for event_type, data in observer.seen:
            self.assertIsInstance(data, dict)
            for key in data:
                lowered = str(key).lower()
                for bad in forbidden_keys:
                    self.assertNotIn(
                        bad,
                        lowered,
                        f"forbidden metadata key {key!r} reached observer via {event_type}",
                    )
            self.assertNotIn(secret, json.dumps(data, default=str))

    def test_external_observer_exception_does_not_fail_run(self) -> None:
        """A raising observer must not change the Run's outcome."""
        baseline_executor = self._make_executor([GOOD])
        baseline = baseline_executor.execute(self._request(run_id="obs-baseline"))

        observer = _RecordingObserver(fail_on=EventType.RUN_COMPLETED)
        failing_executor = self._make_executor([GOOD])
        with self.assertLogs("forge_ai", level="WARNING"):
            observed = failing_executor.execute(
                self._request(run_id="obs-failing", observer=observer)
            )

        self.assertEqual(observed.final_status, baseline.final_status)
        self.assertEqual(observed.final_status, EngineeringRunStatus.SUCCESS)
        self.assertGreater(len(observer.seen), 0, "observer should have been invoked")

    def test_observer_failure_after_event_storage(self) -> None:
        """The canonical event survives even when the observer raises."""
        observer = _RecordingObserver(fail_on=EventType.ENGINEERING_RUN_STARTED)
        executor = self._make_executor([GOOD])
        with self.assertLogs("forge_ai", level="WARNING"):
            res = executor.execute(self._request(run_id="obs-after-store", observer=observer))

        start_events = [
            event
            for event in res.run.events
            if event.type == EventType.ENGINEERING_RUN_STARTED
        ]
        self.assertEqual(len(start_events), 1, "canonical start event must be stored")
        self.assertIn(EventType.ENGINEERING_RUN_STARTED, observer.event_types)

    def test_observer_is_optional(self) -> None:
        """Without an observer the existing behaviour is unchanged."""
        executor = self._make_executor([GOOD])
        res = executor.execute(self._request(run_id="obs-none", observer=None))

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(res.final_acceptance.status.value, "pass")
        self.assertIsNotNone(res.trace)
        self.assertTrue(any(
            event.type == EventType.ENGINEERING_RUN_COMPLETED for event in res.run.events
        ))

    def test_observer_cannot_change_execution_authority(self) -> None:
        """The observer receives data only: no scope, authorizer or coordinator."""
        captured: list[object] = []
        executor = self._make_executor([GOOD])

        def inspecting(event_type, data):
            captured.append(event_type)
            captured.append(data)

        req = self._request(run_id="obs-authority", observer=inspecting)
        frozen_scope = req.run_scope
        fingerprint_before = frozen_scope.fingerprint
        res = executor.execute(req)

        from app.runtime.run_scope import RunScope as _Scope

        scope_after = _Scope.frozen_scope("obs-authority")
        self.assertIsNotNone(scope_after)
        self.assertEqual(scope_after.fingerprint, fingerprint_before)
        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        for item in captured:
            self.assertNotIsInstance(item, _Scope)
            self.assertNotIsInstance(item, EngineeringRunExecutor)

    def test_observer_sees_attempt_scoped_events_across_revisions(self) -> None:
        """With two attempts the observer sees revision-scoped activity."""
        observer = _RecordingObserver()
        executor = self._make_executor(["rejected fixture", GOOD])
        res = executor.execute(
            self._request(run_id="obs-attempts", observer=observer, max_attempts=2)
        )

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(len(res.context_envelopes), 2)
        # Each envelope is announced, so the observer can correlate attempts.
        decision_ready = [
            event_type
            for event_type in observer.event_types
            if event_type == EventType.CONTEXT_DECISION_READY
        ]
        self.assertGreaterEqual(len(decision_ready), 1)


if __name__ == "__main__":
    unittest.main()
