"""Security and Invariant Regression Suite for Execution Authorization Hardening Block 1.

Covers:
1. Fail-closed capability classification (OPAQUE capability for unclassified executables with argv).
2. Profile environment inclusion in ExecutionIntent fingerprint without secret leakage.
3. Strict approval fingerprint enforcement (missing, empty, mismatched, matching).
4. Preservation of unapproved legitimate flows.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability, classify_invocation
from app.execution.identity import CommandIdentity
from app.execution.intent import ExecutionIntent, IntentBuilder
from app.execution.policy import ExecutionPolicy
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.runtime.run_scope import RunScope
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalResolver,
    ApprovalState,
    InMemoryApprovalResolver,
)


class _MockExecutionBackend:
    def __init__(self) -> None:
        self.call_count = 0
        self.calls: list[object] = []

    def execute(self, authorized_execution: object) -> ExecutionResult:
        self.call_count += 1
        self.calls.append(authorized_execution)
        req_id = getattr(authorized_execution, "request_id", "req-test")
        return ExecutionResult(
            request_id=req_id,
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="mock execution success",
            stderr="",
            duration_seconds=0.01,
        )


class _CustomApprovalResolver:
    def __init__(self, decision: ApprovalState, approved_fingerprint: str = "") -> None:
        self.decision = decision
        self.approved_fingerprint = approved_fingerprint
        self.requests_received: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest) -> ApprovalResolution:
        self.requests_received.append(request)
        return ApprovalResolution(
            decision=self.decision,
            approved_fingerprint=self.approved_fingerprint,
            approval_id="custom-resolver-id",
        )


class ExecutionAuthorizationHardeningBlock1Tests(unittest.TestCase):
    """Verify Block 1 Execution Authorization Hardening invariants."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.backend = _MockExecutionBackend()
        self.coordinator = ExecutionCoordinator(adapter=self.backend)

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp_dir.cleanup()

    # -------------------------------------------------------------------------
    # 1. FAIL-CLOSED CAPABILITY CLASSIFICATION (OPAQUE)
    # -------------------------------------------------------------------------

    def test_01_unclassified_executable_with_argv_requires_opaque(self) -> None:
        caps = classify_invocation("unknown_custom_tool", ("--some-flag", "val"))
        self.assertIn(ExecutionCapability.OPAQUE, caps)

    def test_02_unclassified_executable_without_argv_requires_no_opaque(self) -> None:
        caps = classify_invocation("unknown_custom_tool", ())
        self.assertNotIn(ExecutionCapability.OPAQUE, caps)

    def test_03_standard_tools_do_not_require_opaque(self) -> None:
        for tool in ["pytest", "ruff", "black", "mypy", "git", "make", "echo", "cat"]:
            caps = classify_invocation(tool, ("-v", "arg"))
            self.assertNotIn(ExecutionCapability.OPAQUE, caps)

    def test_04_unclassified_executable_denied_under_bare_allowed_command_without_opaque_capability(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-test",
            allowed_commands=("unknown_custom_tool",),
            capabilities=frozenset(),  # No OPAQUE capability
        )
        policy = ExecutionPolicy()
        intent = (
            IntentBuilder()
            .with_command(("unknown_custom_tool", "--evil-flag", "do_harm"))
            .with_profile_id("p-test")
            .build()
        )
        decision = policy.evaluate(intent, profile)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "argv_not_authorized")

    def test_05_unclassified_executable_allowed_when_pinned_by_command_identity(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-test",
            allowed_commands=(
                CommandIdentity("unknown_custom_tool", ("--safe-flag", "ok")),
            ),
            capabilities=frozenset(),
        )
        policy = ExecutionPolicy()
        intent = (
            IntentBuilder()
            .with_command(("unknown_custom_tool", "--safe-flag", "ok"))
            .with_profile_id("p-test")
            .build()
        )
        decision = policy.evaluate(intent, profile)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "authorized")

    def test_06_unclassified_executable_allowed_when_opaque_capability_explicitly_granted(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-test",
            allowed_commands=("unknown_custom_tool",),
            capabilities=frozenset({ExecutionCapability.OPAQUE}),
        )
        policy = ExecutionPolicy()
        intent = (
            IntentBuilder()
            .with_command(("unknown_custom_tool", "--custom-arg", "123"))
            .with_profile_id("p-test")
            .build()
        )
        decision = policy.evaluate(intent, profile)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "authorized")

    # -------------------------------------------------------------------------
    # 2. PROFILE ENVIRONMENT IN INTENT FINGERPRINT
    # -------------------------------------------------------------------------

    def test_07_profile_environment_included_in_intent_fingerprint(self) -> None:
        prof1 = ProjectExecutionProfile(
            profile_id="p1",
            allowed_commands=(sys.executable,),
            environment_variables={"API_URL": "https://api.v1.local"},
        )
        prof2 = ProjectExecutionProfile(
            profile_id="p1",
            allowed_commands=(sys.executable,),
            environment_variables={"API_URL": "https://api.v2.local"},
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print(1)"),
            environment_variables={},
        )
        intent1 = IntentBuilder.from_request(req, prof1).build()
        intent2 = IntentBuilder.from_request(req, prof2).build()

        self.assertNotEqual(intent1.fingerprint, intent2.fingerprint)
        self.assertEqual(intent1.profile_environment_variables, (("API_URL", "https://api.v1.local"),))
        self.assertEqual(intent2.profile_environment_variables, (("API_URL", "https://api.v2.local"),))

    def test_08_profile_environment_does_not_leak_secrets_in_fingerprint(self) -> None:
        secret_token = "sk-super-secret-auth-token-xyz-123456789"
        prof = ProjectExecutionProfile(
            profile_id="p-sec",
            allowed_commands=(sys.executable,),
            environment_variables={"SECRET_TOKEN": secret_token},
        )
        req = ExecutionRequest(command=(sys.executable, "-c", "print(1)"))
        intent = IntentBuilder.from_request(req, prof).build()

        fp = intent.fingerprint
        self.assertEqual(len(fp), 64)
        self.assertNotIn(secret_token, fp)

    # -------------------------------------------------------------------------
    # 3. EMPTY / MISMATCHED / MATCHING APPROVAL FINGERPRINT
    # -------------------------------------------------------------------------

    def _scope_for(self, req: ExecutionRequest, run_id: str, declared: frozenset[str]) -> RunScope:
        from app.tools.acceptance import AcceptanceCriterion
        from app.tools.workspace import Workspace

        scope = RunScope(
            run_id=run_id,
            workspace=Workspace(self.workspace_root),
            execution_profile=req.profile,
            allowed_execution_commands=declared,
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="crit-1", description="test criterion"),
            ),
        )
        scope.freeze()
        return scope

    def test_09_empty_approved_fingerprint_denied_fail_closed(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-appr",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('needs approval')"),
            profile=profile,
            approval_required=True,
        )
        scope = self._scope_for(req, "run-appr-1", frozenset({sys.executable}))
        resolver = _CustomApprovalResolver(decision=ApprovalState.APPROVED, approved_fingerprint="")
        result = self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            run_id="run-appr-1",
            allowed_commands=frozenset({sys.executable}),
            approval_resolver=resolver,
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("approval fingerprint does not match", result.stderr)
        self.assertEqual(self.backend.call_count, 0)

    def test_10_mismatched_approved_fingerprint_denied_fail_closed(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-appr",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('needs approval')"),
            profile=profile,
            approval_required=True,
        )
        scope = self._scope_for(req, "run-appr-2", frozenset({sys.executable}))
        resolver = _CustomApprovalResolver(
            decision=ApprovalState.APPROVED,
            approved_fingerprint="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )
        result = self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            run_id="run-appr-2",
            allowed_commands=frozenset({sys.executable}),
            approval_resolver=resolver,
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("approval fingerprint does not match", result.stderr)
        self.assertEqual(self.backend.call_count, 0)

    def test_11_matching_approved_fingerprint_allowed(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-appr",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('needs approval')"),
            profile=profile,
            approval_required=True,
        )
        intent = IntentBuilder.from_request(req, profile).build()
        scope = self._scope_for(req, "run-appr-3", frozenset({sys.executable}))

        resolver = InMemoryApprovalResolver()
        resolver.submit(
            run_id="run-appr-3",
            invocation_id=req.request_id,
            decision=ApprovalState.APPROVED,
            intent_fingerprint=intent.fingerprint,
        )

        result = self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            run_id="run-appr-3",
            allowed_commands=frozenset({sys.executable}),
            approval_resolver=resolver,
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.backend.call_count, 1)

    def test_12_no_approval_required_flow_remains_valid(self) -> None:
        profile = ProjectExecutionProfile(
            profile_id="p-no-appr",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )
        req = ExecutionRequest(
            command=(sys.executable, "-c", "print('safe')"),
            profile=profile,
            approval_required=False,
        )
        scope = self._scope_for(req, "run-no-appr", frozenset({sys.executable}))
        result = self.coordinator.execute(
            req,
            workspace_root=self.workspace_root,
            run_id="run-no-appr",
            allowed_commands=frozenset({sys.executable}),
            approval_resolver=None,
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(self.backend.call_count, 1)


if __name__ == "__main__":
    unittest.main()
