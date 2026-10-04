"""Offline tests for Verification Contract v0.2, Evidence, Evaluator, and Traceability."""

import unittest
from uuid import uuid4

from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestrator.models import EventType
from app.tasks.specification import Requirement
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceGate,
    AcceptanceStatus,
)
from app.tools.verification import (
    TraceabilityValidationResult,
    VerificationEvaluator,
    VerificationEvidence,
    VerificationRequest,
    VerificationResult,
    VerificationStatus,
    VerificationType,
    validate_verification_traceability,
)


class TestVerificationContract(unittest.TestCase):
    def setUp(self) -> None:
        self.evaluator = VerificationEvaluator()
        self.req = Requirement(requirement_id="req-1", description="Build web service")
        self.crit = AcceptanceCriterion(
            criterion_id="crit-1",
            requirement_id="req-1",
            description="Process exits with 0",
            required=True,
        )

    def test_valid_verification_request(self) -> None:
        req = VerificationRequest(
            verification_id="v-1",
            criterion_id="crit-1",
            verification_type=VerificationType.PROCESS_EXIT,
            execution_request_id="exec-req-1",
            expected_exit_code=0,
            metadata={"priority": "high"},
        )
        errors = req.validate()
        self.assertEqual(errors, ())
        self.assertEqual(req.verification_id, "v-1")
        self.assertEqual(req.criterion_id, "crit-1")
        self.assertEqual(req.expected_exit_code, 0)
        self.assertEqual(req.verification_type, VerificationType.PROCESS_EXIT)

    def test_invalid_verification_request_validation(self) -> None:
        req_empty = VerificationRequest(
            verification_id="",
            criterion_id="",
            expected_exit_code="not_an_int",  # type: ignore
        )
        errors = req_empty.validate()
        self.assertIn("verification_id_required", errors)
        self.assertIn("criterion_id_required", errors)
        self.assertIn("invalid_expected_exit_code", errors)

    def test_unknown_criterion_in_traceability(self) -> None:
        evidence = VerificationEvidence(
            evidence_id="ev-1",
            verification_id="v-1",
            source="execution",
            outcome="SUCCESS",
        )
        v_result = VerificationResult(
            verification_id="v-1",
            status=VerificationStatus.PASS,
            code="execution_matched",
            criterion_id="unknown-crit",
            evidence=evidence,
        )
        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit,),
            verifications=(v_result,),
        )
        self.assertFalse(trace.valid)
        self.assertTrue(any("unknown_criterion:unknown-crit" in err for err in trace.errors))

    def test_duplicate_verification_id_in_traceability(self) -> None:
        evidence1 = VerificationEvidence(
            evidence_id="ev-1",
            verification_id="dup-v-id",
            source="execution",
            outcome="SUCCESS",
        )
        evidence2 = VerificationEvidence(
            evidence_id="ev-2",
            verification_id="dup-v-id",
            source="execution",
            outcome="SUCCESS",
        )
        v1 = VerificationResult(
            verification_id="dup-v-id",
            status=VerificationStatus.PASS,
            code="execution_matched",
            criterion_id="crit-1",
            evidence=evidence1,
        )
        v2 = VerificationResult(
            verification_id="dup-v-id",
            status=VerificationStatus.PASS,
            code="execution_matched",
            criterion_id="crit-1",
            evidence=evidence2,
        )
        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit,),
            verifications=(v1, v2),
        )
        self.assertFalse(trace.valid)
        self.assertIn("duplicate_verification_id:dup-v-id", trace.errors)

    def test_orphan_verification_in_traceability(self) -> None:
        evidence = VerificationEvidence(
            evidence_id="ev-orphan",
            verification_id="v-orphan",
            source="execution",
            outcome="SUCCESS",
        )
        v_orphan = VerificationResult(
            verification_id="v-orphan",
            status=VerificationStatus.PASS,
            code="execution_matched",
            criterion_id=None,  # Not linked to any criterion
            evidence=evidence,
        )
        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit,),
            verifications=(v_orphan,),
        )
        self.assertFalse(trace.valid)
        self.assertIn("orphan_verification:v-orphan", trace.errors)

    def test_missing_evidence_in_traceability(self) -> None:
        v_no_ev = VerificationResult(
            verification_id="v-no-ev",
            status=VerificationStatus.PASS,
            code="some_code",
            criterion_id="crit-1",
            evidence=None,  # Missing evidence
        )
        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit,),
            verifications=(v_no_ev,),
        )
        self.assertFalse(trace.valid)
        self.assertIn("missing_evidence:v-no-ev", trace.errors)

    def test_verification_referencing_wrong_criterion(self) -> None:
        crit2 = AcceptanceCriterion(
            criterion_id="crit-2",
            requirement_id="req-1",
            description="Second criterion",
        )
        evidence = VerificationEvidence(
            evidence_id="ev-1",
            verification_id="v-1",
            source="execution",
            outcome="SUCCESS",
        )
        v_result = VerificationResult(
            verification_id="v-1",
            status=VerificationStatus.PASS,
            code="matched",
            criterion_id="crit-1",
            evidence=evidence,
        )
        # Mapping maps v-1 to crit-2, but v_result says crit-1
        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit, crit2),
            verifications=(v_result,),
            criterion_verification_map={"crit-2": "v-1"},
        )
        self.assertFalse(trace.valid)
        self.assertIn("verification_referencing_wrong_criterion:v-1", trace.errors)

    def test_unknown_requirement_in_traceability(self) -> None:
        bad_crit = AcceptanceCriterion(
            criterion_id="crit-bad",
            requirement_id="nonexistent-req",
            description="Bad criterion",
        )
        evidence = VerificationEvidence(
            evidence_id="ev-1",
            verification_id="v-1",
            source="execution",
            outcome="SUCCESS",
        )
        v_result = VerificationResult(
            verification_id="v-1",
            status=VerificationStatus.PASS,
            code="matched",
            criterion_id="crit-bad",
            evidence=evidence,
        )
        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(bad_crit,),
            verifications=(v_result,),
        )
        self.assertFalse(trace.valid)
        self.assertIn("unknown_requirement:nonexistent-req", trace.errors)

    def test_execution_result_success_to_verification_pass(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-pass-test",
            criterion_id="crit-1",
            execution_request_id="exec-1",
            expected_exit_code=0,
        )
        exec_res = ExecutionResult(
            request_id="exec-1",
            status=ExecutionStatus.SUCCESS,
            outcome_status=ExecutionOutcomeStatus.EXECUTION_SUCCESS,
            exit_code=0,
            stdout="process output",
            duration_seconds=0.15,
        )
        res = self.evaluator.evaluate_execution(v_req, exec_res)
        self.assertEqual(res.status, VerificationStatus.PASS)
        self.assertEqual(res.code, "execution_matched")
        self.assertEqual(res.criterion_id, "crit-1")
        self.assertEqual(res.execution_result_id, "exec-1")
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.outcome, "SUCCESS")
        self.assertEqual(res.evidence.metadata.get("exit_code"), 0)

    def test_execution_result_failure_to_verification_fail(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-fail-test",
            criterion_id="crit-1",
            execution_request_id="exec-2",
            expected_exit_code=0,
        )
        exec_res = ExecutionResult(
            request_id="exec-2",
            status=ExecutionStatus.FAILURE,
            outcome_status=ExecutionOutcomeStatus.EXECUTION_FAILURE,
            exit_code=42,
            stderr="process crashed",
            duration_seconds=0.08,
        )
        res = self.evaluator.evaluate_execution(v_req, exec_res)
        self.assertEqual(res.status, VerificationStatus.FAIL)
        self.assertEqual(res.code, "exit_code_mismatch")
        self.assertEqual(res.criterion_id, "crit-1")
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.outcome, "FAILURE")
        self.assertEqual(res.evidence.metadata.get("exit_code"), 42)

    def test_execution_timeout_semantics(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-timeout-test",
            criterion_id="crit-1",
            execution_request_id="exec-timeout",
            expected_exit_code=0,
        )
        exec_res = ExecutionResult(
            request_id="exec-timeout",
            status=ExecutionStatus.TIMEOUT,
            outcome_status=ExecutionOutcomeStatus.EXECUTION_TIMEOUT,
            exit_code=None,
            duration_seconds=5.01,
        )
        res = self.evaluator.evaluate_execution(v_req, exec_res)
        self.assertEqual(res.status, VerificationStatus.ERROR)
        self.assertEqual(res.code, "execution_timeout")
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.outcome, "TIMEOUT")
        self.assertTrue(res.evidence.metadata.get("timeout"))

    def test_execution_error_semantics(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-err-test",
            criterion_id="crit-1",
            execution_request_id="exec-err",
        )
        exec_res = ExecutionResult(
            request_id="exec-err",
            status=ExecutionStatus.ERROR,
            outcome_status=ExecutionOutcomeStatus.EXECUTION_ERROR,
            exit_code=None,
        )
        res = self.evaluator.evaluate_execution(v_req, exec_res)
        self.assertEqual(res.status, VerificationStatus.ERROR)
        self.assertEqual(res.code, "execution_error")
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.outcome, "ERROR")

    def test_execution_denied_semantics(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-denied-test",
            criterion_id="crit-1",
            execution_request_id="exec-denied",
        )
        exec_res = ExecutionResult(
            request_id="exec-denied",
            status=ExecutionStatus.DENIED,
            outcome_status=ExecutionOutcomeStatus.PERMISSION_DENIED,
            exit_code=None,
        )
        res = self.evaluator.evaluate_execution(v_req, exec_res)
        self.assertEqual(res.status, VerificationStatus.DENIED)
        self.assertEqual(res.code, "execution_denied_permission_denied")
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.outcome, "DENIED")

    def test_missing_execution_result_returns_not_run(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-missing",
            criterion_id="crit-1",
            execution_request_id="exec-missing",
        )
        res = self.evaluator.evaluate_execution(v_req, None)
        self.assertEqual(res.status, VerificationStatus.NOT_RUN)
        self.assertEqual(res.code, "execution_missing")
        self.assertIsNotNone(res.evidence)
        self.assertEqual(res.evidence.outcome, "MISSING")

    def test_traceability_requirement_criterion_verification_execution(self) -> None:
        exec_res = ExecutionResult(
            request_id="exec-42",
            status=ExecutionStatus.SUCCESS,
            outcome_status=ExecutionOutcomeStatus.EXECUTION_SUCCESS,
            exit_code=0,
        )
        v_req = VerificationRequest(
            verification_id="v-trace",
            criterion_id="crit-1",
            execution_request_id="exec-42",
        )
        v_res = self.evaluator.evaluate_execution(v_req, exec_res)

        trace = validate_verification_traceability(
            requirements=(self.req,),
            criteria=(self.crit,),
            verifications=(v_res,),
            execution_results=(exec_res,),
        )
        self.assertTrue(trace.valid)
        self.assertEqual(trace.errors, ())
        self.assertIn("req-1", trace.traceability_map)
        self.assertIn("crit-1", trace.traceability_map["req-1"]["criteria"])
        self.assertIn("v-trace", trace.traceability_map["req-1"]["verifications"])

    def test_acceptance_gate_receives_verification_result_correctly(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-acc",
            criterion_id="crit-1",
            execution_request_id="exec-1",
        )
        exec_res = ExecutionResult(
            request_id="exec-1",
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
        )
        v_res = self.evaluator.evaluate_execution(v_req, exec_res)

        gate = AcceptanceGate()
        events = []
        result = gate.evaluate(
            criteria=(self.crit,),
            verifications={"crit-1": v_res},
            requirements=(self.req,),
            run_id="run-test",
            observer=lambda k, d: events.append((k, d)),
        )
        self.assertEqual(result.status, AcceptanceStatus.PASS)
        self.assertEqual(len(result.results), 1)
        self.assertEqual(result.results[0].status, AcceptanceStatus.PASS)
        self.assertEqual(result.results[0].verification_result, v_res)

    def test_acceptance_gate_fails_on_verification_failure_or_error(self) -> None:
        v_req = VerificationRequest(
            verification_id="v-acc-fail",
            criterion_id="crit-1",
            execution_request_id="exec-fail",
        )
        exec_res = ExecutionResult(
            request_id="exec-fail",
            status=ExecutionStatus.FAILURE,
            exit_code=1,
        )
        v_res = self.evaluator.evaluate_execution(v_req, exec_res)

        gate = AcceptanceGate()
        events = []
        result = gate.evaluate(
            criteria=(self.crit,),
            verifications={"crit-1": v_res},
            requirements=(self.req,),
            run_id="run-test",
            observer=lambda k, d: events.append((k, d)),
        )
        self.assertEqual(result.status, AcceptanceStatus.FAIL)
        self.assertEqual(result.results[0].status, AcceptanceStatus.FAIL)

    def test_lifecycle_events_contain_metadata_only(self) -> None:
        events: list[tuple[EventType, dict[str, object]]] = []
        observer = lambda kind, data: events.append((kind, data))

        v_req = VerificationRequest(
            verification_id="v-event-test",
            criterion_id="crit-1",
            execution_request_id="exec-event",
            expected_exit_code=0,
        )
        secret_content = "super_secret_payload_888"
        exec_res = ExecutionResult(
            request_id="exec-event",
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout=f"Output with {secret_content}",
            stderr="Error stream",
        )

        res = self.evaluator.evaluate_execution(
            v_req,
            exec_res,
            run_id="run-lifecycle",
            observer=observer,
        )

        event_types = [e[0] for e in events]
        self.assertIn(EventType.VERIFICATION_REQUESTED, event_types)
        self.assertIn(EventType.VERIFICATION_COMPLETED, event_types)

        for _, data in events:
            data_str = str(data)
            self.assertNotIn("stdout", data)
            self.assertNotIn("stderr", data)
            self.assertNotIn(secret_content, data_str)
            self.assertNotIn("Output with", data_str)

        # Check completed event payload structure
        comp_event = next(e[1] for e in events if e[0] == EventType.VERIFICATION_COMPLETED)
        self.assertEqual(comp_event.get("verification_id"), "v-event-test")
        self.assertEqual(comp_event.get("criterion_id"), "crit-1")
        self.assertEqual(comp_event.get("execution_result_id"), "exec-event")
        self.assertEqual(comp_event.get("status"), "pass")
        self.assertEqual(comp_event.get("code"), "execution_matched")

    def test_legacy_verification_result_remains_compatible(self) -> None:
        # Legacy positional instantiation: (verification_id, status, code)
        legacy_res = VerificationResult("v-legacy", VerificationStatus.PASS, "matched")
        self.assertEqual(legacy_res.verification_id, "v-legacy")
        self.assertEqual(legacy_res.status, VerificationStatus.PASS)
        self.assertEqual(legacy_res.code, "matched")
        self.assertIsNone(legacy_res.relative_path)
        self.assertIsNone(legacy_res.fingerprint)
        self.assertEqual(legacy_res.metadata, {})
        self.assertIsNone(legacy_res.execution_result_id)
        self.assertIsNone(legacy_res.criterion_id)
        self.assertIsNone(legacy_res.evidence)

        # Legacy file verification with metadata
        file_res = VerificationResult(
            verification_id="v-file",
            status=VerificationStatus.FAIL,
            code="content_hash_mismatch",
            relative_path="file.txt",
            fingerprint="abc",
            metadata={"actual_exists": True},
        )
        self.assertEqual(file_res.relative_path, "file.txt")
        self.assertEqual(file_res.fingerprint, "abc")
        self.assertEqual(file_res.metadata.get("actual_exists"), True)

    def test_evidence_to_dict_serialization(self) -> None:
        ev = VerificationEvidence(
            evidence_id="ev-123",
            verification_id="v-456",
            source="execution",
            execution_result_id="exec-789",
            outcome="SUCCESS",
            metadata={"exit_code": 0, "duration": 1.2},
        )
        d = ev.to_dict()
        self.assertEqual(d["evidence_id"], "ev-123")
        self.assertEqual(d["verification_id"], "v-456")
        self.assertEqual(d["source"], "execution")
        self.assertEqual(d["execution_result_id"], "exec-789")
        self.assertEqual(d["outcome"], "SUCCESS")
        self.assertEqual(d["metadata"]["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
