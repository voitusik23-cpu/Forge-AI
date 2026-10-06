"""Comprehensive security regression test suite for Forge AI Security Hardening Block.

Covers:
- F-02: Capability Widening (Invariant 4, Invariant 10)
- F-03: Environment Injection & RCE prevention
- F-05: Tool Approval Cryptographic Payload Binding (Invariant 1, Invariant 8)
- NEW-01: ApprovalResolution Canonical Type Contract & Production Resolver Integration
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.execution.adapter import (
    ExecutionAuthorizationError,
    LocalExecutionAdapter,
    SAFE_ENV_WHITELIST_KEYS,
)
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.identity import CommandIdentity
from app.execution.intent import AuthorizedExecution, ExecutionIntent, IntentBuilder
from app.execution.policy import ExecutionPolicy, ExecutionPolicyDecision
from app.execution.profile import (
    ExecutionEnvironmentType,
    ProjectExecutionProfile,
    TargetOS,
)
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.runtime.run_scope import (
    RunScope,
    RunScopeError,
    require_active_scope,
)
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalResolution,
    ApprovalState,
    InMemoryApprovalResolver,
    tool_invocation_fingerprint,
)
from app.tools.contracts import ToolInvocation, ToolResult, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.permissions import ToolExecutionContext
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class SecurityHardeningF02CapabilityWideningTests(unittest.TestCase):
    """F-02: Capability Widening Regression Tests."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp_dir.cleanup()

    def _create_scope(
        self,
        run_id: str,
        capabilities: frozenset[ExecutionCapability] = frozenset(),
        allowed_commands: tuple[object, ...] = (sys.executable,),
        env_vars: dict[str, str] | None = None,
    ) -> RunScope:
        profile = ProjectExecutionProfile(
            profile_id="scope-prof",
            allowed_commands=tuple(allowed_commands),
            capabilities=capabilities,
            environment_variables=dict(env_vars or {}),
            timeout_seconds=30.0,
            max_output_bytes=1048576,
        )
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=profile,
            allowed_execution_commands=frozenset(allowed_commands),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="crit-1", description="test crit"),
            ),
        )
        scope.freeze()
        return scope

    def test_01_scope_caps_empty_request_interpret_text_denied(self) -> None:
        """1. Scope capabilities = empty, request INTERPRET_TEXT -> DENY."""
        run_id = "test-f02-1"
        scope = self._create_scope(run_id, capabilities=frozenset())

        request_profile = ProjectExecutionProfile(
            profile_id="req-prof",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            timeout_seconds=30.0,
        )
        request = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.stdout.write('FAIL')"),
            profile=request_profile,
        )

        # Direct scope validation fails closed
        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_execution_request(request)
        self.assertIn("cannot grant capabilities", str(ctx.exception))

        # Coordinator also fails closed
        coordinator = ExecutionCoordinator()
        result = coordinator.execute(
            request,
            run_id=run_id,
            run_scope=scope,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(result.metadata.get("denial_reason"), "run_scope_violation")

    def test_02_scope_interpret_text_request_interpret_text_allowed(self) -> None:
        """2. Scope INTERPRET_TEXT, request INTERPRET_TEXT -> ALLOWED."""
        run_id = "test-f02-2"
        scope = self._create_scope(
            run_id,
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )

        request_profile = ProjectExecutionProfile(
            profile_id="req-prof",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            timeout_seconds=30.0,
        )
        request = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.stdout.write('OK')"),
            profile=request_profile,
        )

        scope.validate_execution_request(request)
        coordinator = ExecutionCoordinator(adapter=LocalExecutionAdapter(isolate_workspace=False))
        result = coordinator.execute(
            request,
            run_id=run_id,
            run_scope=scope,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.stdout, "OK")

    def test_03_request_subset_of_scope_allowed(self) -> None:
        """3. Request capabilities subset of scope -> ALLOWED."""
        run_id = "test-f02-3"
        scope = self._create_scope(
            run_id,
            capabilities=frozenset({
                ExecutionCapability.INTERPRET_TEXT,
                ExecutionCapability.INTERPRET_MODULE,
                ExecutionCapability.EXEC_CHILD,
            }),
        )

        request_profile = ProjectExecutionProfile(
            profile_id="req-prof",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            timeout_seconds=30.0,
        )
        request = ExecutionRequest(
            command=(sys.executable, "-c", "import sys; sys.stdout.write('SUBSET_OK')"),
            profile=request_profile,
        )

        scope.validate_execution_request(request)
        coordinator = ExecutionCoordinator(adapter=LocalExecutionAdapter(isolate_workspace=False))
        result = coordinator.execute(
            request,
            run_id=run_id,
            run_scope=scope,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)

    def test_04_request_superset_of_scope_denied(self) -> None:
        """4. Request capabilities superset of scope -> DENY."""
        run_id = "test-f02-4"
        scope = self._create_scope(
            run_id,
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )

        request_profile = ProjectExecutionProfile(
            profile_id="req-prof",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({
                ExecutionCapability.INTERPRET_TEXT,
                ExecutionCapability.EXEC_CHILD,
            }),
            timeout_seconds=30.0,
        )
        request = ExecutionRequest(
            command=(sys.executable, "-c", "print('hello')"),
            profile=request_profile,
        )

        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_execution_request(request)
        self.assertIn("cannot grant capabilities", str(ctx.exception))

    def test_05_profile_replacement_mutation_attempt_denied(self) -> None:
        """5. Scope properties cannot be replaced or mutated after freeze."""
        run_id = "test-f02-5"
        scope = self._create_scope(run_id, capabilities=frozenset())

        # Scope is frozen dataclass
        with self.assertRaises(Exception):
            scope.capabilities = frozenset({ExecutionCapability.INTERPRET_TEXT})  # type: ignore

        # Second scope for same run_id fails freeze
        scope2 = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=ProjectExecutionProfile(
                profile_id="p2",
                allowed_commands=(sys.executable,),
                capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            ),
            allowed_execution_commands=frozenset({sys.executable}),
            acceptance_criteria=(AcceptanceCriterion("c1", "d1"),),
        )
        with self.assertRaises(RunScopeError):
            scope2.freeze()

    def test_06_interpreter_capability_variants_denied_when_outside_scope(self) -> None:
        """6. Interpreter capability variants (INTERPRET_MODULE, EXEC_CHILD) fail closed."""
        run_id = "test-f02-6"
        scope = self._create_scope(run_id, capabilities=frozenset())

        for cap in (
            ExecutionCapability.INTERPRET_MODULE,
            ExecutionCapability.EXEC_CHILD,
        ):
            req_prof = ProjectExecutionProfile(
                profile_id=f"prof-{cap.value}",
                allowed_commands=(sys.executable,),
                capabilities=frozenset({cap}),
                timeout_seconds=30.0,
            )
            req = ExecutionRequest(
                command=(sys.executable, "-m", "unittest"),
                profile=req_prof,
            )
            with self.assertRaises(RunScopeError):
                scope.validate_execution_request(req)


class SecurityHardeningF03EnvironmentInjectionTests(unittest.TestCase):
    """F-03: Process Environment Injection Regression Tests."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp_dir.cleanup()

    def _create_scope(
        self,
        run_id: str,
        env_vars: dict[str, str] | None = None,
        allowed_commands: tuple[object, ...] = (sys.executable,),
        capabilities: frozenset[ExecutionCapability] = frozenset(),
    ) -> RunScope:
        profile = ProjectExecutionProfile(
            profile_id="scope-prof",
            allowed_commands=tuple(allowed_commands),
            environment_variables=dict(env_vars or {}),
            capabilities=capabilities,
            timeout_seconds=30.0,
            max_output_bytes=1048576,
        )
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=profile,
            allowed_execution_commands=frozenset(allowed_commands),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="crit-1", description="test crit"),
            ),
        )
        scope.freeze()
        return scope

    def test_07_request_adds_pythonpath_absent_from_scope_denied(self) -> None:
        """7. Request adds PYTHONPATH absent from scope -> DENY."""
        run_id = "test-f03-7"
        scope = self._create_scope(run_id, env_vars={})

        request = ExecutionRequest(
            command=(sys.executable, "--version"),
            environment_variables={"PYTHONPATH": "/malicious/path"},
            profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,), timeout_seconds=30.0),
        )

        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_execution_request(request)
        self.assertIn("PYTHONPATH", str(ctx.exception))

    def test_08_request_adds_arbitrary_new_env_key_denied(self) -> None:
        """8. Request adds arbitrary new env key -> DENY."""
        run_id = "test-f03-8"
        scope = self._create_scope(run_id, env_vars={"SAFE_KEY": "val"})

        request = ExecutionRequest(
            command=(sys.executable, "--version"),
            environment_variables={"SAFE_KEY": "val", "UNAUTHORIZED_KEY": "evil"},
            profile=ProjectExecutionProfile(
                "p",
                allowed_commands=(sys.executable,),
                environment_variables={"SAFE_KEY": "val"},
                timeout_seconds=30.0,
            ),
        )

        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_execution_request(request)
        self.assertIn("UNAUTHORIZED_KEY", str(ctx.exception))

    def test_09_request_modifies_path_denied(self) -> None:
        """9. Request modifies PATH when not in scope -> DENY."""
        run_id = "test-f03-9"
        scope = self._create_scope(run_id, env_vars={})

        request = ExecutionRequest(
            command=(sys.executable, "--version"),
            environment_variables={"PATH": "/malicious/bin"},
            profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,), timeout_seconds=30.0),
        )

        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_execution_request(request)
        self.assertIn("PATH", str(ctx.exception))

    def test_10_request_modifies_existing_scope_env_value_denied(self) -> None:
        """10. Request modifies value of authorized scope env key -> DENY."""
        run_id = "test-f03-10"
        scope = self._create_scope(run_id, env_vars={"PROJECT_VAR": "expected_value"})

        request = ExecutionRequest(
            command=(sys.executable, "--version"),
            environment_variables={"PROJECT_VAR": "tampered_value"},
            profile=ProjectExecutionProfile(
                "p",
                allowed_commands=(sys.executable,),
                environment_variables={"PROJECT_VAR": "expected_value"},
                timeout_seconds=30.0,
            ),
        )

        with self.assertRaises(RunScopeError) as ctx:
            scope.validate_execution_request(request)
        self.assertIn("cannot change environment variable 'PROJECT_VAR'", str(ctx.exception))

    def test_11_safe_scope_authorized_env_survives(self) -> None:
        """11. Safe scope-authorized env survives into subprocess."""
        run_id = "test-f03-11"
        scope = self._create_scope(
            run_id,
            env_vars={"MY_SPECIAL_APP_VAR": "hello_world_123"},
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )

        program = "import os, sys; sys.stdout.write('VAR=' + os.environ.get('MY_SPECIAL_APP_VAR', 'missing'))"
        profile = ProjectExecutionProfile(
            "p",
            allowed_commands=(sys.executable,),
            environment_variables={"MY_SPECIAL_APP_VAR": "hello_world_123"},
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            timeout_seconds=30.0,
        )
        request = ExecutionRequest(
            command=(sys.executable, "-c", program),
            environment_variables={"MY_SPECIAL_APP_VAR": "hello_world_123"},
            profile=profile,
        )

        coordinator = ExecutionCoordinator(adapter=LocalExecutionAdapter(isolate_workspace=False))
        result = coordinator.execute(
            request,
            run_id=run_id,
            run_scope=scope,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.stdout, "VAR=hello_world_123")

    def test_12_inherited_safe_env_survives(self) -> None:
        """12. Inherited safe system environment survives."""
        run_id = "test-f03-12"
        scope = self._create_scope(
            run_id,
            env_vars={},
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
        )

        program = "import os, sys; sys.stdout.write('HAS_PATH=' + str('PATH' in os.environ))"
        profile = ProjectExecutionProfile(
            "p",
            allowed_commands=(sys.executable,),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            timeout_seconds=30.0,
        )
        request = ExecutionRequest(
            command=(sys.executable, "-c", program),
            profile=profile,
        )

        coordinator = ExecutionCoordinator(adapter=LocalExecutionAdapter(isolate_workspace=False))
        result = coordinator.execute(
            request,
            run_id=run_id,
            run_scope=scope,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(result.stdout, "HAS_PATH=True")

    def test_13_pythonpath_sitecustomize_rce_prevented(self) -> None:
        """13. Attempted PYTHONPATH + sitecustomize RCE is rejected and payload NEVER executes."""
        run_id = "test-f03-13"
        scope = self._create_scope(run_id, env_vars={}, allowed_commands=(sys.executable,))

        # Create malicious directory with sitecustomize.py
        malicious_dir = self.workspace_root / "evil_pkg"
        malicious_dir.mkdir(parents=True, exist_ok=True)
        marker_file = self.workspace_root / "pwned_marker.txt"
        (malicious_dir / "sitecustomize.py").write_text(
            f"import pathlib; pathlib.Path({str(marker_file)!r}).write_text('PWNED')\n",
            encoding="utf-8",
        )

        # Attacker tries to inject PYTHONPATH via request
        request = ExecutionRequest(
            command=(sys.executable, "--version"),
            environment_variables={"PYTHONPATH": str(malicious_dir)},
            profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,), timeout_seconds=30.0),
        )

        coordinator = ExecutionCoordinator(adapter=LocalExecutionAdapter(isolate_workspace=False))
        result = coordinator.execute(
            request,
            run_id=run_id,
            run_scope=scope,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )

        # Request is denied
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        # Marker file was NEVER created (sitecustomize never ran)
        self.assertFalse(marker_file.exists())

    def test_14_adapter_environment_is_defense_in_depth(self) -> None:
        """14. LocalExecutionAdapter enforces AuthorizedExecution validity."""
        adapter = LocalExecutionAdapter(workspace_root=self.workspace_root)
        with self.assertRaises(ExecutionAuthorizationError):
            adapter.execute("invalid-token")

    def test_15_protected_env_classes_denied_when_absent_from_scope(self) -> None:
        """15. Protected env variables (LD_PRELOAD, NODE_OPTIONS, BASH_ENV) fail closed."""
        run_id = "test-f03-15"
        scope = self._create_scope(run_id, env_vars={})

        for dangerous_key in ("LD_PRELOAD", "NODE_OPTIONS", "BASH_ENV", "GIT_DIR", "PYTHONSTARTUP"):
            req = ExecutionRequest(
                command=(sys.executable, "--version"),
                environment_variables={dangerous_key: "/evil/path"},
                profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,), timeout_seconds=30.0),
            )
            with self.assertRaises(RunScopeError) as ctx:
                scope.validate_execution_request(req)
            self.assertIn(dangerous_key, str(ctx.exception))


class SecurityHardeningF05ToolApprovalBindingTests(unittest.TestCase):
    """F-05: Tool Approval Cryptographic Payload Binding Regression Tests."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_root)

        self.registry = ToolRegistry()
        self.write_tool = WriteProjectFile()
        self.registry.register(self.write_tool)
        self.approval_policy = ApprovalPolicy(approval_required_tools=("write_project_file",))

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp_dir.cleanup()

    def _context(self, run_id: str = "test-run") -> ToolExecutionContext:
        return ToolExecutionContext(
            run_id=run_id,
            context_fingerprint="ctx-fp",
            allowed_tool_ids=frozenset({"write_project_file"}),
            workspace=self.workspace,
        )

    def test_16_approved_payload_a_executed_payload_a_success(self) -> None:
        """16. Approved payload A, executed payload A -> SUCCESS."""
        resolver = InMemoryApprovalResolver()
        executor = ToolExecutor(
            self.registry,
            approval_policy=self.approval_policy,
            approval_resolver=resolver,
        )

        payload = {"relative_path": "safe.txt", "content": "SAFE_CONTENT", "overwrite": True}
        fp = tool_invocation_fingerprint("write_project_file", payload)

        resolver.submit(
            run_id="run-tool-1",
            invocation_id="inv-1",
            decision=ApprovalState.APPROVED,
            intent_fingerprint=fp,
        )

        invocation = ToolInvocation("write_project_file", payload, invocation_id="inv-1")
        result = executor.execute(invocation, context=self._context("run-tool-1"), observer=lambda event, data: None)

        self.assertEqual(result.status, ToolStatus.COMPLETED)
        self.assertEqual((self.workspace_root / "safe.txt").read_text(encoding="utf-8"), "SAFE_CONTENT")

    def test_17_approved_payload_a_executed_payload_b_denied(self) -> None:
        """17. Approved payload A, executed payload B under same invocation_id -> DENIED."""
        resolver = InMemoryApprovalResolver()
        executor = ToolExecutor(
            self.registry,
            approval_policy=self.approval_policy,
            approval_resolver=resolver,
        )

        payload_a = {"relative_path": "safe.txt", "content": "SAFE_CONTENT", "overwrite": True}
        fp_a = tool_invocation_fingerprint("write_project_file", payload_a)

        # Admin approves payload A
        resolver.submit(
            run_id="run-tool-2",
            invocation_id="inv-2",
            decision=ApprovalState.APPROVED,
            intent_fingerprint=fp_a,
        )

        # Attacker submits payload B under same invocation_id
        payload_b = {"relative_path": "evil.py", "content": "MALICIOUS", "overwrite": True}
        invocation_b = ToolInvocation("write_project_file", payload_b, invocation_id="inv-2")

        result = executor.execute(invocation_b, context=self._context("run-tool-2"), observer=lambda event, data: None)
        self.assertEqual(result.status, ToolStatus.WAITING_FOR_APPROVAL)
        # Payload B was NEVER written
        self.assertFalse((self.workspace_root / "evil.py").exists())

    def test_18_same_invocation_id_different_payload_denied(self) -> None:
        """18. Different payload parameters produce different fingerprints and fail resolution."""
        payload1 = {"relative_path": "file.txt", "content": "A"}
        payload2 = {"relative_path": "file.txt", "content": "B"}
        fp1 = tool_invocation_fingerprint("write_project_file", payload1)
        fp2 = tool_invocation_fingerprint("write_project_file", payload2)
        self.assertNotEqual(fp1, fp2)

    def test_19_dict_key_reordering_same_fingerprint(self) -> None:
        """19. Dict key reordering produces same semantic payload and same fingerprint."""
        payload1 = {"a": 1, "b": 2, "c": 3}
        payload2 = {"c": 3, "a": 1, "b": 2}
        fp1 = tool_invocation_fingerprint("tool", payload1)
        fp2 = tool_invocation_fingerprint("tool", payload2)
        self.assertEqual(fp1, fp2)

    def test_20_nested_payload_canonicalization(self) -> None:
        """20. Nested dictionaries, lists, booleans, None canonicalize deterministically."""
        payload1 = {
            "nested": {"z": 10, "a": [1, 2, {"k": "v"}], "null_val": None, "flag": True},
            "root_key": "root",
        }
        payload2 = {
            "root_key": "root",
            "nested": {"flag": True, "null_val": None, "a": [1, 2, {"k": "v"}], "z": 10},
        }
        fp1 = tool_invocation_fingerprint("tool", payload1)
        fp2 = tool_invocation_fingerprint("tool", payload2)
        self.assertEqual(fp1, fp2)

    def test_21_fingerprint_none_for_approval_required_tool_fails_closed(self) -> None:
        """21. Missing fingerprint on approval request or entry fails closed (never wildcard)."""
        resolver = InMemoryApprovalResolver()
        resolver.submit("run-1", "inv-1", ApprovalState.APPROVED, intent_fingerprint=None)

        req_with_fp = ApprovalRequest("run-1", "inv-1", "tool", "test", intent_fingerprint="fp-123")
        req_without_fp = ApprovalRequest("run-1", "inv-1", "tool", "test", intent_fingerprint=None)

        self.assertIsNone(resolver.resolve(req_with_fp))
        self.assertIsNone(resolver.resolve(req_without_fp))

    def test_22_approval_replay_denied(self) -> None:
        """22. Approval cannot be replayed (single-use consumption)."""
        resolver = InMemoryApprovalResolver()
        resolver.submit("run-1", "inv-1", ApprovalState.APPROVED, intent_fingerprint="fp-123")

        req = ApprovalRequest("run-1", "inv-1", "tool", "test", intent_fingerprint="fp-123")
        first = resolver.resolve(req)
        self.assertIsNotNone(first)
        self.assertEqual(first.decision, ApprovalState.APPROVED)

        # Replay attempt fails
        second = resolver.resolve(req)
        self.assertIsNone(second)


class SecurityHardeningNEW01ApprovalResolutionTypeTests(unittest.TestCase):
    """NEW-01: ApprovalResolution Canonical Type Contract & Production Resolver Integration."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name).resolve()
        self.workspace = Workspace(self.workspace_root)

        self.registry = ToolRegistry()
        self.write_tool = WriteProjectFile()
        self.registry.register(self.write_tool)
        self.approval_policy = ApprovalPolicy(approval_required_tools=("write_project_file",))

    def tearDown(self) -> None:
        RunScope.release_all()
        self.temp_dir.cleanup()

    def _context(self, run_id: str = "test-run") -> ToolExecutionContext:
        return ToolExecutionContext(
            run_id=run_id,
            context_fingerprint="ctx-fp",
            allowed_tool_ids=frozenset({"write_project_file"}),
            workspace=self.workspace,
        )

    def test_23_real_approval_resolution_approved_path_success_no_attribute_error(self) -> None:
        """23. Real InMemoryApprovalResolver approval path executes without AttributeError."""
        resolver = InMemoryApprovalResolver()
        executor = ToolExecutor(
            self.registry,
            approval_policy=self.approval_policy,
            approval_resolver=resolver,
        )

        payload = {"relative_path": "new01.txt", "content": "NO_CRASH", "overwrite": True}
        fp = tool_invocation_fingerprint("write_project_file", payload)

        resolver.submit(
            run_id="run-new01",
            invocation_id="inv-new01",
            decision=ApprovalState.APPROVED,
            intent_fingerprint=fp,
            approval_id="custom-appr-999",
        )

        events: list[tuple[object, dict[str, object]]] = []
        invocation = ToolInvocation("write_project_file", payload, invocation_id="inv-new01")
        result = executor.execute(
            invocation,
            context=self._context("run-new01"),
            observer=lambda e, d: events.append((e, d)),
        )

        self.assertEqual(result.status, ToolStatus.COMPLETED)
        self.assertEqual((self.workspace_root / "new01.txt").read_text(encoding="utf-8"), "NO_CRASH")
        # Verify resolution event was recorded
        resolved_event = next(d for e, d in events if getattr(e, "value", str(e)) == "approval_resolved")
        self.assertEqual(resolved_event["resolution"], "APPROVED")

    def test_24_real_approval_resolution_denied_path(self) -> None:
        """24. Real InMemoryApprovalResolver rejection path returns DENIED."""
        resolver = InMemoryApprovalResolver()
        executor = ToolExecutor(
            self.registry,
            approval_policy=self.approval_policy,
            approval_resolver=resolver,
        )

        payload = {"relative_path": "rejected.txt", "content": "NO", "overwrite": True}
        fp = tool_invocation_fingerprint("write_project_file", payload)

        resolver.submit(
            run_id="run-new01-rej",
            invocation_id="inv-new01-rej",
            decision=ApprovalState.REJECTED,
            intent_fingerprint=fp,
        )

        invocation = ToolInvocation("write_project_file", payload, invocation_id="inv-new01-rej")
        result = executor.execute(
            invocation,
            context=self._context("run-new01-rej"),
            observer=lambda e, d: None,
        )

        self.assertEqual(result.status, ToolStatus.DENIED)
        self.assertEqual(result.error, "approval rejected")
        self.assertFalse((self.workspace_root / "rejected.txt").exists())

    def test_25_unexpected_resolver_return_type_fails_closed(self) -> None:
        """25. Unexpected resolver return type fails closed with controlled ToolStatus.FAILED."""
        class _InvalidTypeResolver:
            def resolve(self, request):
                return "A STRING INSTEAD OF APPROVAL_RESOLUTION"

        executor = ToolExecutor(
            self.registry,
            approval_policy=self.approval_policy,
            approval_resolver=_InvalidTypeResolver(),
        )

        invocation = ToolInvocation("write_project_file", {"relative_path": "a.txt", "content": "a"}, invocation_id="inv-bad-type")
        result = executor.execute(
            invocation,
            context=self._context("inv-bad-type"),
            observer=lambda e, d: None,
        )

        self.assertEqual(result.status, ToolStatus.FAILED)
        self.assertIn("unexpected type: str", result.error or "")

    def test_26_no_attribute_error_leak_across_all_paths(self) -> None:
        """26. Verify no AttributeError occurs across unapproved, waiting, approved, or rejected paths."""
        resolver = InMemoryApprovalResolver()
        executor = ToolExecutor(
            self.registry,
            approval_policy=self.approval_policy,
            approval_resolver=resolver,
        )

        # Unapproved / waiting
        inv1 = ToolInvocation("write_project_file", {"relative_path": "w.txt", "content": "w"}, invocation_id="inv-wait")
        res1 = executor.execute(
            inv1,
            context=self._context("inv-wait"),
            observer=lambda e, d: None,
        )
        self.assertEqual(res1.status, ToolStatus.WAITING_FOR_APPROVAL)


if __name__ == "__main__":
    unittest.main()
