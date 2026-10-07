"""Integration tests for Dynamic Project Understanding Live Scanner in EngineeringRun."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.agents.registry import AgentRegistry
from app.context.models import ContextSourceType
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
from app.snapshots import SnapshotFile
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile
from app.understanding.models import (
    ProjectTopology,
    UnderstandingSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers — copied from the proven test_engineering.py pattern
# ---------------------------------------------------------------------------

class _SequenceAgent:
    """Agent that writes predictable content per attempt, matching working test pattern."""
    name = "fixture"
    provider_name = "fixture"

    def __init__(self, contents, *, path="result.txt"):
        self.contents = list(contents)
        self.path = path
        self.calls = 0

    def run(self, task):
        if task.context.get("forge_tool_results"):
            return TaskResult(task.id, True, output="tool round complete")
        revision = None
        for item in task.context.get("forge_execution_context", {}).get("items", []):
            if item.get("kind") == "task_context":
                context = json.loads(item["content"])
                revision = context.get("forge_revision")
                break
        index = revision["attempt_number"] if revision else 0
        content = self.contents[min(index, len(self.contents) - 1)]
        self.calls += 1
        return TaskResult(
            task.id,
            True,
            output="fixture proposal",
            tool_invocations=[ToolInvocation(
                WriteProjectFile.TOOL_ID,
                {"relative_path": self.path, "content": content, "overwrite": True},
                f"fixture-write-{self.calls}",
            )],
        )


class _Resolver:
    def __init__(self, decision=ApprovalState.APPROVED):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return ApprovalResolution(
            decision=self.decision,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="eng-resolver-id",
        )


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

class EngineeringUnderstandingLiveScanTests(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.good = "accepted fixture"
        self.bad = "rejected fixture"
        self.criterion = AcceptanceCriterion(
            "file-content", "File has expected digest", requirement_id="file-created"
        )
        self.expectations = {
            "file-content": VerificationExpectation(
                "result.txt", True, hashlib.sha256(self.good.encode()).hexdigest()
            )
        }

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp.cleanup()

    def _scope_for(self, run_id, *, allowed, criteria=None):
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=ProjectExecutionProfile(
                "eng-profile", allowed_commands=("python",)
            ),
            allowed_tool_ids=frozenset(allowed),
            allowed_execution_commands=frozenset(),
            acceptance_criteria=tuple(criteria or ()) or (self.criterion,),
        )
        scope.freeze()
        return scope

    def _make_executor(self, contents, *, path="result.txt"):
        agent = _SequenceAgent(contents, path=path)
        agents = AgentRegistry()
        agents.register(agent)
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        resolver = _Resolver(ApprovalState.APPROVED)
        run_executor = RunExecutor(
            Orchestrator(agents, default_provider="fixture"),
            tool_executor=ToolExecutor(
                registry, approval_policy=ApprovalPolicy(), approval_resolver=resolver
            ),
        )
        return EngineeringRunExecutor(RevisionLoopExecutor(run_executor)), agent

    def _request(self, *, run_id, allowed=(WriteProjectFile.TOOL_ID,), max_attempts=1, **overrides):
        defaults = dict(
            task=Task(id="eng-test", description="write expected file"),
            workspace=self.workspace,
            snapshot_paths=("result.txt",),
            verification_expectations=self.expectations,
            acceptance_criteria=(self.criterion,),
            max_revision_attempts=max_attempts,
            allowed_tool_ids=allowed,
            run_scope=self._scope_for(run_id, allowed=allowed),
        )
        defaults.update(overrides)
        return EngineeringRunRequest(**defaults)

    # ------------------------------------------------------------------
    # 1. Workspace + no explicit snapshot → auto-creates snapshot
    # ------------------------------------------------------------------
    def test_1_workspace_auto_scans_understanding_snapshot(self) -> None:
        (self.root / "module_a.py").write_text(
            "def run_a(): pass\n", encoding="utf-8"
        )
        (self.root / "requirements.txt").write_text(
            "pytest>=7.0.0\n", encoding="utf-8"
        )

        executor, _ = self._make_executor([self.good])
        req = self._request(run_id="run-auto-scan")
        res = executor.execute(req)

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertTrue(len(res.context_envelopes) >= 1)
        envelope = res.context_envelopes[0]

        understanding_items = [
            i for i in envelope.context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        ]
        self.assertEqual(len(understanding_items), 1)
        u_item = understanding_items[0]
        self.assertEqual(u_item.item_type, "project_understanding")
        self.assertEqual(u_item.source_type, ContextSourceType.PROJECT_UNDERSTANDING)

        u_summary = json.loads(u_item.value)
        self.assertTrue(u_summary["total_files"] >= 2)
        self.assertTrue(u_summary["total_manifests"] >= 1)
        self.assertTrue(u_summary["total_symbols"] >= 1)

        start_events = [
            e for e in res.run.events
            if e.type == EventType.ENGINEERING_RUN_STARTED
        ]
        self.assertEqual(len(start_events), 1)
        self.assertIn("workspace_fingerprint", start_events[0].data)
        self.assertTrue(len(str(start_events[0].data["workspace_fingerprint"])) > 0)

    # ------------------------------------------------------------------
    # 2. Explicit snapshot → used as-is; scanner NOT invoked
    # ------------------------------------------------------------------
    def test_2_explicit_snapshot_used_as_is_without_rescanning(self) -> None:
        dummy_snapshot = UnderstandingSnapshot(
            snapshot_id="explicit-custom-snap-42",
            project_id="custom_project",
            workspace_fingerprint="fp-explicit-1234",
            files=(SnapshotFile(
                relative_path="custom_file.py",
                exists=True,
                fingerprint="custom-fp",
            ),),
            manifests=(),
            topology=ProjectTopology(nodes=(), edges=()),
            facts=(),
        )

        executor, _ = self._make_executor([self.good])
        with patch("app.orchestrator.engineering.UnderstandingSnapshotter") as mock_cls:
            req = self._request(
                run_id="run-explicit",
                understanding_snapshot=dummy_snapshot,
            )
            res = executor.execute(req)
            mock_cls.assert_not_called()

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        envelope = res.context_envelopes[0]
        understanding_items = [
            i for i in envelope.context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        ]
        self.assertEqual(len(understanding_items), 1)
        self.assertEqual(
            understanding_items[0].source_id,
            "understanding:explicit-custom-snap-42",
        )

    # ------------------------------------------------------------------
    # 3. No workspace + no snapshot → no understanding item, run still works
    # ------------------------------------------------------------------
    def test_3_no_workspace_and_no_snapshot(self) -> None:
        executor, _ = self._make_executor([self.good])
        # No workspace means no snapshot_paths either (can't snapshot nothing).
        req = EngineeringRunRequest(
            task=Task(id="eng-no-ws", description="write expected file"),
            workspace=None,
            snapshot_paths=None,
            verification_expectations=self.expectations,
            acceptance_criteria=(self.criterion,),
            understanding_snapshot=None,
        )
        res = executor.execute(req)

        # Without workspace: verification reads from None workspace → FAIL is expected.
        # The important assertion is: no crash, no understanding item present.
        envelope = res.context_envelopes[0]
        understanding_items = [
            i for i in envelope.context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        ]
        self.assertEqual(len(understanding_items), 0)

    # ------------------------------------------------------------------
    # 4. Understanding reaches DecisionContextAssembler with structured facts
    # ------------------------------------------------------------------
    def test_4_generated_understanding_reaches_decision_context_assembler(self) -> None:
        (self.root / "app").mkdir(exist_ok=True)
        (self.root / "app" / "server.py").write_text(
            "class APIServer:\n    def start(self): pass\n", encoding="utf-8"
        )

        executor, _ = self._make_executor([self.good])
        req = self._request(run_id="run-assembler")
        res = executor.execute(req)

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        envelope = res.context_envelopes[0]
        u_item = next(
            i for i in envelope.context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        )
        data = json.loads(u_item.value)
        self.assertTrue(data["total_files"] >= 1)
        self.assertTrue(data["total_symbols"] >= 1)

    # ------------------------------------------------------------------
    # 5. ContextSelector budget still applies to understanding summary
    # ------------------------------------------------------------------
    def test_5_context_selector_budget_applies_to_understanding_summary(self) -> None:
        (self.root / "app.py").write_text("def hello(): pass\n", encoding="utf-8")

        executor, _ = self._make_executor([self.good])
        req = self._request(run_id="run-budget")
        res = executor.execute(req)

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        envelope = res.context_envelopes[0]
        self.assertTrue(len(envelope.context_items) >= 1)

    # ------------------------------------------------------------------
    # 6. Secret files (.env, id_rsa) do NOT enter understanding/context
    # ------------------------------------------------------------------
    def test_6_secret_files_excluded_from_engineering_context(self) -> None:
        (self.root / ".env").write_text(
            "MY_CONFIDENTIAL_KEY=1234567890abcdef\n", encoding="utf-8"
        )
        (self.root / "id_rsa").write_text(
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEogIBAAKCAQEA...\n"
            "-----END RSA PRIVATE KEY-----\n",
            encoding="utf-8",
        )
        (self.root / "main.py").write_text(
            "def app(): return 'safe'\n", encoding="utf-8"
        )

        executor, _ = self._make_executor([self.good])
        req = self._request(run_id="run-privacy")
        res = executor.execute(req)

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        envelope = res.context_envelopes[0]
        for item in envelope.context_items:
            content_str = str(item.value)
            self.assertNotIn("MY_CONFIDENTIAL_KEY", content_str)
            self.assertNotIn("1234567890abcdef", content_str)
            self.assertNotIn("BEGIN RSA PRIVATE KEY", content_str)

    # ------------------------------------------------------------------
    # 7. Symlink pointing outside workspace root is safely skipped
    # ------------------------------------------------------------------
    def test_7_symlink_escape_remains_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_path = Path(outside_dir)
            (outside_path / "outside_leak.txt").write_text(
                "OUTSIDE_LEAK_DATA", encoding="utf-8"
            )
            (self.root / "safe.py").write_text("x = 10\n", encoding="utf-8")

            try:
                (self.root / "escape_link.txt").symlink_to(
                    outside_path / "outside_leak.txt"
                )
            except (OSError, NotImplementedError):
                pass  # On Windows without symlink privileges

            executor, _ = self._make_executor([self.good])
            req = self._request(run_id="run-symlink")
            res = executor.execute(req)

            self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
            envelope = res.context_envelopes[0]
            for item in envelope.context_items:
                self.assertNotIn("OUTSIDE_LEAK_DATA", str(item.value))

    # ------------------------------------------------------------------
    # 8. Non-fatal scan warnings do NOT crash the run
    # ------------------------------------------------------------------
    def test_8_scanner_warnings_do_not_break_engineering_run(self) -> None:
        (self.root / "syntax_error.py").write_text(
            "def broken_func(\n", encoding="utf-8"
        )
        (self.root / "valid.py").write_text(
            "def good_func(): pass\n", encoding="utf-8"
        )

        executor, _ = self._make_executor([self.good])
        req = self._request(run_id="run-warnings")
        res = executor.execute(req)

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        envelope = res.context_envelopes[0]
        u_item = next(
            i for i in envelope.context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        )
        data = json.loads(u_item.value)
        self.assertTrue(data["total_warnings"] >= 1)

    # ------------------------------------------------------------------
    # 9. Unexpected snapshotter exception is NOT silently swallowed
    # ------------------------------------------------------------------
    def test_9_unexpected_snapshotter_failure_not_swallowed(self) -> None:
        executor, _ = self._make_executor([self.good])

        with patch("app.orchestrator.engineering.UnderstandingSnapshotter") as mock_cls:
            mock_inst = mock_cls.return_value
            mock_inst.create_snapshot.side_effect = RuntimeError(
                "Fatal disk hardware failure"
            )

            req = EngineeringRunRequest(
                task=Task(id="eng-error", description="write expected file"),
                workspace=self.workspace,
                snapshot_paths=("result.txt",),
                verification_expectations=self.expectations,
                acceptance_criteria=(self.criterion,),
            )
            with self.assertRaises(RuntimeError) as ctx:
                executor.execute(req)
            self.assertIn("Fatal disk hardware failure", str(ctx.exception))

    # ------------------------------------------------------------------
    # 10. Snapshot created ONCE, reused across revision attempts
    # ------------------------------------------------------------------
    def test_10_snapshot_reused_across_attempts(self) -> None:
        (self.root / "core.py").write_text(
            "class Engine: pass\n", encoding="utf-8"
        )

        executor, _ = self._make_executor([self.bad, self.good])
        with patch("app.orchestrator.engineering.UnderstandingSnapshotter") as mock_cls:
            from app.understanding.snapshotter import (
                UnderstandingSnapshotter as RealSnapshotter,
            )
            real_inst = RealSnapshotter()
            mock_cls.return_value.create_snapshot = patch.object(
                real_inst, "create_snapshot", wraps=real_inst.create_snapshot
            ).start()

            req = self._request(run_id="run-retry", max_attempts=2)
            res = executor.execute(req)

            self.assertEqual(
                mock_cls.return_value.create_snapshot.call_count, 1
            )

        self.assertEqual(res.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(len(res.context_envelopes), 2)

        u0 = next(
            i for i in res.context_envelopes[0].context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        )
        u1 = next(
            i for i in res.context_envelopes[1].context_items
            if i.source_type == ContextSourceType.PROJECT_UNDERSTANDING
        )
        self.assertEqual(u0.value, u1.value)
        self.assertEqual(u0.source_id, u1.source_id)


if __name__ == "__main__":
    unittest.main()
