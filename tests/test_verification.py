"""Offline tests for the read-only workspace verification boundary."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.mock_agent import MockAgent
from app.agents.registry import AgentRegistry
from app.orchestrator.models import Event, EventType, RunState, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import (
    VerificationExpectation,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class _Resolver:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="verif-approval-id",
        )


class WorkspaceVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.root = self.base / "project"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.verifier = WorkspaceVerifier()
        self.events = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def verify(self, path, exists=True, sha256=None, workspace="default"):
        selected = self.workspace if workspace == "default" else workspace
        return self.verifier.verify(
            VerificationExpectation(path, exists, sha256),
            workspace=selected,
            run_id="run-test",
            observer=lambda kind, data: self.events.append((kind, data)),
            verification_id="verification-test",
        )

    def test_correct_existing_file_passes_with_deterministic_fingerprint(self):
        payload = b"known fixture content"
        (self.root / "result.txt").write_bytes(payload)
        expected_hash = hashlib.sha256(payload).hexdigest()

        first = self.verify("result.txt", sha256=expected_hash)
        second = self.verify("result.txt", sha256=expected_hash)

        self.assertEqual(first.status, VerificationStatus.PASS)
        self.assertEqual(first.fingerprint, expected_hash)
        self.assertEqual(second.fingerprint, first.fingerprint)

    def test_mismatch_missing_and_unexpected_presence_fail(self):
        (self.root / "present.txt").write_text("actual", encoding="utf-8")
        mismatch = self.verify("present.txt", sha256=hashlib.sha256(b"expected").hexdigest())
        missing = self.verify("missing.txt", exists=True)
        unexpected = self.verify("present.txt", exists=False)
        absent = self.verify("absent.txt", exists=False)

        self.assertEqual(mismatch.code, "content_hash_mismatch")
        self.assertEqual(missing.code, "expected_file_missing")
        self.assertEqual(unexpected.code, "unexpected_file_present")
        self.assertEqual(absent.status, VerificationStatus.PASS)

    def test_invalid_paths_are_denied_without_opening_any_candidate(self):
        candidates = (
            "../outside.txt",
            "nested/../../outside.txt",
            "C:\\Windows\\win.ini",
            "/etc/passwd",
            "C:secret.txt",
            "file.txt:stream",
        )
        for candidate in candidates:
            with self.subTest(path=candidate):
                with patch.object(Path, "open", side_effect=AssertionError("must not read")):
                    result = self.verify(candidate)
                self.assertEqual(result.status, VerificationStatus.DENIED)
                self.assertIsNone(result.relative_path)

    def test_platform_absolute_path_is_denied(self):
        result = self.verify(str(self.base / "outside.txt"))
        self.assertEqual(result.status, VerificationStatus.DENIED)

    def test_missing_workspace_or_removed_root_fails_closed(self):
        self.assertEqual(self.verify("a.txt", workspace=None).status, VerificationStatus.DENIED)
        self.root.rmdir()
        removed = self.verify("a.txt")
        self.assertEqual(removed.status, VerificationStatus.DENIED)
        self.assertEqual(removed.code, "workspace_or_path_rejected")

    def test_verification_does_not_mutate_workspace(self):
        (self.root / "keep.txt").write_text("same", encoding="utf-8")
        before = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        self.verify("keep.txt", sha256=hashlib.sha256(b"same").hexdigest())
        after = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def make_runner(self, relative_path, content):
        invocation = ToolInvocation(
            WriteProjectFile.TOOL_ID,
            {"relative_path": relative_path, "content": content},
            "invocation-test",
        )
        agents = AgentRegistry()
        agent = MockAgent(tool_invocations=(invocation,))
        agents.register(agent)
        orchestrator = Orchestrator(agents, default_provider="mock")
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        runner = RunExecutor(
            orchestrator,
            tool_executor=ToolExecutor(
                registry,
                approval_policy=ApprovalPolicy(),
                approval_resolver=_Resolver(),
            ),
        )
        return runner

    def execute_and_verify(self, content):
        runner = self.make_runner("output.txt", content)
        task = Task(id="verification-run", description="write and verify")
        run = runner.execute(
            task, allowed_tool_ids=(WriteProjectFile.TOOL_ID,), workspace=self.workspace
        )
        write_result = run.result.tool_results[0]
        self.assertEqual(write_result.status, ToolStatus.COMPLETED)
        expected = VerificationExpectation(
            "output.txt", True, hashlib.sha256(content.encode("utf-8")).hexdigest()
        )
        verification = self.verifier.verify(
            expected,
            workspace=self.workspace,
            run_id=run.id,
            observer=lambda kind, data: run.events.append(
                Event(run_id=run.id, type=kind, data=data)
            ),
            verification_id="verification-integration",
        )
        return run, verification

    def test_run_write_result_then_verification_passes_and_emits_safe_trace(self):
        secretish_content = "fixture output that must not enter event data"
        run, verification = self.execute_and_verify(secretish_content)
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertEqual(verification.status, VerificationStatus.PASS)
        event = next(e for e in run.events if e.type == EventType.VERIFICATION_COMPLETED)
        self.assertEqual(event.data["run_id"], run.id)
        self.assertEqual(event.data["verification_id"], verification.verification_id)
        self.assertEqual(event.data["relative_path"], "output.txt")
        self.assertEqual(event.data["fingerprint"], verification.fingerprint)
        self.assertNotIn(secretish_content, repr(run.events))

    def test_external_change_fails_verification_without_auto_repair(self):
        original = "written by approved tool"
        run, _ = self.execute_and_verify(original)
        target = self.root / "output.txt"
        target.write_text("externally changed", encoding="utf-8")
        failed = self.verifier.verify(
            VerificationExpectation(
                "output.txt", True, hashlib.sha256(original.encode()).hexdigest()
            ),
            workspace=self.workspace,
            run_id=run.id,
            observer=lambda kind, data: run.events.append(
                Event(run_id=run.id, type=kind, data=data)
            ),
        )
        self.assertEqual(failed.status, VerificationStatus.FAIL)
        self.assertEqual(target.read_text(encoding="utf-8"), "externally changed")


if __name__ == "__main__":
    unittest.main()
