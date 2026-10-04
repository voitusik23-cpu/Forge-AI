"""Tests for Forge Execution Plane v0.1 enforcing ephemeral isolation, process lifecycle, and artifacts."""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from app.execution.adapter import (
    ExecutionBackend,
    LocalExecutionAdapter,
    LocalProcessExecutionBackend,
)
from app.execution.artifacts import (
    Artifact,
    create_artifact_from_file,
)
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import ExecutionPolicy
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
    TargetOS,
)
from app.execution.redaction import (
    DefaultSecretRedactor,
    REDACTED_PLACEHOLDER,
)
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.execution.workspace_manager import EphemeralWorkspaceManager
from app.orchestrator.models import EventType
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceGate,
    AcceptanceResult,
    AcceptanceStatus,
)
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolver,
    ApprovalState,
)
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.tools.workspace import Workspace


class _MockApprovalResolver:
    def __init__(self, decision: ApprovalState = ApprovalState.REQUIRED) -> None:
        self.decision = decision
        self.calls: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest) -> ApprovalState:
        self.calls.append(request)
        return self.decision


class TestExecutionPlane(unittest.TestCase):
    """Test suite for Forge Execution Plane v0.1."""

    def setUp(self) -> None:
        self.source_dir = tempfile.TemporaryDirectory()
        self.source_root = Path(self.source_dir.name).resolve()

        # Seed source workspace with initial files
        (self.source_root / "main.py").write_text("print('hello source')\n", encoding="utf-8")
        data_dir = self.source_root / "data"
        data_dir.mkdir()
        (data_dir / "input.txt").write_text("initial data\n", encoding="utf-8")

        self.profile = ProjectExecutionProfile(
            profile_id="test-exec-profile",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            allowed_commands=(sys.executable, "python", "python.exe"),
            timeout_seconds=10.0,
            max_output_bytes=1048576,
            network_access=False,
        )
        self.policy = ExecutionPolicy()
        self.redactor = DefaultSecretRedactor()
        self.adapter = LocalExecutionAdapter(
            workspace_root=self.source_root,
            policy=self.policy,
            redactor=self.redactor,
        )

    def tearDown(self) -> None:
        self.source_dir.cleanup()

    # A. Ephemeral workspace creation
    def test_ephemeral_workspace_creation(self) -> None:
        mgr = EphemeralWorkspaceManager(source_workspace_root=self.source_root)
        self.assertIsNone(mgr._scratch_root)
        scratch = mgr.initialize()
        try:
            self.assertTrue(scratch.exists())
            self.assertTrue(scratch.is_dir())
            self.assertNotEqual(scratch, self.source_root)
            self.assertTrue(scratch.name.startswith("forge_scratch_"))
        finally:
            mgr.cleanup()
        self.assertFalse(scratch.exists())

    # B. Input COPY staging
    def test_input_copy_staging(self) -> None:
        mgr = EphemeralWorkspaceManager(source_workspace_root=self.source_root)
        with mgr:
            scratch = mgr.scratch_root
            staged_main = scratch / "main.py"
            staged_data = scratch / "data" / "input.txt"

            self.assertTrue(staged_main.exists())
            self.assertTrue(staged_data.exists())
            self.assertEqual(staged_main.read_text(encoding="utf-8"), "print('hello source')\n")
            self.assertEqual(staged_data.read_text(encoding="utf-8"), "initial data\n")

    # C. Source workspace remains unchanged
    def test_source_workspace_remains_unchanged(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import pathlib; "
                "pathlib.Path('main.py').write_text('mutated'); "
                "pathlib.Path('new_file.txt').write_text('scratch only'); "
                "pathlib.Path('data/input.txt').unlink()",
            ),
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)

        # Primary workspace must be completely unmodified
        self.assertEqual(
            (self.source_root / "main.py").read_text(encoding="utf-8"),
            "print('hello source')\n",
        )
        self.assertTrue((self.source_root / "data" / "input.txt").exists())
        self.assertFalse((self.source_root / "new_file.txt").exists())

    # D. Workspace cleanup on success
    def test_workspace_cleanup_on_success(self) -> None:
        captured_scratch: list[Path] = []

        class TrackingManager(EphemeralWorkspaceManager):
            def initialize(self) -> Path:
                path = super().initialize()
                captured_scratch.append(path)
                return path

        custom_mgr = TrackingManager(source_workspace_root=self.source_root)
        adapter = LocalExecutionAdapter(
            workspace_root=self.source_root,
            workspace_manager=custom_mgr,
            policy=self.policy,
            redactor=self.redactor,
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('clean-success')"),
            profile=self.profile,
        )
        result = adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(captured_scratch), 1)
        self.assertFalse(captured_scratch[0].exists())

    # E. Workspace cleanup on failure
    def test_workspace_cleanup_on_failure(self) -> None:
        captured_scratch: list[Path] = []

        class TrackingManager(EphemeralWorkspaceManager):
            def initialize(self) -> Path:
                path = super().initialize()
                captured_scratch.append(path)
                return path

        custom_mgr = TrackingManager(source_workspace_root=self.source_root)
        adapter = LocalExecutionAdapter(
            workspace_root=self.source_root,
            workspace_manager=custom_mgr,
            policy=self.policy,
            redactor=self.redactor,
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.exit(42)"),
            profile=self.profile,
        )
        result = adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.FAILURE)
        self.assertEqual(len(captured_scratch), 1)
        self.assertFalse(captured_scratch[0].exists())

    # F. Path traversal rejection
    def test_path_traversal_rejection(self) -> None:
        # Invalid working directory traversal
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print(1)"),
            working_directory="../outside",
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("denied by policy", result.stderr)

        # Invalid artifact target traversal in request validation
        req_bad_target = ExecutionRequest(
            command=(sys.executable, "-c", "print(1)"),
            artifact_targets=("../escape.txt",),
            profile=self.profile,
        )
        errors = req_bad_target.validate()
        self.assertIn("unsafe_artifact_target", errors)

        # Direct manager traversal check
        mgr = EphemeralWorkspaceManager(source_workspace_root=self.source_root)
        with mgr:
            with self.assertRaises(ValueError):
                mgr.resolve_path("../../etc/passwd")

    # G. Artifact extraction
    def test_artifact_extraction(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import pathlib; "
                "pathlib.Path('output.txt').write_text('build artifact data\\n'); "
                "pathlib.Path('report.json').write_text('report_data\\n')",
            ),
            artifact_targets=("output.txt", "report.json"),
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(result.artifacts), 2)

        rel_paths = {a.relative_path for a in result.artifacts}
        self.assertEqual(rel_paths, {"output.txt", "report.json"})

    # H. Artifact SHA-256 correctness
    def test_artifact_sha256_correctness(self) -> None:
        content = b"deterministic artifact content\n"
        expected_sha = hashlib.sha256(content).hexdigest()

        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                f"import pathlib; pathlib.Path('digest.bin').write_bytes({repr(content)})",
            ),
            artifact_targets=("digest.bin",),
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(result.artifacts), 1)

        art = result.artifacts[0]
        self.assertEqual(art.relative_path, "digest.bin")
        self.assertEqual(art.sha256, expected_sha)
        self.assertEqual(art.size_bytes, len(content))

    # I. Artifact run_id/provenance
    def test_artifact_run_id_provenance(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import pathlib; pathlib.Path('prov.txt').write_text('provenance test')",
            ),
            artifact_targets=("prov.txt",),
            metadata={"run_id": "run-42-test"},
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(len(result.artifacts), 1)

        art = result.artifacts[0]
        self.assertEqual(art.run_id, "run-42-test")
        self.assertEqual(art.provenance_source, sys.executable)
        self.assertIn("text/plain", art.media_type)
        self.assertTrue(art.created_at)

    # J. Artifact size bounds
    def test_artifact_size_bounds(self) -> None:
        mgr = EphemeralWorkspaceManager(
            source_workspace_root=self.source_root,
            max_artifact_bytes=50,
        )
        with mgr:
            scratch = mgr.scratch_root
            large_file = scratch / "large.txt"
            large_file.write_text("X" * 100, encoding="utf-8")

            with self.assertRaises(ValueError) as ctx:
                mgr.harvest_artifacts(("large.txt",), run_id="run-size-test")
            self.assertIn("exceeds maximum allowed limit", str(ctx.exception))

    # K. Credential injection
    def test_credential_injection(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import os; print('ENV_VAL=' + os.environ.get('MY_JOB_TOKEN', 'missing'))",
            ),
            environment_variables={"MY_JOB_TOKEN": "injected_secret_token_abc"},
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        # Token was accessible to child process, and then redacted in output
        self.assertIn("ENV_VAL=", result.stdout)
        self.assertNotIn("injected_secret_token_abc", result.stdout)
        self.assertIn(REDACTED_PLACEHOLDER, result.stdout)

    # L. Secret redaction
    def test_secret_redaction(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import os; print('Leaking: ' + os.environ['AUTH_KEY'])",
            ),
            environment_variables={"AUTH_KEY": "super_secret_auth_token_999"},
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertNotIn("super_secret_auth_token_999", result.stdout)
        self.assertIn(REDACTED_PLACEHOLDER, result.stdout)

    # M. Approval blocks before workspace creation
    def test_approval_blocks_before_workspace_creation(self) -> None:
        captured_scratch: list[Path] = []

        class TrackingManager(EphemeralWorkspaceManager):
            def initialize(self) -> Path:
                path = super().initialize()
                captured_scratch.append(path)
                return path

        custom_mgr = TrackingManager(source_workspace_root=self.source_root)
        adapter = LocalExecutionAdapter(
            workspace_root=self.source_root,
            workspace_manager=custom_mgr,
            policy=self.policy,
            redactor=self.redactor,
        )
        coordinator = ExecutionCoordinator(adapter=adapter, policy=self.policy)

        approval_policy = ApprovalPolicy(approval_required_tools=(sys.executable, "python"))
        resolver = _MockApprovalResolver(decision=ApprovalState.REQUIRED)

        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('should not execute')"),
            profile=self.profile,
        )
        result = coordinator.execute(
            req,
            run_id="run-blocked",
            allowed_commands=frozenset([sys.executable]),
            approval_policy=approval_policy,
            approval_resolver=resolver,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(len(captured_scratch), 0)  # Zero scratch workspace created!

    # N. Timeout kills process tree
    def test_timeout_kills_process_tree(self) -> None:
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import time; time.sleep(10)",
            ),
            timeout_seconds=0.2,
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT)
        self.assertTrue(result.timed_out)
        self.assertIn("Process timed out", result.stderr)

    # O. Windows-compatible process termination path
    def test_windows_compatible_process_termination_path(self) -> None:
        # Run a script that spawns a child worker and hangs
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import subprocess, sys, time; "
                "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)']); "
                "time.sleep(10)",
            ),
            timeout_seconds=0.3,
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT)

    # P. Best-effort network restrictions
    def test_best_effort_network_restrictions(self) -> None:
        # Network access is False on profile
        req = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import os; print('PROXY=' + os.environ.get('http_proxy', 'none'))",
            ),
            profile=self.profile,
        )
        result = self.adapter.execute(req)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertIn("PROXY=http://127.0.0.1:0", result.stdout)

    # Q. Cross-run workspace isolation
    def test_cross_run_workspace_isolation(self) -> None:
        req1 = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import pathlib; pathlib.Path('marker_run1.txt').write_text('run1')",
            ),
            profile=self.profile,
        )
        res1 = self.adapter.execute(req1)
        self.assertEqual(res1.status, ExecutionStatus.SUCCESS)

        req2 = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import pathlib; print('EXISTS=' + str(pathlib.Path('marker_run1.txt').exists()))",
            ),
            profile=self.profile,
        )
        res2 = self.adapter.execute(req2)
        self.assertEqual(res2.status, ExecutionStatus.SUCCESS)
        self.assertIn("EXISTS=False", res2.stdout)

    # R. No artifact leakage between runs
    def test_no_artifact_leakage_between_runs(self) -> None:
        req1 = ExecutionRequest(
            command=(
                sys.executable,
                "-c",
                "import pathlib; pathlib.Path('art1.txt').write_text('art1 data')",
            ),
            artifact_targets=("art1.txt",),
            profile=self.profile,
        )
        res1 = self.adapter.execute(req1)
        self.assertEqual(len(res1.artifacts), 1)

        req2 = ExecutionRequest(
            command=(sys.executable, "-c", "print('run2')"),
            artifact_targets=(),
            profile=self.profile,
        )
        res2 = self.adapter.execute(req2)
        self.assertEqual(len(res2.artifacts), 0)

    # S. Existing PermissionPolicy still authoritative
    def test_permission_policy_authoritative(self) -> None:
        coordinator = ExecutionCoordinator(adapter=self.adapter, policy=self.policy)
        req = ExecutionRequest(
            command=("forbidden_binary_xyz", "-c", "print(1)"),
            profile=self.profile,
        )
        result = coordinator.execute(
            req,
            run_id="run-perm",
            allowed_commands=frozenset([sys.executable]),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)

    # T. Existing ApprovalPolicy still authoritative
    def test_approval_policy_authoritative(self) -> None:
        coordinator = ExecutionCoordinator(adapter=self.adapter, policy=self.policy)
        approval_policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        resolver = _MockApprovalResolver(decision=ApprovalState.REJECTED)

        req = ExecutionRequest(
            command=(sys.executable, "-c", "print(1)"),
            profile=self.profile,
        )
        result = coordinator.execute(
            req,
            run_id="run-appr-rej",
            allowed_commands=frozenset([sys.executable]),
            approval_policy=approval_policy,
            approval_resolver=resolver,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_REJECTED)

    # U. Existing Verification/Acceptance still authoritative
    def test_verification_acceptance_authoritative(self) -> None:
        ws = Workspace(self.source_root)
        verifier = WorkspaceVerifier()
        expectation = VerificationExpectation(relative_path="main.py", exists=True)
        v_result = verifier.verify(
            expectation, workspace=ws, run_id="run-u", observer=lambda t, d: None
        )
        self.assertEqual(v_result.status, VerificationStatus.PASS)

        gate = AcceptanceGate()
        crit = AcceptanceCriterion(criterion_id="crit-main", description="main.py exists")
        report = gate.evaluate(
            criteria=[crit],
            verifications={"crit-main": v_result},
            run_id="run-u",
            observer=lambda t, d: None,
        )
        self.assertEqual(report.status, AcceptanceStatus.PASS)


if __name__ == "__main__":
    unittest.main()
