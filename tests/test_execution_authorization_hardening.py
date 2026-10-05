"""Focused Block 1 tests for fail-closed, unified execution authorization."""

import sys
import unittest

from app.execution.authorizer import ExecutionCoordinator
from app.execution.identity import CommandIdentity, command_identity
from app.execution.policy import ExecutionPolicy
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.tools.approval import ApprovalPolicy, ApprovalState


class _Backend:
    def execute(self, request):
        from app.execution.request import ExecutionResult, ExecutionStatus

        return ExecutionResult(
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="ok",
            stderr="",
            duration_seconds=0.0,
        )


class _Resolver:
    def __init__(self, decision=ApprovalState.APPROVED):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.decision


class ExecutionAuthorizationHardeningTests(unittest.TestCase):
    def profile(self, allowed):
        return ProjectExecutionProfile("hardening", allowed_commands=allowed)

    def request(self, command, allowed):
        return ExecutionRequest(tuple(command), profile=self.profile(allowed))

    def test_missing_permission_is_denied(self):
        req = self.request((sys.executable,), (sys.executable,))
        result = ExecutionCoordinator(adapter=_Backend()).execute(req)
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)

    def test_command_identity_is_exact_and_path_substitution_is_denied(self):
        allowed = ("python",)
        for executable in ("./python", "/tmp/evil/python", r"C:\\evil\\python.exe", "python.exe"):
            with self.subTest(executable=executable):
                req = self.request((executable,), allowed)
                self.assertFalse(ExecutionPolicy().evaluate(req).allowed)
                self.assertFalse(
                    ExecutionCoordinator._is_permission_allowed(executable, frozenset(allowed))
                )

    def test_argv_is_part_of_exact_identity(self):
        exact = CommandIdentity("python", ("--version",))
        matching = self.request(("python", "--version"), (exact,))
        different = self.request(("python", "-c", "print(1)"), (exact,))
        self.assertTrue(ExecutionPolicy().evaluate(matching).allowed)
        self.assertFalse(ExecutionPolicy().evaluate(different).allowed)
        self.assertNotEqual(command_identity(matching.command).fingerprint, command_identity(different.command).fingerprint)

    def test_python_eval_without_exact_argv_authorization_is_denied(self):
        req = self.request(("python", "-c", "print(1)"), ("python",))
        self.assertFalse(ExecutionPolicy().evaluate(req).allowed)

    def test_approval_requires_resolver_and_carries_intent_fingerprint(self):
        req = self.request((sys.executable,), (sys.executable,))
        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        waiting = ExecutionCoordinator(adapter=_Backend()).execute(
            req,
            allowed_commands=frozenset({sys.executable}),
            approval_policy=policy,
        )
        self.assertEqual(waiting.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

        resolver = _Resolver()
        approved = ExecutionCoordinator(adapter=_Backend()).execute(
            req,
            allowed_commands=frozenset({sys.executable}),
            approval_policy=policy,
            approval_resolver=resolver,
        )
        self.assertEqual(approved.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(len(resolver.requests), 1)
        self.assertEqual(len(resolver.requests[0].intent_fingerprint), 64)

    def test_missing_required_approval_policy_is_denied(self):
        req = ExecutionRequest(
            (sys.executable,), profile=self.profile((sys.executable,)), approval_required=True
        )
        result = ExecutionCoordinator(adapter=_Backend()).execute(
            req, allowed_commands=frozenset({sys.executable})
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

    def test_approval_intent_changes_when_argv_changes(self):
        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        resolver = _Resolver()
        for argv in (("--version",), ("--help",)):
            req = self.request((sys.executable, *argv), (sys.executable,))
            ExecutionCoordinator(adapter=_Backend()).execute(
                req,
                allowed_commands=frozenset({sys.executable}),
                approval_policy=policy,
                approval_resolver=resolver,
            )
    def test_in_memory_resolver_exact_approved_intent_allows(self):
        from app.tools.approval import InMemoryApprovalResolver

        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        req = self.request((sys.executable, "--version"), (sys.executable,))
        coord = ExecutionCoordinator(adapter=_Backend())
        identity = command_identity(req.command)
        fingerprint = coord._intent_fingerprint(req, identity)

        resolver = InMemoryApprovalResolver()
        resolver.submit("run-1", req.request_id, ApprovalState.APPROVED, intent_fingerprint=fingerprint)

        res = coord.execute(
            req,
            run_id="run-1",
            allowed_commands=frozenset({sys.executable}),
            approval_policy=policy,
            approval_resolver=resolver,
        )
        self.assertEqual(res.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)

    def test_in_memory_resolver_modified_argv_is_waiting_denied(self):
        from app.tools.approval import InMemoryApprovalResolver

        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        req_orig = self.request((sys.executable, "--version"), (sys.executable,))
        coord = ExecutionCoordinator(adapter=_Backend())
        identity_orig = command_identity(req_orig.command)
        fingerprint_orig = coord._intent_fingerprint(req_orig, identity_orig)

        resolver = InMemoryApprovalResolver()
        resolver.submit("run-1", req_orig.request_id, ApprovalState.APPROVED, intent_fingerprint=fingerprint_orig)

        # Invocation ID reused with modified argv (attempted substitution)
        req_modified = ExecutionRequest(
            (sys.executable, "--help"),
            request_id=req_orig.request_id,
            profile=self.profile((sys.executable,)),
        )
        res = coord.execute(
            req_modified,
            run_id="run-1",
            allowed_commands=frozenset({sys.executable}),
            approval_policy=policy,
            approval_resolver=resolver,
        )
        self.assertEqual(res.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

    def test_in_memory_resolver_modified_executable_is_waiting_denied(self):
        from app.tools.approval import InMemoryApprovalResolver

        policy = ApprovalPolicy(approval_required_tools=(sys.executable, "python"))
        req_orig = self.request((sys.executable, "--version"), (sys.executable, "python"))
        coord = ExecutionCoordinator(adapter=_Backend())
        identity_orig = command_identity(req_orig.command)
        fingerprint_orig = coord._intent_fingerprint(req_orig, identity_orig)

        resolver = InMemoryApprovalResolver()
        resolver.submit("run-1", req_orig.request_id, ApprovalState.APPROVED, intent_fingerprint=fingerprint_orig)

        # Invocation ID reused with different executable
        req_modified = ExecutionRequest(
            ("python", "--version"),
            request_id=req_orig.request_id,
            profile=self.profile((sys.executable, "python")),
        )
        res = coord.execute(
            req_modified,
            run_id="run-1",
            allowed_commands=frozenset({sys.executable, "python"}),
            approval_policy=policy,
            approval_resolver=resolver,
        )
        self.assertEqual(res.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

    def test_absolute_interpreter_path_dangerous_argv_without_exact_authorization_denied(self):
        # Absolute interpreter paths with eval flags must also be denied
        for path in (sys.executable, r"C:\Python313\python.exe", "/usr/bin/python3"):
            req = self.request((path, "-c", "print(1)"), (path,))
            dec = ExecutionPolicy().evaluate(req)
            self.assertFalse(dec.allowed)
            self.assertEqual(dec.reason, "argv_not_authorized")


if __name__ == "__main__":
    unittest.main()
