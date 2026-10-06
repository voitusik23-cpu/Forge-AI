"""Security regressions for the request-authority ceiling.

Invariant under test: REQUEST AUTHORITY MUST NEVER EXCEED FROZEN RUNSCOPE
AUTHORITY. A request may narrow the perimeter of its Run; it may never widen it.
Each test drives the real guard and then asserts the protected side effect did
not happen (no backend dispatch, no child process, no file mutation).
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

from app.execution.authorizer import ExecutionCoordinator
from app.execution.adapter import LocalExecutionAdapter
from app.execution.capabilities import ExecutionCapability
from app.execution.identity import CommandIdentity
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest, ExecutionOutcomeStatus
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalState, InMemoryApprovalResolver
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor, tool_invocation_fingerprint
from app.tools.permissions import ToolExecutionContext
from app.tools.registry import ToolRegistry
from app.tools.write_project_file import WriteProjectFile
from app.tools.workspace import Workspace


class _CountingBackend:
    """Records whether the adapter dispatched. No test may reach it."""

    def __init__(self) -> None:
        self.call_count = 0

    def execute(self, authorized_execution):  # pragma: no cover - must not run
        self.call_count += 1
        raise AssertionError("backend reached: the perimeter should have denied")


def _criterion() -> AcceptanceCriterion:
    return AcceptanceCriterion(criterion_id="ceiling-crit", description="ceiling")


def _scope(workspace: Workspace, *, capabilities=(), environment=None, run_id="ceiling-run"):
    profile = ProjectExecutionProfile(
        "ceiling-profile",
        allowed_commands=(sys.executable,),
        capabilities=frozenset(capabilities),
        environment_variables=dict(environment or {}),
    )
    scope = RunScope(
        run_id=run_id,
        workspace=workspace,
        execution_profile=profile,
        allowed_execution_commands=frozenset({sys.executable}),
        acceptance_criteria=(_criterion(),),
    )
    scope.freeze()
    return scope


class CapabilityCeilingTests(unittest.TestCase):
    """F-02: request capabilities must stay inside the frozen perimeter."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = pathlib.Path(self._tmp.name)
        self.workspace = Workspace(self.workspace_root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _run(self, *, scope_caps, request_caps, program, backend=None):
        scope = _scope(self.workspace, capabilities=scope_caps)
        req_profile = ProjectExecutionProfile(
            "request-profile",
            allowed_commands=(sys.executable,),
            capabilities=frozenset(request_caps),
        )
        request = ExecutionRequest((sys.executable, "-c", program), profile=req_profile)
        adapter = backend if backend is not None else LocalExecutionAdapter(
            workspace_root=self.workspace_root, isolate_workspace=False
        )
        return ExecutionCoordinator(adapter=adapter).execute(
            request,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
            run_id=scope.run_id,
            run_scope=scope,
        )

    def test_request_capability_absent_from_scope_is_denied(self) -> None:
        backend = _CountingBackend()
        result = self._run(
            scope_caps=(),
            request_caps=(ExecutionCapability.INTERPRET_TEXT,),
            program="import sys; sys.stdout.write('PWNED')",
            backend=backend,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(result.metadata.get("denial_reason"), "run_scope_violation")
        self.assertNotIn("PWNED", result.stdout or "")
        self.assertEqual(backend.call_count, 0, "the perimeter must deny before dispatch")

    def test_matching_capability_is_allowed(self) -> None:
        result = self._run(
            scope_caps=(ExecutionCapability.INTERPRET_TEXT,),
            request_caps=(ExecutionCapability.INTERPRET_TEXT,),
            program="import sys; sys.stdout.write('ok')",
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_request_capability_subset_is_allowed(self) -> None:
        result = self._run(
            scope_caps=(ExecutionCapability.INTERPRET_TEXT, ExecutionCapability.EXEC_CHILD),
            request_caps=(ExecutionCapability.INTERPRET_TEXT,),
            program="import sys; sys.stdout.write('subset')",
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)

    def test_request_capability_superset_is_denied(self) -> None:
        result = self._run(
            scope_caps=(ExecutionCapability.INTERPRET_TEXT,),
            request_caps=(ExecutionCapability.INTERPRET_TEXT, ExecutionCapability.INTERPRET_MODULE),
            program="import sys; sys.stdout.write('nope')",
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(result.metadata.get("denial_reason"), "run_scope_violation")

    def test_module_capability_absent_from_scope_is_denied(self) -> None:
        result = self._run(
            scope_caps=(),
            request_caps=(ExecutionCapability.INTERPRET_MODULE,),
            program="import sys; sys.stdout.write('nope')",
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

    def test_scope_validation_rejects_widened_capability_directly(self) -> None:
        """The authoritative RunScope layer itself must refuse the widening."""
        scope = _scope(self.workspace, capabilities=())
        widened = ProjectExecutionProfile(
            "widened",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )
        with self.assertRaises(RunScopeError):
            scope.validate_execution_profile(widened)


class EnvironmentCeilingTests(unittest.TestCase):
    """F-03: environment authority belongs to the frozen perimeter."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace_root = pathlib.Path(self._tmp.name)
        self.workspace = Workspace(self.workspace_root)
        self.safe_env = {"FORGE_CEILING_LABEL": "release-candidate-7"}

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _run(self, *, scope_env, request_env, program, capabilities=()):
        scope = _scope(
            self.workspace,
            capabilities=capabilities,
            environment=scope_env,
        )
        req_profile = ProjectExecutionProfile(
            "request-profile",
            allowed_commands=(sys.executable,),
            capabilities=frozenset(capabilities),
            environment_variables=dict(scope_env),
        )
        request = ExecutionRequest(
            (sys.executable, "-c", program),
            profile=req_profile,
            environment_variables=dict(request_env),
        )
        return ExecutionCoordinator(
            adapter=LocalExecutionAdapter(
                workspace_root=self.workspace_root, isolate_workspace=False
            )
        ).execute(
            request,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
            run_id=scope.run_id,
            run_scope=scope,
        )

    def test_request_adds_key_absent_from_scope_is_denied(self) -> None:
        result = self._run(
            scope_env=self.safe_env,
            request_env={**self.safe_env, "ARBITRARY_NEW_KEY": "value"},
            program="import sys; sys.stdout.write('nope')",
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(result.metadata.get("denial_reason"), "run_scope_violation")

    def test_request_changes_existing_scope_value_is_denied(self) -> None:
        result = self._run(
            scope_env=self.safe_env,
            request_env={"FORGE_CEILING_LABEL": "tampered"},
            program="import sys; sys.stdout.write('nope')",
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

    def test_pythonpath_injection_is_denied_and_payload_never_runs(self) -> None:
        inject_dir = self.workspace_root / "inject"
        inject_dir.mkdir()
        (inject_dir / "sitecustomize.py").write_text(
            "import sys; sys.stderr.write('INJECTED_CODE_RAN\\n')", encoding="utf-8"
        )
        backend = _CountingBackend()
        scope = _scope(
            self.workspace,
            capabilities=(ExecutionCapability.INTERPRET_TEXT,),
            environment=self.safe_env,
        )
        req_profile = ProjectExecutionProfile(
            "request-profile",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            environment_variables=dict(self.safe_env),
        )
        request = ExecutionRequest(
            (sys.executable, "-c", "import sys; sys.stdout.write('benign')"),
            profile=req_profile,
            environment_variables={"PYTHONPATH": str(inject_dir)},
        )
        result = ExecutionCoordinator(adapter=backend).execute(
            request,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
            run_id=scope.run_id,
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertNotIn("INJECTED_CODE_RAN", result.stderr or "")
        self.assertEqual(backend.call_count, 0, "no subprocess may be started")

    def test_protected_environment_keys_are_denied_when_absent_from_scope(self) -> None:
        protected = (
            "PYTHONPATH",
            "PYTHONSTARTUP",
            "LD_PRELOAD",
            "NODE_OPTIONS",
            "BASH_ENV",
            "GIT_DIR",
            "PATH",
        )
        for key in protected:
            with self.subTest(key=key):
                result = self._run(
                    scope_env=self.safe_env,
                    request_env={**self.safe_env, key: "attacker-value"},
                    program="import sys; sys.stdout.write('nope')",
                )
                self.assertEqual(
                    result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED
                )

    def test_scope_authorized_environment_value_survives(self) -> None:
        program = (
            "import os, sys; sys.stdout.write("
            "os.environ.get('FORGE_CEILING_LABEL', 'missing'))"
        )
        result = self._run(
            scope_env=self.safe_env,
            request_env=dict(self.safe_env),
            program=program,
            capabilities=(ExecutionCapability.INTERPRET_TEXT,),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.stdout.strip(), "release-candidate-7")

    def test_request_may_omit_an_authorized_key(self) -> None:
        """Omitting a key is narrowing, not widening: it must not appear at all."""
        program = (
            "import os, sys; sys.stdout.write("
            "os.environ.get('FORGE_CEILING_LABEL', 'absent'))"
        )
        result = self._run(
            scope_env=self.safe_env,
            request_env={},
            program=program,
            capabilities=(ExecutionCapability.INTERPRET_TEXT,),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.stdout.strip(), "absent")


class ToolApprovalPayloadBindingTests(unittest.TestCase):
    """F-05 / NEW-01: approval binds to one payload and uses one type contract."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.workspace = Workspace(self.root)
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        self.resolver = InMemoryApprovalResolver()
        self.executor = ToolExecutor(
            registry,
            approval_policy=ApprovalPolicy(
                approval_required_tools=(WriteProjectFile.TOOL_ID,)
            ),
            approval_resolver=self.resolver,
        )
        self.context = ToolExecutionContext(
            run_id="approval-run",
            context_fingerprint="approval-ctx",
            allowed_tool_ids=frozenset({WriteProjectFile.TOOL_ID}),
            workspace=self.workspace,
        )
        self.invocation_id = "inv-shared"

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _execute(self, tool_input):
        return self.executor.execute(
            ToolInvocation(
                tool_id=WriteProjectFile.TOOL_ID,
                input=tool_input,
                invocation_id=self.invocation_id,
            ),
            context=self.context,
            observer=lambda *args, **kwargs: None,
        )

    def test_approved_payload_executes(self) -> None:
        payload = {"relative_path": "safe.txt", "content": "SAFE"}
        self.resolver.submit(
            run_id="approval-run",
            invocation_id=self.invocation_id,
            decision=ApprovalState.APPROVED,
            intent_fingerprint=tool_invocation_fingerprint(
                WriteProjectFile.TOOL_ID, payload
            ),
        )
        result = self._execute(payload)
        self.assertEqual(result.status.value, "completed")
        self.assertEqual((self.root / "safe.txt").read_text(encoding="utf-8"), "SAFE")

    def test_different_payload_under_same_invocation_id_is_denied(self) -> None:
        approved = {"relative_path": "safe.txt", "content": "SAFE"}
        attacker = {"relative_path": "evil.py", "content": "MALICIOUS"}
        self.resolver.submit(
            run_id="approval-run",
            invocation_id=self.invocation_id,
            decision=ApprovalState.APPROVED,
            intent_fingerprint=tool_invocation_fingerprint(
                WriteProjectFile.TOOL_ID, approved
            ),
        )
        result = self._execute(attacker)
        self.assertNotEqual(result.status.value, "completed")
        self.assertFalse((self.root / "evil.py").exists())

    def test_approval_replay_is_denied(self) -> None:
        payload = {"relative_path": "safe.txt", "content": "SAFE"}
        self.resolver.submit(
            run_id="approval-run",
            invocation_id=self.invocation_id,
            decision=ApprovalState.APPROVED,
            intent_fingerprint=tool_invocation_fingerprint(
                WriteProjectFile.TOOL_ID, payload
            ),
        )
        first = self._execute(payload)
        second = self._execute(payload)
        self.assertEqual(first.status.value, "completed")
        self.assertNotEqual(second.status.value, "completed")

    def test_executor_binds_the_request_fingerprint_to_the_payload(self) -> None:
        """The executor must put the payload fingerprint on the ApprovalRequest.

        Without this the approval boundary degrades to (run_id, invocation_id),
        which lets an approval granted for one payload authorise another. The
        check drives the real executor and inspects the request it emits.
        """
        from app.tools.approval import ApprovalRequest  # noqa: F401  (type anchor)
        captured = []
        real_resolver = self.resolver

        class _CapturingResolver:
            def resolve(self, request):
                captured.append(request)
                return real_resolver.resolve(request)

        payload = {"relative_path": "bound.txt", "content": "BOUND"}
        expected = tool_invocation_fingerprint(WriteProjectFile.TOOL_ID, payload)
        self.executor._approval_resolver = _CapturingResolver()
        self._execute(payload)

        self.assertEqual(len(captured), 1, "the approval request must be raised once")
        emitted = captured[0]
        self.assertIsNotNone(
            emitted.intent_fingerprint,
            "a tool approval request must carry a payload fingerprint",
        )
        self.assertEqual(emitted.intent_fingerprint, expected)

    def test_wildcard_approval_does_not_satisfy_a_bound_request(self) -> None:
        """A fingerprint-less entry is not a wildcard for a bound tool request."""
        payload = {"relative_path": "safe.txt", "content": "SAFE"}
        self.resolver.submit(
            run_id="approval-run",
            invocation_id=self.invocation_id,
            decision=ApprovalState.APPROVED,
            intent_fingerprint=None,
        )
        result = self._execute(payload)
        self.assertNotEqual(result.status.value, "completed")
        self.assertFalse((self.root / "safe.txt").exists())

    def test_real_approval_resolution_drives_the_approved_path(self) -> None:
        """Regression for the production crash: the resolver returns an
        ApprovalResolution, not a bare ApprovalState, and the approved path must
        complete instead of raising AttributeError."""
        payload = {"relative_path": "resolved.txt", "content": "OK"}
        self.resolver.submit(
            run_id="approval-run",
            invocation_id=self.invocation_id,
            decision=ApprovalState.APPROVED,
            intent_fingerprint=tool_invocation_fingerprint(
                WriteProjectFile.TOOL_ID, payload
            ),
        )
        # The executor raises internally if it dereferences the resolution wrongly.
        result = self._execute(payload)
        self.assertEqual(result.status.value, "completed")
        self.assertIsNone(result.error)
        self.assertEqual(
            (self.root / "resolved.txt").read_text(encoding="utf-8"), "OK"
        )

    def test_rejected_resolution_denies_the_tool(self) -> None:
        payload = {"relative_path": "rejected.txt", "content": "NO"}
        self.resolver.submit(
            run_id="approval-run",
            invocation_id=self.invocation_id,
            decision=ApprovalState.REJECTED,
            intent_fingerprint=tool_invocation_fingerprint(
                WriteProjectFile.TOOL_ID, payload
            ),
        )
        result = self._execute(payload)
        self.assertNotEqual(result.status.value, "completed")
        self.assertFalse((self.root / "rejected.txt").exists())


class ToolFingerprintCanonicalisationTests(unittest.TestCase):
    """F-05: the payload fingerprint is deterministic and input-sensitive."""

    def test_key_order_does_not_change_the_fingerprint(self) -> None:
        self.assertEqual(
            tool_invocation_fingerprint("t", {"a": 1, "b": 2}),
            tool_invocation_fingerprint("t", {"b": 2, "a": 1}),
        )

    def test_nested_structures_canonicalise(self) -> None:
        self.assertEqual(
            tool_invocation_fingerprint("t", {"outer": {"x": [1, 2], "y": {"z": True}}}),
            tool_invocation_fingerprint("t", {"outer": {"y": {"z": True}, "x": [1, 2]}}),
        )

    def test_different_payloads_differ(self) -> None:
        self.assertNotEqual(
            tool_invocation_fingerprint("t", {"content": "A"}),
            tool_invocation_fingerprint("t", {"content": "B"}),
        )

    def test_different_tool_ids_differ(self) -> None:
        self.assertNotEqual(
            tool_invocation_fingerprint("a", {"content": "X"}),
            tool_invocation_fingerprint("b", {"content": "X"}),
        )

    def test_scalar_types_are_distinguished(self) -> None:
        self.assertNotEqual(
            tool_invocation_fingerprint("t", {"v": 1}),
            tool_invocation_fingerprint("t", {"v": True}),
        )
        self.assertNotEqual(
            tool_invocation_fingerprint("t", {"v": "1"}),
            tool_invocation_fingerprint("t", {"v": 1}),
        )


if __name__ == "__main__":
    unittest.main()
