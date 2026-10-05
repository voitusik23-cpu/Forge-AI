"""Offline tests for TestVerificationAdapter, TestFramework, and test execution verification."""

import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.policy import ExecutionPolicy
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
from app.orchestrator.models import EventType
from app.tasks.specification import Requirement
from app.tools.acceptance import AcceptanceCriterion, AcceptanceGate, AcceptanceStatus
from app.tools.approval import ApprovalPolicy, ApprovalRequest, ApprovalState
from app.tools.test_verification import (
    TestFramework,
    TestVerificationAdapter,
    TestVerificationIntent,
)
from app.tools.verification import (
    VerificationEvaluator,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerificationType,
    validate_verification_traceability,
)


class _TrackingApprovalResolver:
    def __init__(self, decision=ApprovalState.APPROVED) -> None:
        self.decision = decision
        self.requests: list[ApprovalRequest] = []

    def resolve(self, request: ApprovalRequest):
        self.requests.append(request)
        return self.decision


class TestTestVerificationAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.adapter = TestVerificationAdapter()
        self.policy = ExecutionPolicy()
        self.exec_adapter = LocalExecutionAdapter(workspace_root=self.workspace_root)
        self.coordinator = ExecutionCoordinator(adapter=self.exec_adapter, policy=self.policy)

        from app.execution.identity import CommandIdentity

        self.ok_cmd = CommandIdentity(sys.executable, ("-c", "import sys; sys.stdout.write('test_ok'); sys.exit(0)"))
        self.fail_cmd = CommandIdentity(sys.executable, ("-c", "import sys; sys.stderr.write('test_fail'); sys.exit(1)"))
        self.profile = ProjectExecutionProfile(
            profile_id="py-test-env",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            target_os=TargetOS.WINDOWS if sys.platform == "win32" else TargetOS.LINUX,
            allowed_commands=(sys.executable, "python", "python.exe", "pytest", self.ok_cmd, self.fail_cmd),
            timeout_seconds=10.0,
        )

        self.req = Requirement(requirement_id="req-unit-tests", description="Test suite passes")
        self.crit = AcceptanceCriterion(
            criterion_id="crit-unit-tests",
            requirement_id="req-unit-tests",
            description="Unit tests exit with code 0",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_creation_of_test_verification_request(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-unit-tests",
            command=("pytest", "-q"),
            verification_id="v-test-1",
            expected_exit_code=0,
            working_directory=".",
            timeout_seconds=30.0,
            requirement_id="req-unit-tests",
            profile=self.profile,
        )
        exec_req = self.adapter.build_execution_request(intent)
        self.assertEqual(exec_req.command, ("pytest", "-q"))
        self.assertEqual(exec_req.working_directory, ".")
        self.assertEqual(exec_req.timeout_seconds, 30.0)
        self.assertEqual(exec_req.profile, self.profile)
        self.assertEqual(exec_req.metadata["verification_id"], "v-test-1")
        self.assertEqual(exec_req.metadata["criterion_id"], "crit-unit-tests")
        self.assertEqual(exec_req.metadata["requirement_id"], "req-unit-tests")

        v_req = self.adapter.build_verification_request(intent, execution_request_id=exec_req.request_id)
        self.assertEqual(v_req.verification_id, "v-test-1")
        self.assertEqual(v_req.criterion_id, "crit-unit-tests")
        self.assertEqual(v_req.verification_type, VerificationType.COMMAND_EXECUTION)
        self.assertEqual(v_req.execution_request_id, exec_req.request_id)
        self.assertEqual(v_req.expected_exit_code, 0)
        self.assertEqual(v_req.metadata["requirement_id"], "req-unit-tests")

    def test_framework_neutral_configuration(self) -> None:
        intent = TestVerificationAdapter.build_generic_intent(
            criterion_id="crit-1",
            command=("custom-runner", "--suite", "all"),
            expected_exit_code=0,
        )
        self.assertEqual(intent.framework, TestFramework.GENERIC)
        self.assertEqual(intent.command, ("custom-runner", "--suite", "all"))
        exec_req = self.adapter.build_execution_request(intent)
        self.assertEqual(exec_req.command, ("custom-runner", "--suite", "all"))
        self.assertEqual(exec_req.metadata["test_framework"], "generic")

    def test_pytest_configuration(self) -> None:
        intent = TestVerificationAdapter.build_pytest_intent(
            criterion_id="crit-pytest",
            test_args=("-m", "unit", "--tb=short"),
            python_executable="pytest",
            timeout_seconds=20.0,
        )
        self.assertEqual(intent.framework, TestFramework.PYTEST)
        self.assertEqual(intent.command, ("pytest", "-m", "unit", "--tb=short"))
        exec_req = self.adapter.build_execution_request(intent)
        self.assertEqual(exec_req.metadata["test_framework"], "pytest")

    def test_unittest_configuration(self) -> None:
        intent = TestVerificationAdapter.build_unittest_intent(
            criterion_id="crit-unittest",
            test_args=("discover", "-s", "tests"),
            python_executable=sys.executable,
        )
        self.assertEqual(intent.framework, TestFramework.UNITTEST)
        self.assertEqual(intent.command, (sys.executable, "-m", "unittest", "discover", "-s", "tests"))
        exec_req = self.adapter.build_execution_request(intent)
        self.assertEqual(exec_req.metadata["test_framework"], "unittest")

    def test_invalid_empty_command(self) -> None:
        intent_empty_tuple = TestVerificationIntent(
            criterion_id="crit-1",
            command=(),
        )
        errors = intent_empty_tuple.validate()
        self.assertIn("command_required", errors)
        with self.assertRaises(ValueError):
            self.adapter.build_execution_request(intent_empty_tuple)

        # Rejection of shell string
        intent_str = TestVerificationIntent(
            criterion_id="crit-1",
            command="pytest -q",  # type: ignore
        )
        errors_str = intent_str.validate()
        self.assertIn("command_must_be_argv_tuple_not_string", errors_str)
        with self.assertRaises(ValueError):
            self.adapter.build_execution_request(intent_str)

    def test_invalid_criterion_id(self) -> None:
        intent_no_crit = TestVerificationIntent(
            criterion_id="",
            command=("pytest",),
        )
        errors = intent_no_crit.validate()
        self.assertIn("criterion_id_required", errors)
        with self.assertRaises(ValueError):
            self.adapter.build_execution_request(intent_no_crit)

    def test_expected_exit_code(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-1",
            command=(sys.executable, "-c", "import sys; sys.exit(42)"),
            expected_exit_code=42,
        )
        exec_res_42 = ExecutionResult(
            request_id="exec-42",
            status=ExecutionStatus.SUCCESS,
            exit_code=42,
        )
        res_pass = self.adapter.evaluate_result(intent, exec_res_42)
        self.assertEqual(res_pass.status, VerificationStatus.PASS)

        exec_res_0 = ExecutionResult(
            request_id="exec-0",
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
        )
        res_fail = self.adapter.evaluate_result(intent, exec_res_0)
        self.assertEqual(res_fail.status, VerificationStatus.FAIL)
        self.assertEqual(res_fail.code, "exit_code_mismatch")

    def test_timeout_propagation(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-1",
            command=(sys.executable, "-c", "pass"),
            timeout_seconds=12.5,
        )
        exec_req = self.adapter.build_execution_request(intent)
        self.assertEqual(exec_req.timeout_seconds, 12.5)

    def test_permission_denial(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-1",
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=self.profile,
        )
        exec_req = self.adapter.build_execution_request(intent)
        # Coordinate execution with a whitelist that does NOT include sys.executable
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({"disallowed_cmd"}),
        )
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.PERMISSION_DENIED)

        v_res = self.adapter.evaluate_result(intent, exec_res)
        self.assertEqual(v_res.status, VerificationStatus.DENIED)
        self.assertEqual(v_res.code, "execution_denied_permission_denied")

    def test_approval_waiting(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-1",
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=self.profile,
        )
        exec_req = self.adapter.build_execution_request(intent)
        policy = ApprovalPolicy(approval_required_tools=(sys.executable,))
        resolver = _TrackingApprovalResolver(decision=ApprovalState.REQUIRED)
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
            approval_policy=policy,
            approval_resolver=resolver,
        )
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING)

        v_res = self.adapter.evaluate_result(intent, exec_res)
        self.assertEqual(v_res.status, VerificationStatus.DENIED)

    def test_policy_denial(self) -> None:
        restricted_profile = ProjectExecutionProfile(
            profile_id="restricted",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name="python",
            allowed_commands=("other_bin",),
        )
        intent = TestVerificationIntent(
            criterion_id="crit-1",
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=restricted_profile,
        )
        exec_req = self.adapter.build_execution_request(intent)
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        self.assertEqual(exec_res.status, ExecutionStatus.DENIED)
        self.assertEqual(exec_res.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)

        v_res = self.adapter.evaluate_result(intent, exec_res)
        self.assertEqual(v_res.status, VerificationStatus.DENIED)
        self.assertIn("policy_denied", v_res.code)

    def test_successful_test_execution_through_coordinator(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-unit-tests",
            command=(sys.executable, "-c", "import sys; sys.stdout.write('test_ok'); sys.exit(0)"),
            profile=self.profile,
            expected_exit_code=0,
            requirement_id="req-unit-tests",
        )
        exec_req = self.adapter.build_execution_request(intent)
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable, self.ok_cmd}),
        )
        self.assertEqual(exec_res.status, ExecutionStatus.SUCCESS)
        self.assertEqual(exec_res.exit_code, 0)
        self.assertEqual(exec_res.stdout, "test_ok")

        v_res = self.adapter.evaluate_result(intent, exec_res)
        self.assertEqual(v_res.status, VerificationStatus.PASS)
        self.assertEqual(v_res.code, "execution_matched")
        self.assertIsNotNone(v_res.evidence)
        self.assertEqual(v_res.evidence.outcome, "SUCCESS")

    def test_failed_test_execution_through_coordinator(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-unit-tests",
            command=(sys.executable, "-c", "import sys; sys.stderr.write('test_fail'); sys.exit(1)"),
            profile=self.profile,
            expected_exit_code=0,
            requirement_id="req-unit-tests",
        )
        exec_req = self.adapter.build_execution_request(intent)
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable, self.fail_cmd}),
        )
        self.assertEqual(exec_res.status, ExecutionStatus.FAILURE)
        self.assertEqual(exec_res.exit_code, 1)

        v_res = self.adapter.evaluate_result(intent, exec_res)
        self.assertEqual(v_res.status, VerificationStatus.FAIL)
        self.assertEqual(v_res.code, "exit_code_mismatch")
        self.assertIsNotNone(v_res.evidence)
        self.assertEqual(v_res.evidence.outcome, "FAILURE")

    def test_traceability_preservation(self) -> None:
        intent = TestVerificationIntent(
            criterion_id="crit-unit-tests",
            command=(sys.executable, "-c", "import sys; sys.exit(0)"),
            profile=self.profile,
            requirement_id="req-unit-tests",
        )
        exec_req = self.adapter.build_execution_request(intent)
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
        )
        v_res = self.adapter.evaluate_result(intent, exec_res)

        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit,),
            verifications=(v_res,),
            execution_results=(exec_res,),
        )
        self.assertTrue(trace.valid)
        self.assertEqual(trace.errors, ())
        self.assertEqual(v_res.criterion_id, "crit-unit-tests")
        self.assertEqual(v_res.execution_result_id, exec_res.request_id)
        self.assertEqual(v_res.verification_id, intent.verification_id)

    def test_verification_evaluator_is_used_not_duplicated(self) -> None:
        self.assertIsInstance(self.adapter.evaluator, VerificationEvaluator)
        intent = TestVerificationIntent(
            criterion_id="crit-1",
            command=("custom-cmd",),
        )
        exec_res = ExecutionResult(
            request_id="exec-custom",
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
        )
        result = self.adapter.evaluate_result(intent, exec_res)
        # Evaluator produced standard code
        self.assertEqual(result.code, "execution_matched")
        self.assertEqual(result.status, VerificationStatus.PASS)

    def test_lifecycle_events_contain_no_stdout_or_stderr(self) -> None:
        events: list[tuple[EventType, dict[str, object]]] = []
        observer = lambda kind, data: events.append((kind, data))

        secret_text = "secret_in_test_output_123"
        intent = TestVerificationIntent(
            criterion_id="crit-unit-tests",
            command=(sys.executable, "-c", f"import sys; sys.stdout.write('{secret_text}'); sys.exit(0)"),
            profile=self.profile,
            requirement_id="req-unit-tests",
        )
        exec_req = self.adapter.build_execution_request(intent)
        exec_res = self.coordinator.execute(
            exec_req,
            workspace_root=self.workspace_root,
            allowed_commands=frozenset({sys.executable}),
            observer=observer,
        )
        v_res = self.adapter.evaluate_result(
            intent,
            exec_res,
            run_id="run-test",
            observer=observer,
        )

        for event_type, data in events:
            data_repr = str(data)
            self.assertNotIn("stdout", data, f"stdout found in event {event_type}")
            self.assertNotIn("stderr", data, f"stderr found in event {event_type}")
            self.assertNotIn(secret_text, data_repr, f"secret leaked in event {event_type}")


if __name__ == "__main__":
    unittest.main()
