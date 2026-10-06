"""Comprehensive Invariant and Regression Test Suite for Execution Authorization Contract Reset v0.2.

Covers:
- Category 1: Intent Immutability & Canonical Fingerprint (Invariants I1, I4, I5)
- Category 2: Canonical Paths (Finding F3, Invariants I5, I6)
- Category 3: Capability Model & Invocation Classifier (Finding F4, Invariants I9, I10)
- Category 4: Approval Contract & Single-Use (Invariants I1, I7, I8)
- Category 5: Workspace Root Enforcement (Finding F2, Invariant I6)
- Category 6: Backend Authority (Finding F5, Invariants I2, I3)
- Category 7: End-to-end Pipeline Invariants
"""

import dataclasses
import os
import sys
import pathlib
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import ExecutionAuthorizationError, LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability, classify_invocation
from app.execution.identity import CommandIdentity, command_identity
from app.execution.intent import (
    AuthorizedExecution,
    ExecutionIntent,
    IntentBody,
    IntentBuilder,
)
from app.execution.paths import (
    PathSecurityError,
    canonical_executable,
    is_bare_executable,
    normalize_workspace_relative_parts,
    normalize_workspace_relative_path,
)
from app.execution.policy import ExecutionPolicy
from app.execution.profile import ProjectExecutionProfile
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalState,
    InMemoryApprovalResolver,
)


class _MockBackend:
    def __init__(self):
        self.call_count = 0
        self.calls = []

    def execute(self, authorized_execution):
        self.call_count += 1
        self.calls.append(authorized_execution)
        req_id = getattr(authorized_execution, "request_id", "req-test")
        return ExecutionResult(
            request_id=req_id,
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="mock output",
            stderr="",
            duration_seconds=0.01,
        )



def _scope_for(req, run_id, declared, command, *, source_root):
    """Freeze one Run's scope from the request's own declaration.

    Everything here is derived from what the test itself declares: the run id,
    the declared commands, and the request profile. The scope profile permits
    the executables of the request profile, of the declared commands, and of the
    request command, so the coordinator's own command validation reaches the
    authorization layer the test targets instead of stopping at the perimeter.
    """
    anchor = source_root
    try:
        anchor = pathlib.Path(anchor)
        if not anchor.is_absolute() or not anchor.exists() or not anchor.is_dir():
            anchor = pathlib.Path(tempfile.gettempdir())
    except Exception:
        anchor = pathlib.Path(tempfile.gettempdir())

    executables = list(req.profile.allowed_commands)
    executables.extend(declared)
    entries = set(declared)
    if command:
        executables.append(command[0])
        entries.add(command[0])

    profile = req.profile
    scope = RunScope(
        run_id=run_id,
        workspace=Workspace(anchor),
        execution_profile=ProjectExecutionProfile(
            profile.profile_id or "v02-scope-profile",
            environment_type=profile.environment_type,
            runtime_name=profile.runtime_name,
            runtime_version=profile.runtime_version,
            target_os=profile.target_os,
            working_directory=profile.working_directory,
            environment_variables=dict(profile.environment_variables),
            timeout_seconds=profile.timeout_seconds,
            max_output_bytes=profile.max_output_bytes,
            network_access=profile.network_access,
            capabilities=profile.capabilities,
            allowed_commands=tuple(executables),
        ),
        allowed_execution_commands=frozenset(entries),
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="v02-scope-anchor", description="run scope anchor"),
        ),
    )
    scope.freeze()
    return scope


class Category1IntentImmutabilityTests(unittest.TestCase):
    """Category 1: Intent Immutability & Canonical Fingerprint."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        RunScope.release_all()
        self.temp_dir.cleanup()

    def test_intent_fields_are_frozen(self):
        intent = ExecutionIntent(
            executable="python",
            argv=("test.py",),
            working_directory=".",
            environment_variables=(("KEY", "VALUE"),),
            timeout_seconds=30.0,
            max_output_bytes=100000,
            artifact_targets=(),
            profile_id="default",
            network_access=False,
            capabilities=frozenset(),
        )

        with self.assertRaises(dataclasses.FrozenInstanceError):
            intent.executable = "modified"  # type: ignore

        with self.assertRaises(dataclasses.FrozenInstanceError):
            intent.working_directory = "other"  # type: ignore

    def test_intent_fingerprint_stability(self):
        intent1 = ExecutionIntent(
            executable="python",
            argv=("-m", "unittest"),
            working_directory="tests",
            environment_variables=(("ENV_A", "1"), ("ENV_B", "2")),
            timeout_seconds=15.0,
            max_output_bytes=50000,
            artifact_targets=("out.txt",),
            profile_id="prof1",
            network_access=False,
            capabilities=frozenset(),
        )
        intent2 = ExecutionIntent(
            executable="python",
            argv=("-m", "unittest"),
            working_directory="tests",
            environment_variables=(("ENV_A", "1"), ("ENV_B", "2")),
            timeout_seconds=15.0,
            max_output_bytes=50000,
            artifact_targets=("out.txt",),
            profile_id="prof1",
            network_access=False,
            capabilities=frozenset(),
        )

        self.assertEqual(intent1.fingerprint, intent2.fingerprint)
        self.assertEqual(len(intent1.fingerprint), 64)

    def test_intent_fingerprint_changes_on_any_body_field_tampering(self):
        base_kwargs = dict(
            executable="python",
            argv=("main.py",),
            working_directory=".",
            environment_variables=(("A", "1"),),
            timeout_seconds=10.0,
            max_output_bytes=1024,
            artifact_targets=("a.txt",),
            profile_id="prof",
            network_access=False,
            capabilities=frozenset(),
        )
        base_intent = ExecutionIntent(**base_kwargs)
        base_fp = base_intent.fingerprint

        field_mutations = [
            ("executable", "python3"),
            ("argv", ("other.py",)),
            ("working_directory", "subdir"),
            ("environment_variables", (("A", "2"),)),
            ("timeout_seconds", 20.0),
            ("max_output_bytes", 2048),
            ("artifact_targets", ("a.txt", "extra.txt")),
            ("profile_id", "prof2"),
            ("network_access", True),
            ("capabilities", frozenset([ExecutionCapability.INTERPRET_TEXT])),
        ]

        for field_name, new_val in field_mutations:
            with self.subTest(field=field_name):
                mutated_kwargs = dict(base_kwargs)
                mutated_kwargs[field_name] = new_val
                mutated_intent = ExecutionIntent(**mutated_kwargs)
                self.assertNotEqual(
                    base_fp,
                    mutated_intent.fingerprint,
                    f"Fingerprint did not change when mutating field: {field_name}",
                )

    def test_approval_for_intent_a_cannot_authorize_intent_b(self):
        profile = ProjectExecutionProfile("test", allowed_commands=("python",))
        req_a = ExecutionRequest(
            command=("python", "script_a.py"),
            profile=profile,
            approval_required=True,
            request_id="req-1",
        )
        req_b = ExecutionRequest(
            command=("python", "script_b.py"),
            profile=profile,
            approval_required=True,
            request_id="req-1",
        )

        intent_a = IntentBuilder.from_request(req_a, profile).build()
        intent_b = IntentBuilder.from_request(req_b, profile).build()
        self.assertNotEqual(intent_a.fingerprint, intent_b.fingerprint)

        resolver = InMemoryApprovalResolver()
        resolver.submit(
            run_id="run-1",
            invocation_id="req-1",
            decision=ApprovalState.APPROVED,
            intent_fingerprint=intent_a.fingerprint,
        )

        # Attempt to authorize Intent B using Intent A's approval
        coord = ExecutionCoordinator(adapter=_MockBackend())
        result_b = coord.execute(
            req_b,
            run_id="run-1",
            allowed_commands=("python",),
            approval_resolver=resolver,
            workspace_root=self.workspace,
            run_scope=_scope_for(
                req_b, "run-1", ("python",), req_b.command, source_root=self.workspace
            ),)
        self.assertEqual(result_b.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)


class Category2CanonicalPathsTests(unittest.TestCase):
    """Category 2: Canonical Paths (Finding F3, Invariants I5, I6)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        RunScope.release_all()
        self.temp_dir.cleanup()

    def test_windows_drive_absolute_path_rejected_outside_workspace(self):
        outside_drive_path = "D:\\Forbidden\\Path" if os.name == "nt" else "/etc/forbidden"
        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path(outside_drive_path)

    def test_traversal_path_rejected(self):
        traversals = [
            "../outside.txt",
            "foo/../../outside.txt",
            ".../weird",
            "subdir/../../../etc/passwd",
        ]
        for trav in traversals:
            with self.subTest(traversal=trav):
                with self.assertRaises(PathSecurityError):
                    normalize_workspace_relative_path(trav)

    def test_device_names_and_ads_rejected(self):
        invalid_patterns = [
            "CON",
            "PRN",
            "AUX",
            "NUL",
            "COM1",
            "LPT1",
            "foo.txt:hidden_stream",
            "subdir/NUL.txt",
        ]
        for pattern in invalid_patterns:
            with self.subTest(pattern=pattern):
                with self.assertRaises(PathSecurityError):
                    normalize_workspace_relative_parts(pattern)

    def test_unc_paths_rejected(self):
        unc = "\\\\server\\share\\evil.bat"
        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_parts(unc)

    def test_canonical_executable_is_consistent(self):
        self.assertEqual(canonical_executable("python"), "python")
        self.assertEqual(canonical_executable("python.exe"), "python.exe")
        self.assertEqual(canonical_executable("pytest"), "pytest")
        self.assertTrue(is_bare_executable("python"))
        self.assertTrue(is_bare_executable("python.exe"))
        self.assertFalse(is_bare_executable("./python"))
        self.assertFalse(is_bare_executable(r"C:\bin\python.exe"))


class Category3CapabilityModelTests(unittest.TestCase):
    """Category 3: Capability Model & Invocation Classifier (Finding F4, Invariants I9, I10)."""

    def test_python_flag_classifications(self):
        caps_c = classify_invocation("python", ("-c", "import sys; print(1)"))
        self.assertIn(ExecutionCapability.INTERPRET_TEXT, caps_c)

        caps_m = classify_invocation("python", ("-m", "unittest"))
        self.assertIn(ExecutionCapability.INTERPRET_MODULE, caps_m)

        caps_combined = classify_invocation("python", ("-Werror", "-O", "-c", "pass"))
        self.assertIn(ExecutionCapability.INTERPRET_TEXT, caps_combined)

    def test_node_flag_classifications(self):
        caps_e = classify_invocation("node", ("-e", "console.log(1)"))
        self.assertIn(ExecutionCapability.INTERPRET_TEXT, caps_e)

        caps_eval = classify_invocation("node", ("--eval", "console.log(1)"))
        self.assertIn(ExecutionCapability.INTERPRET_TEXT, caps_eval)

        caps_p = classify_invocation("node", ("-p", "process.version"))
        self.assertIn(ExecutionCapability.INTERPRET_TEXT, caps_p)

    def test_shell_flag_classifications(self):
        caps_sh = classify_invocation("bash", ("-c", "ls -la"))
        self.assertIn(ExecutionCapability.EXEC_CHILD, caps_sh)

        caps_cmd = classify_invocation("cmd.exe", ("/c", "dir"))
        self.assertIn(ExecutionCapability.EXEC_CHILD, caps_cmd)

    def test_powershell_flag_classifications(self):
        caps_ps = classify_invocation("powershell", ("-Command", "Get-Process"))
        self.assertIn(ExecutionCapability.EXEC_CHILD, caps_ps)

        caps_enc = classify_invocation("pwsh", ("-EncodedCommand", "RwBlAHQA"))
        self.assertIn(ExecutionCapability.EXEC_CHILD, caps_enc)

    def test_denial_when_profile_lacks_capability(self):
        # Profile allows executable "python" as string, but grants NO capabilities
        profile = ProjectExecutionProfile(
            "strict",
            allowed_commands=("python",),
            capabilities=frozenset(),  # Lacks INTERPRET_TEXT
        )
        req = ExecutionRequest(
            command=("python", "-c", "import os; os.system('echo hi')"),
            profile=profile,
        )
        temp_dir = tempfile.TemporaryDirectory()
        try:
            coord = ExecutionCoordinator(adapter=_MockBackend())
            result = coord.execute(
                req,
                allowed_commands=("python",),
                workspace_root=Path(temp_dir.name).resolve(),
            run_id="v02-run-2",
            run_scope=_scope_for(
                req, "v02-run-2", ("python",), req.command, source_root=Path(temp_dir.name).resolve()
            ),)
            self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
            self.assertIn(result.failure_reason, ("argv_not_authorized", "capability_denied"))
        finally:
            temp_dir.cleanup()

    def test_explicit_command_identity_allowed_even_without_generic_capability(self):
        # Invariant I9: Explicit CommandIdentity bypasses general capability denial
        cmd_id = CommandIdentity("python", ("-m", "unittest", "tests/test_foo.py"))
        profile = ProjectExecutionProfile(
            "specific",
            allowed_commands=(cmd_id,),
            capabilities=frozenset(),  # No generic INTERPRET_MODULE
        )
        req = ExecutionRequest(
            command=("python", "-m", "unittest", "tests/test_foo.py"),
            profile=profile,
        )
        temp_dir = tempfile.TemporaryDirectory()
        try:
            coord = ExecutionCoordinator(adapter=_MockBackend())
            result = coord.execute(
                req,
                allowed_commands=(cmd_id,),
                workspace_root=Path(temp_dir.name).resolve(),
            run_id="v02-run-3",
            run_scope=_scope_for(
                req, "v02-run-3", (cmd_id,), req.command, source_root=Path(temp_dir.name).resolve()
            ),)
            self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        finally:
            temp_dir.cleanup()


class Category4ApprovalContractTests(unittest.TestCase):
    """Category 4: Approval Contract & Single-Use (Invariants I1, I7, I8)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        RunScope.release_all()
        self.temp_dir.cleanup()

    def test_approval_resolution_contains_fingerprint_and_id(self):
        resolver = InMemoryApprovalResolver()
        app_id = resolver.submit(
            run_id="run-1",
            invocation_id="inv-1",
            decision=ApprovalState.APPROVED,
            intent_fingerprint="fp-1234",
            approval_id="custom-appr-id",
        )
        self.assertEqual(app_id, "custom-appr-id")

        req = ApprovalRequest(
            run_id="run-1",
            invocation_id="inv-1",
            tool_id="execute",
            reason="testing",
            intent_fingerprint="fp-1234",
        )
        res = resolver.resolve(req)
        self.assertIsNotNone(res)
        self.assertEqual(res.decision, ApprovalState.APPROVED)
        self.assertEqual(res.approved_fingerprint, "fp-1234")
        self.assertEqual(res.approval_id, "custom-appr-id")

    def test_single_use_consumption(self):
        resolver = InMemoryApprovalResolver()
        resolver.submit(
            run_id="run-1",
            invocation_id="inv-1",
            decision=ApprovalState.APPROVED,
            intent_fingerprint="fp-1234",
        )
        req = ApprovalRequest("run-1", "inv-1", "execute", "test", intent_fingerprint="fp-1234")

        # First resolution consumes approval
        first = resolver.resolve(req)
        self.assertIsNotNone(first)
        self.assertEqual(first.decision, ApprovalState.APPROVED)

        # Second resolution returns None (waiting/exhausted)
        second = resolver.resolve(req)
        self.assertIsNone(second)

    def test_fingerprint_mismatch_returns_none(self):
        resolver = InMemoryApprovalResolver()
        resolver.submit(
            run_id="run-1",
            invocation_id="inv-1",
            decision=ApprovalState.APPROVED,
            intent_fingerprint="correct-fingerprint",
        )
        req = ApprovalRequest(
            run_id="run-1",
            invocation_id="inv-1",
            tool_id="execute",
            reason="test",
            intent_fingerprint="attacker-tampered-fingerprint",
        )
        self.assertIsNone(resolver.resolve(req))

    def test_reexecution_prevention_end_to_end(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(
            command=("python", "task.py"),
            profile=profile,
            approval_required=True,
            request_id="req-reuse",
        )
        intent = IntentBuilder.from_request(req, profile).build()
        resolver = InMemoryApprovalResolver()
        resolver.submit(
            run_id="run-reuse",
            invocation_id="req-reuse",
            decision=ApprovalState.APPROVED,
            intent_fingerprint=intent.fingerprint,
        )

        backend = _MockBackend()
        coord = ExecutionCoordinator(adapter=backend)

        # First execution succeeds
        r1 = coord.execute(
            req,
            run_id="run-reuse",
            allowed_commands=("python",),
            approval_resolver=resolver,
            workspace_root=self.workspace,
            run_scope=_scope_for(
                req, "run-reuse", ("python",), req.command, source_root=self.workspace
            ),)
        self.assertEqual(r1.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(backend.call_count, 1)

        # Attempting immediate replay fails because approval is consumed
        r2 = coord.execute(
            req,
            run_id="run-reuse",
            allowed_commands=("python",),
            approval_resolver=resolver,
            workspace_root=self.workspace,
            run_scope=_scope_for(
                req, "run-reuse", ("python",), req.command, source_root=self.workspace
            ),)
        self.assertEqual(r2.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 1)


class Category5WorkspaceRootEnforcementTests(unittest.TestCase):
    """Category 5: Workspace Root Enforcement (Finding F2, Invariant I6)."""

    def test_missing_workspace_root_fails_closed(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(command=("python", "test.py"), profile=profile)
        coord = ExecutionCoordinator(adapter=LocalExecutionAdapter())

        result = coord.execute(
            req,
            allowed_commands=("python",),
            workspace_root=None,
            run_id="v02-run-6",
            run_scope=_scope_for(
                req, "v02-run-6", ("python",), req.command, source_root=None
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertIn("workspace_root", result.failure_reason)

    def test_relative_workspace_root_fails_closed(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(command=("python", "test.py"), profile=profile)
        coord = ExecutionCoordinator(adapter=LocalExecutionAdapter())

        result = coord.execute(
            req,
            allowed_commands=("python",),
            workspace_root=Path("relative/path"),
            run_id="v02-run-7",
            run_scope=_scope_for(
                req, "v02-run-7", ("python",), req.command, source_root=Path("relative/path")
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertIn("workspace_root", result.failure_reason)

    def test_path_dot_workspace_root_fails_closed(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(command=("python", "test.py"), profile=profile)
        coord = ExecutionCoordinator(adapter=LocalExecutionAdapter())

        result = coord.execute(
            req,
            allowed_commands=("python",),
            workspace_root=Path("."),
            run_id="v02-run-8",
            run_scope=_scope_for(
                req, "v02-run-8", ("python",), req.command, source_root=Path(".")
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertIn("workspace_root", result.failure_reason)

    def test_nonexistent_workspace_root_fails_closed(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(command=("python", "test.py"), profile=profile)
        coord = ExecutionCoordinator(adapter=LocalExecutionAdapter())

        non_existent = Path(tempfile.gettempdir()) / "nonexistent_dir_forge_test_12345"
        if non_existent.exists():
            non_existent.rmdir()

        result = coord.execute(
            req,
            allowed_commands=("python",),
            workspace_root=non_existent,
            run_id="v02-run-9",
            run_scope=_scope_for(
                req, "v02-run-9", ("python",), req.command, source_root=non_existent
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertIn("workspace_root", result.failure_reason)


class Category6BackendAuthorityTests(unittest.TestCase):
    """Category 6: Backend Authority (Finding F5, Invariants I2, I3)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        RunScope.release_all()
        self.temp_dir.cleanup()

    def test_direct_call_without_token_raises_error(self):
        adapter = LocalExecutionAdapter()
        fake_req = ExecutionRequest(command=("python", "test.py"))

        with self.assertRaises(ExecutionAuthorizationError):
            adapter.execute(fake_req)

    def test_forged_authorized_execution_rejected(self):
        adapter = LocalExecutionAdapter()
        fake_intent = ExecutionIntent(
            executable="python",
            argv=("test.py",),
            working_directory=".",
            environment_variables=(),
            timeout_seconds=10.0,
            max_output_bytes=1000,
            artifact_targets=(),
            profile_id="p",
            network_access=False,
            capabilities=frozenset(),
        )
        forged = AuthorizedExecution(
            intent=fake_intent,
            workspace_root=self.workspace,
            run_id="fake-run",
            _token="forged-token-value",
        )

        with self.assertRaises(ExecutionAuthorizationError):
            adapter.execute(forged)

    def test_valid_authorized_execution_via_coordinator_accepted(self):
        adapter = LocalExecutionAdapter()
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(command=("python", "-V"), profile=profile)
        coord = ExecutionCoordinator(adapter=adapter)

        result = coord.execute(
            req,
            allowed_commands=("python",),
            workspace_root=self.workspace,
            run_id="v02-run-10",
            run_scope=_scope_for(
                req, "v02-run-10", ("python",), req.command, source_root=self.workspace
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.exit_code, 0)


class Category7PipelineInvariantsTests(unittest.TestCase):
    """Category 7: End-to-end Pipeline Invariants (Invariants I2, I3)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name).resolve()

    def tearDown(self):
        RunScope.release_all()
        self.temp_dir.cleanup()

    def test_permission_denial_short_circuits_before_approval_and_backend(self):
        # Command not permitted in allowed_commands
        profile = ProjectExecutionProfile("p", allowed_commands=("pytest",))
        req = ExecutionRequest(
            command=("python", "test.py"),
            profile=profile,
            approval_required=True,
        )
        resolver = InMemoryApprovalResolver()
        backend = _MockBackend()
        coord = ExecutionCoordinator(adapter=backend)

        result = coord.execute(
            req,
            allowed_commands=("pytest",),
            approval_resolver=resolver,
            workspace_root=self.workspace,
            run_id="v02-run-11",
            run_scope=_scope_for(
                req, "v02-run-11", ("pytest",), req.command, source_root=self.workspace
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)
        # Approval resolver never queried
        self.assertEqual(len(resolver._entries), 0)
        # Backend never called
        self.assertEqual(backend.call_count, 0)

    def test_approval_waiting_short_circuits_before_backend(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(
            command=("python", "test.py"),
            profile=profile,
            approval_required=True,
        )
        backend = _MockBackend()
        # Resolver with no entry submitted
        resolver = InMemoryApprovalResolver()
        coord = ExecutionCoordinator(adapter=backend)

        result = coord.execute(
            req,
            allowed_commands=("python",),
            approval_resolver=resolver,
            workspace_root=self.workspace,
            run_id="v02-run-12",
            run_scope=_scope_for(
                req, "v02-run-12", ("python",), req.command, source_root=self.workspace
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 0)

    def test_approval_rejection_short_circuits_before_backend(self):
        profile = ProjectExecutionProfile("p", allowed_commands=("python",))
        req = ExecutionRequest(
            command=("python", "test.py"),
            profile=profile,
            approval_required=True,
            request_id="req-reject",
        )
        intent = IntentBuilder.from_request(req, profile).build()
        resolver = InMemoryApprovalResolver()
        resolver.submit(
            run_id="run-1",
            invocation_id="req-reject",
            decision=ApprovalState.REJECTED,
            intent_fingerprint=intent.fingerprint,
        )
        backend = _MockBackend()
        coord = ExecutionCoordinator(adapter=backend)

        result = coord.execute(
            req,
            run_id="run-1",
            allowed_commands=("python",),
            approval_resolver=resolver,
            workspace_root=self.workspace,
            run_scope=_scope_for(
                req, "run-1", ("python",), req.command, source_root=self.workspace
            ),)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_REJECTED)
        self.assertEqual(backend.call_count, 0)


class TestAuthorizedExecutionForgery(unittest.TestCase):
    """Regression tests: AuthorizedExecution cannot be forged via dataclasses.replace
    or any other post-creation mutation of the intent field.
    """

    def _make_intent(self, argv=()) -> ExecutionIntent:
        return (
            IntentBuilder()
            .with_command(["python"] + list(argv))
            .with_working_directory(".")
            .build()
        )

    def _make_authorized(self, intent: ExecutionIntent) -> AuthorizedExecution:
        from app.execution import intent as intent_mod  # noqa: PLC0415

        sentinel = intent_mod._COORDINATOR_SENTINEL  # access private sentinel for tests
        workspace = Path(tempfile.mkdtemp())
        return AuthorizedExecution.create(
            intent,
            workspace,
            coordinator_token=sentinel,
        )

    def test_legitimate_authorized_execution_is_valid(self):
        """A legitimately created AuthorizedExecution must pass is_valid()."""
        intent = self._make_intent()
        auth = self._make_authorized(intent)
        self.assertTrue(auth.is_valid())

    def test_replace_with_different_intent_is_invalid(self):
        """dataclasses.replace swapping the intent must fail is_valid() – forgery closed."""
        original_intent = self._make_intent(argv=["-m", "pytest"])
        evil_intent = self._make_intent(argv=["-c", "import os; os.system('whoami')"])

        auth = self._make_authorized(original_intent)
        # Swap intent via dataclasses.replace – this is the forgery vector
        forged = dataclasses.replace(auth, intent=evil_intent)

        self.assertFalse(
            forged.is_valid(),
            "Forged AuthorizedExecution (replaced intent) must NOT be valid",
        )

    def test_replace_with_same_intent_remains_valid(self):
        """dataclasses.replace that keeps the same intent object must stay valid."""
        intent = self._make_intent()
        auth = self._make_authorized(intent)
        same = dataclasses.replace(auth, run_id="different-run-id")
        # The intent was not changed – fingerprint should still match
        self.assertTrue(
            same.is_valid(),
            "AuthorizedExecution with unchanged intent must still be valid after replace",
        )

    def test_default_fingerprint_is_invalid(self):
        """An AuthorizedExecution constructed directly (bypassing create) must fail is_valid()."""
        intent = self._make_intent()
        workspace = Path(tempfile.mkdtemp())
        # Bypass create() – _intent_fingerprint defaults to ""
        direct = AuthorizedExecution(
            intent=intent,
            workspace_root=workspace,
        )
        self.assertFalse(
            direct.is_valid(),
            "Directly constructed AuthorizedExecution (empty fingerprint) must NOT be valid",
        )

    def test_adapter_rejects_forged_authorized_execution(self):
        """LocalExecutionAdapter must refuse a forged AuthorizedExecution.

        The adapter checks is_valid() before any backend call, so no subprocess
        is ever spawned for a forged token.
        """
        original_intent = self._make_intent(argv=["-m", "pytest"])
        evil_intent = self._make_intent(argv=["-m", "unittest"])

        auth = self._make_authorized(original_intent)
        forged = dataclasses.replace(auth, intent=evil_intent)

        # Adapter rejects before reaching the backend; no backend kwarg needed
        adapter = LocalExecutionAdapter()
        with self.assertRaises(ExecutionAuthorizationError):
            adapter.execute(forged)


if __name__ == "__main__":
    unittest.main()
