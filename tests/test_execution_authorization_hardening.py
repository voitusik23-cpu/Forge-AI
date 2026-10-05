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
    def __init__(self):
        self.call_count = 0
        self.calls = []

    def execute(self, request):
        self.call_count += 1
        self.calls.append(request)
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


    def test_harness_with_real_in_memory_approval_resolver_executes_when_fingerprint_matches(self):
        from app.agent_runtime.harness import AgentHarness
        from app.agent_runtime.models import HarnessRequest, HarnessStatus
        from app.agent_runtime.policy import AgentHarnessPolicy
        from app.tools.approval import InMemoryApprovalResolver
        from app.projects.state import ProjectState, ProjectStateStatus

        req = self.request((sys.executable, "--version"), (sys.executable,))
        coord = ExecutionCoordinator(adapter=_Backend())
        identity = command_identity(req.command)
        fp = coord._intent_fingerprint(req, identity)

        resolver = InMemoryApprovalResolver()
        resolver.submit("run-h-1", req.request_id, ApprovalState.APPROVED, intent_fingerprint=fp)

        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        h_req = HarnessRequest(
            run_id="run-h-1",
            execution_requests=(req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=policy,
            approval_resolver=resolver,
            initial_project_state=ProjectState(run_id="run-h-1", attempt_number=0, status=ProjectStateStatus.INITIAL),
        )
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1), execution_coordinator=coord)
        res = harness.run(h_req)

        self.assertEqual(res.final_state.status, HarnessStatus.LIMIT_REACHED)
        self.assertEqual(len(res.observations), 1)
        self.assertEqual(res.observations[0].result_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS.value)

    def test_harness_with_real_in_memory_approval_resolver_waits_when_approval_missing(self):
        from app.agent_runtime.harness import AgentHarness
        from app.agent_runtime.models import HarnessRequest, HarnessStatus
        from app.agent_runtime.policy import AgentHarnessPolicy
        from app.tools.approval import InMemoryApprovalResolver
        from app.projects.state import ProjectState, ProjectStateStatus

        req = self.request((sys.executable, "--version"), (sys.executable,))
        coord = ExecutionCoordinator(adapter=_Backend())

        resolver = InMemoryApprovalResolver()  # No approval submitted
        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        h_req = HarnessRequest(
            run_id="run-h-2",
            execution_requests=(req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=policy,
            approval_resolver=resolver,
            initial_project_state=ProjectState(run_id="run-h-2", attempt_number=0, status=ProjectStateStatus.INITIAL),
        )
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1), execution_coordinator=coord)
        res = harness.run(h_req)

        self.assertEqual(res.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertTrue(res.final_state.terminal)

    def test_harness_with_real_in_memory_approval_resolver_waits_when_fingerprint_mismatched(self):
        from app.agent_runtime.harness import AgentHarness
        from app.agent_runtime.models import HarnessRequest, HarnessStatus
        from app.agent_runtime.policy import AgentHarnessPolicy
        from app.tools.approval import InMemoryApprovalResolver
        from app.projects.state import ProjectState, ProjectStateStatus

        req = self.request((sys.executable, "--version"), (sys.executable,))
        coord = ExecutionCoordinator(adapter=_Backend())

        resolver = InMemoryApprovalResolver()
        resolver.submit("run-h-3", req.request_id, ApprovalState.APPROVED, intent_fingerprint="wrong_fingerprint_sha256")

        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        h_req = HarnessRequest(
            run_id="run-h-3",
            execution_requests=(req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=policy,
            approval_resolver=resolver,
            initial_project_state=ProjectState(run_id="run-h-3", attempt_number=0, status=ProjectStateStatus.INITIAL),
        )
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1), execution_coordinator=coord)
        res = harness.run(h_req)

        self.assertEqual(res.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertTrue(res.final_state.terminal)

    def test_module_contract_exact_and_string_only_rules(self):
        cmd_unit = CommandIdentity(sys.executable, ("-m", "unittest", "discover", "-s", "tests"))
        cmd_pyt = CommandIdentity(sys.executable, ("-m", "pytest", "tests"))

        # Exact approved python -m unittest ... -> allowed
        req_exact_unit = self.request((sys.executable, "-m", "unittest", "discover", "-s", "tests"), (sys.executable, cmd_unit))
        self.assertTrue(ExecutionPolicy().evaluate(req_exact_unit).allowed)

        # Exact approved python -m pytest ... -> allowed
        req_exact_pyt = self.request((sys.executable, "-m", "pytest", "tests"), (sys.executable, cmd_pyt))
        self.assertTrue(ExecutionPolicy().evaluate(req_exact_pyt).allowed)

        # String-only python does NOT authorize arbitrary or test -m modules
        req_str_unit = self.request((sys.executable, "-m", "unittest", "discover", "-s", "tests"), (sys.executable,))
        dec1 = ExecutionPolicy().evaluate(req_str_unit)
        self.assertFalse(dec1.allowed)
        self.assertEqual(dec1.reason, "argv_not_authorized")

        req_str_pyt = self.request((sys.executable, "-m", "pytest", "tests"), (sys.executable,))
        dec2 = ExecutionPolicy().evaluate(req_str_pyt)
        self.assertFalse(dec2.allowed)
        self.assertEqual(dec2.reason, "argv_not_authorized")

        # String-only python does NOT authorize -c
        req_str_c = self.request((sys.executable, "-c", "print(1)"), (sys.executable,))
        dec3 = ExecutionPolicy().evaluate(req_str_c)
        self.assertFalse(dec3.allowed)
        self.assertEqual(dec3.reason, "argv_not_authorized")

        # Modified argv -> rejected
        req_mod = self.request((sys.executable, "-m", "unittest", "discover", "-s", "tampered"), (sys.executable, cmd_unit))
        dec4 = ExecutionPolicy().evaluate(req_mod)
        self.assertFalse(dec4.allowed)
        self.assertEqual(dec4.reason, "argv_not_authorized")

        # python -c "EVIL" -m unittest -> rejected
        req_evil = self.request((sys.executable, "-c", "import os", "-m", "unittest"), (sys.executable, cmd_unit))
        dec5 = ExecutionPolicy().evaluate(req_evil)
        self.assertFalse(dec5.allowed)
        self.assertEqual(dec5.reason, "argv_not_authorized")

    def test_relative_executable_paths_rejected(self):
        from app.execution.identity import canonical_executable, command_is_allowed

        # 1. Relative executable forms rejected in canonicalization and CommandIdentity
        for rel in ("./x", "../x", ".\\x", "..\\x", "subdir/x", "subdir\\x", ".", ".."):
            with self.assertRaises(ValueError):
                canonical_executable(rel)
            with self.assertRaises(ValueError):
                CommandIdentity(rel, ("arg",))

        # 2. Relative executable in profile allowed list is rejected
        prof = ProjectExecutionProfile("p-rel", allowed_commands=("./x",))
        val = prof.validate()
        self.assertFalse(val.valid)
        self.assertIn("invalid_allowed_command", val.errors)

        # 3. Relative executable in request is rejected fail-closed
        backend = _Backend()
        coord = ExecutionCoordinator(adapter=backend)
        req_rel = ExecutionRequest(("./x",), profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,)))
        res = coord.execute(req_rel, allowed_commands=frozenset([sys.executable]))
        self.assertEqual(res.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)
        self.assertEqual(backend.call_count, 0)

    def test_approval_required_true_fail_closed_regression(self):
        backend = _Backend()
        coord = ExecutionCoordinator(adapter=backend)
        req = ExecutionRequest(
            command=(sys.executable, "--version"),
            profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,)),
            approval_required=True,
        )

        # Case A: approval_policy is empty and does not list the tool, resolver is None
        empty_policy = ApprovalPolicy(approval_required_tools=())
        res_a = coord.execute(req, allowed_commands=frozenset([sys.executable]), approval_policy=empty_policy, approval_resolver=None)
        self.assertEqual(res_a.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 0)

        # Case B: approval_policy is None, resolver is None
        res_b = coord.execute(req, allowed_commands=frozenset([sys.executable]), approval_policy=None, approval_resolver=None)
        self.assertEqual(res_b.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 0)

        # Case C: resolver is present but has no approval for this intent
        from app.tools.approval import InMemoryApprovalResolver
        resolver = InMemoryApprovalResolver()
        res_c = coord.execute(req, allowed_commands=frozenset([sys.executable]), approval_policy=empty_policy, approval_resolver=resolver)
        self.assertEqual(res_c.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 0)

    def test_approval_policy_command_identity_semantics(self):
        # exact executable match
        policy_exact = ApprovalPolicy(approval_required_tools=(sys.executable,))
        self.assertEqual(policy_exact.evaluate(command_identity((sys.executable, "--version"))), ApprovalState.REQUIRED)

        # absolute path mismatch
        policy_mismatch = ApprovalPolicy(approval_required_tools=("/usr/bin/custom_python",))
        self.assertEqual(policy_mismatch.evaluate(command_identity((sys.executable, "--version"))), ApprovalState.NOT_REQUIRED)

        # basename substitution does NOT match
        policy_bare = ApprovalPolicy(approval_required_tools=("python",))
        self.assertEqual(policy_bare.evaluate(command_identity((sys.executable, "--version"))), ApprovalState.NOT_REQUIRED)

        # argv mismatch on CommandIdentity
        cmd_a = CommandIdentity(sys.executable, ("-c", "print('a')"))
        cmd_b = CommandIdentity(sys.executable, ("-c", "print('b')"))
        policy_cmd_a = ApprovalPolicy(approval_required_tools=(cmd_a,))
        self.assertEqual(policy_cmd_a.evaluate(cmd_a), ApprovalState.REQUIRED)
        self.assertEqual(policy_cmd_a.evaluate(cmd_b), ApprovalState.NOT_REQUIRED)

        # approval for command A cannot authorize command B
        from app.tools.approval import InMemoryApprovalResolver
        backend = _Backend()
        coord = ExecutionCoordinator(adapter=backend)
        req_a = ExecutionRequest((sys.executable, "-c", "print('a')"), profile=ProjectExecutionProfile("p", allowed_commands=(cmd_a, cmd_b)))
        req_b = ExecutionRequest((sys.executable, "-c", "print('b')"), profile=ProjectExecutionProfile("p", allowed_commands=(cmd_a, cmd_b)))

        resolver = InMemoryApprovalResolver()
        fp_a = coord._intent_fingerprint(req_a, cmd_a)
        resolver.submit("run-1", req_b.request_id, ApprovalState.APPROVED, intent_fingerprint=fp_a)

        res = coord.execute(
            req_b,
            run_id="run-1",
            allowed_commands=frozenset([cmd_a, cmd_b]),
            approval_policy=ApprovalPolicy(approval_required_tools=(cmd_b,)),
            approval_resolver=resolver,
        )
        self.assertEqual(res.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 0)

    def test_one_resolve_per_execution_and_single_use(self):
        from app.agent_runtime.harness import AgentHarness
        from app.agent_runtime.models import HarnessRequest, HarnessStatus
        from app.agent_runtime.policy import AgentHarnessPolicy
        from app.projects.state import ProjectState, ProjectStateStatus
        from app.tools.approval import InMemoryApprovalResolver

        class _CountingResolver:
            def __init__(self, inner):
                self.inner = inner
                self.call_count = 0
                self.calls = []

            def resolve(self, request):
                self.call_count += 1
                self.calls.append(request)
                return self.inner.resolve(request)

        backend = _Backend()
        coord = ExecutionCoordinator(adapter=backend)
        req = ExecutionRequest((sys.executable, "--version"), profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,)))
        identity = command_identity(req.command)
        fp = coord._intent_fingerprint(req, identity)

        inner = InMemoryApprovalResolver()
        inner.submit("run-single-use", req.request_id, ApprovalState.APPROVED, intent_fingerprint=fp)
        spy_resolver = _CountingResolver(inner)

        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        h_req = HarnessRequest(
            run_id="run-single-use",
            execution_requests=(req,),
            allowed_execution_commands=(sys.executable,),
            approval_policy=policy,
            approval_resolver=spy_resolver,
            initial_project_state=ProjectState(run_id="run-single-use", attempt_number=0, status=ProjectStateStatus.INITIAL),
        )
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1), execution_coordinator=coord)
        res1 = harness.run(h_req)

        # Invariant 1: Harness does NOT call resolve() independently; resolve() called exactly once
        self.assertEqual(spy_resolver.call_count, 1)
        self.assertEqual(backend.call_count, 1)

        # Invariant 2: Approval was consumed once. Second execution with same resolver cannot reuse it
        res2 = coord.execute(req, run_id="run-single-use", allowed_commands=frozenset([sys.executable]), approval_policy=policy, approval_resolver=spy_resolver)
        self.assertEqual(spy_resolver.call_count, 2)
        self.assertEqual(res2.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
        self.assertEqual(backend.call_count, 1)

        # Invariant 3: Approval A cannot be reused for modified command B through Harness path
        inner3 = InMemoryApprovalResolver()
        cmd_a = CommandIdentity(sys.executable, ("-c", "print('a')"))
        cmd_b = CommandIdentity(sys.executable, ("-c", "print('b')"))
        req_a = ExecutionRequest(command=(sys.executable, "-c", "print('a')"), profile=ProjectExecutionProfile("p", allowed_commands=(cmd_a, cmd_b)))
        req_b = ExecutionRequest(command=(sys.executable, "-c", "print('b')"), profile=ProjectExecutionProfile("p", allowed_commands=(cmd_a, cmd_b)))
        fp_a = coord._intent_fingerprint(req_a, cmd_a)
        inner3.submit("run-h-mod", req_b.request_id, ApprovalState.APPROVED, intent_fingerprint=fp_a)
        spy_resolver3 = _CountingResolver(inner3)

        backend3 = _Backend()
        coord3 = ExecutionCoordinator(adapter=backend3)
        h_req3 = HarnessRequest(
            run_id="run-h-mod",
            execution_requests=(req_b,),
            allowed_execution_commands=(cmd_a, cmd_b),
            approval_policy=ApprovalPolicy(approval_required_tools=(cmd_b,)),
            approval_resolver=spy_resolver3,
            initial_project_state=ProjectState(run_id="run-h-mod", attempt_number=0, status=ProjectStateStatus.INITIAL),
        )
        harness3 = AgentHarness(policy=AgentHarnessPolicy(max_actions=1), execution_coordinator=coord3)
        res_mod = harness3.run(h_req3)
        self.assertEqual(res_mod.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(backend3.call_count, 0)

    def test_fail_closed_matrix_across_all_three_paths(self):
        from app.agent_runtime.harness import AgentHarness
        from app.agent_runtime.models import HarnessRequest, HarnessStatus
        from app.agent_runtime.policy import AgentHarnessPolicy
        from app.orchestrator.engineering import EngineeringRunExecutor, EngineeringRunRequest, EngineeringRunStatus
        from app.orchestrator.revision import RevisionLoopExecutor
        from app.orchestrator.run import RunExecutor
        from app.orchestrator.orchestrator import Orchestrator
        from app.agents.registry import AgentRegistry
        from app.tools.registry import ToolRegistry
        from app.tools.executor import ToolExecutor
        from app.orchestrator.models import Task
        from app.projects.state import ProjectState, ProjectStateStatus
        from app.tools.approval import InMemoryApprovalResolver
        from app.tools.acceptance import AcceptanceCriterion


        def make_harness(backend):
            coord = ExecutionCoordinator(adapter=backend)
            return AgentHarness(policy=AgentHarnessPolicy(max_actions=1), execution_coordinator=coord)

        def make_engineering(backend):
            coord = ExecutionCoordinator(adapter=backend)
            agent_reg = AgentRegistry()
            from app.agents.base import Agent
            class _DummyAgent(Agent):
                name = "dummy"
                provider_name = "test"
                def run(self, task):
                    from app.agents.base import TaskResult
                    return TaskResult(task.id, True, output="dummy")
            agent_reg.register(_DummyAgent())
            tool_exec = ToolExecutor(ToolRegistry(), approval_policy=ApprovalPolicy())
            orch = Orchestrator(agent_reg, default_provider="test")
            run_exec = RunExecutor(orch, tool_executor=tool_exec)
            rev_exec = RevisionLoopExecutor(run_exec)
            return EngineeringRunExecutor(rev_exec, execution_coordinator=coord)

        cases = [
            # (name, approval_policy, approval_resolver, approval_required, intent_fp_modifier)
            ("missing_resolver", ApprovalPolicy(approval_required_tools=(sys.executable,)), None, False, None),
            ("missing_policy_with_approval_required", None, None, True, None),
            ("missing_approval", ApprovalPolicy(approval_required_tools=(sys.executable,)), InMemoryApprovalResolver(), False, None),
            ("mismatched_approval", ApprovalPolicy(approval_required_tools=(sys.executable,)), InMemoryApprovalResolver(), False, "tampered_fp"),
            ("approval_required_without_approval", ApprovalPolicy(approval_required_tools=()), InMemoryApprovalResolver(), True, None),
        ]

        for name, policy, resolver, app_req, fp_mod in cases:
            with self.subTest(case=name):
                # 1. ExecutionCoordinator path
                backend_c = _Backend()
                coord = ExecutionCoordinator(adapter=backend_c)
                req_c = ExecutionRequest((sys.executable, "--version"), profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,)), approval_required=app_req)
                if resolver is not None and fp_mod is not None:
                    resolver.submit("run-fc", req_c.request_id, ApprovalState.APPROVED, intent_fingerprint="other_fp")
                res_c = coord.execute(req_c, run_id="run-fc", allowed_commands=frozenset([sys.executable]), approval_policy=policy, approval_resolver=resolver)
                self.assertEqual(res_c.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)
                self.assertEqual(backend_c.call_count, 0)

                # 2. AgentHarness path
                backend_h = _Backend()
                harness = make_harness(backend_h)
                req_h = ExecutionRequest((sys.executable, "--version"), profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,)), approval_required=app_req)
                res_res_h = InMemoryApprovalResolver() if resolver is not None else None
                if res_res_h is not None and fp_mod is not None:
                    res_res_h.submit("run-h-fc", req_h.request_id, ApprovalState.APPROVED, intent_fingerprint="other_fp")
                h_req = HarnessRequest(
                    run_id="run-h-fc",
                    execution_requests=(req_h,),
                    allowed_execution_commands=(sys.executable,),
                    approval_policy=policy,
                    approval_resolver=res_res_h,
                    initial_project_state=ProjectState(run_id="run-h-fc", attempt_number=0, status=ProjectStateStatus.INITIAL),
                )
                res_h = harness.run(h_req)
                self.assertEqual(res_h.final_state.status, HarnessStatus.WAITING_FOR_APPROVAL)
                self.assertEqual(backend_h.call_count, 0)

                # 3. EngineeringRun path
                backend_e = _Backend()
                eng_exec = make_engineering(backend_e)
                req_e = ExecutionRequest((sys.executable, "--version"), profile=ProjectExecutionProfile("p", allowed_commands=(sys.executable,)), approval_required=app_req)
                res_res_e = InMemoryApprovalResolver() if resolver is not None else None
                if res_res_e is not None and fp_mod is not None:
                    res_res_e.submit("run-e-fc", req_e.request_id, ApprovalState.APPROVED, intent_fingerprint="other_fp")
                import tempfile
                from pathlib import Path
                from app.tools.workspace import Workspace
                ws = Workspace(Path(tempfile.gettempdir()))
                eng_req = EngineeringRunRequest(
                    task=Task(id="task-e-fc", description="test"),
                    agent_name="dummy",
                    acceptance_criteria=(AcceptanceCriterion(criterion_id="c1", requirement_id="r1", description="desc"),),
                    execution_requests=(req_e,),
                    allowed_execution_commands=(sys.executable,),
                    approval_policy=policy,
                    approval_resolver=res_res_e,
                    workspace=ws,
                )
                res_e = eng_exec.execute(eng_req)
                self.assertEqual(res_e.final_status, EngineeringRunStatus.WAITING_FOR_APPROVAL)
                self.assertEqual(backend_e.call_count, 0)


    def test_command_identity_serialization_preserves_semantics(self):
        cmd = CommandIdentity(sys.executable, ("-m", "unittest", "discover"))
        profile = ProjectExecutionProfile("p-ser", allowed_commands=(sys.executable, "python", cmd))
        d = profile.to_dict()
        self.assertIn(sys.executable, d["allowed_commands"])
        self.assertIn("python", d["allowed_commands"])
        # Crucial: Must NOT collapse CommandIdentity into bare string "python" or sys.executable
        serialized_cmd = {"executable": cmd.executable, "argv": list(cmd.argv)}
        self.assertIn(serialized_cmd, d["allowed_commands"])
        self.assertEqual(len(d["allowed_commands"]), 3)
        self.assertIsInstance(d["allowed_commands"][2], dict)

    def test_mandatory_interpreter_and_shell_escape_regression_suite(self):
        backend = _Backend()
        coord = ExecutionCoordinator(adapter=backend)

        # 1. allowed=("python",), python -cCODE => DENIED
        p1 = ProjectExecutionProfile("p1", allowed_commands=("python",))
        r1 = ExecutionRequest(("python", "-cprint(1)"), profile=p1)
        res1 = coord.execute(r1, allowed_commands=("python",))
        self.assertEqual(res1.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 2. allowed=("python",), python -Ic CODE => DENIED
        p2 = ProjectExecutionProfile("p2", allowed_commands=("python",))
        r2 = ExecutionRequest(("python", "-Ic", "print(1)"), profile=p2)
        res2 = coord.execute(r2, allowed_commands=("python",))
        self.assertEqual(res2.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 3. allowed=("python",), python -mtimeit => DENIED
        p3 = ProjectExecutionProfile("p3", allowed_commands=("python",))
        r3 = ExecutionRequest(("python", "-mtimeit"), profile=p3)
        res3 = coord.execute(r3, allowed_commands=("python",))
        self.assertEqual(res3.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 4. allowed=("python",), python --eval=CODE => DENIED
        p4 = ProjectExecutionProfile("p4", allowed_commands=("python",))
        r4 = ExecutionRequest(("python", "--eval=print(1)"), profile=p4)
        res4 = coord.execute(r4, allowed_commands=("python",))
        self.assertEqual(res4.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 5. allowed=("bash",), bash -c CODE => DENIED
        p5 = ProjectExecutionProfile("p5", allowed_commands=("bash",))
        r5 = ExecutionRequest(("bash", "-c", "echo 1"), profile=p5)
        res5 = coord.execute(r5, allowed_commands=("bash",))
        self.assertEqual(res5.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 6. allowed=("powershell",), powershell -Command CODE => DENIED
        p6 = ProjectExecutionProfile("p6", allowed_commands=("powershell",))
        r6 = ExecutionRequest(("powershell", "-Command", "Write-Output 1"), profile=p6)
        res6 = coord.execute(r6, allowed_commands=("powershell",))
        self.assertEqual(res6.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 7. allowed=("cmd",), cmd /c CODE => DENIED
        p7 = ProjectExecutionProfile("p7", allowed_commands=("cmd",))
        r7 = ExecutionRequest(("cmd", "/c", "echo 1"), profile=p7)
        res7 = coord.execute(r7, allowed_commands=("cmd",))
        self.assertEqual(res7.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(backend.call_count, 0)

        # 8. Exact approved CommandIdentity: python + exact argv => ALLOWED
        approved_cmd = CommandIdentity("python", ("-c", "print(1)"))
        p8 = ProjectExecutionProfile("p8", allowed_commands=(approved_cmd,))
        r8 = ExecutionRequest(("python", "-c", "print(1)"), profile=p8)
        res8 = coord.execute(r8, allowed_commands=(approved_cmd,))
        self.assertEqual(res8.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS)
        self.assertEqual(backend.call_count, 1)

        # 9. Same exact executable + modified argv => DENIED
        r9 = ExecutionRequest(("python", "-c", "print(2)"), profile=p8)
        res9 = coord.execute(r9, allowed_commands=(approved_cmd,))
        self.assertEqual(res9.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)
        self.assertEqual(backend.call_count, 1)  # unchanged from case 8


if __name__ == "__main__":
    unittest.main()
