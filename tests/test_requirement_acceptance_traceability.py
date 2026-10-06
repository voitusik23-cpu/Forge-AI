"""Tests for Requirement -> AcceptanceCriterion -> Verification -> Acceptance traceability."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.agents.mock_agent import MockAgent
from app.agents.registry import AgentRegistry
from app.artifacts import ChangeSet
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import EventType, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import (
    FailedCriterion,
    FailedRequirement,
    RevisionLoopExecutor,
    RevisionRequest,
    RevisionStatus,
)
from app.orchestrator.run import RunExecutor
from app.tasks.specification import (
    InvalidTaskSpecificationError,
    Requirement,
    SpecificationValidationStatus,
    TaskSpecification,
)
from app.tools.acceptance import (
    AcceptanceCriterion,
    AcceptanceGate,
    AcceptanceResult,
    AcceptanceStatus,
    CriterionResult,
    DetailedAcceptanceReport,
    RequirementEvaluation,
    RequirementStatus,
)
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)
from app.execution.profile import ProjectExecutionProfile
from app.runtime.run_scope import RunScope
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class _AlwaysApproved:
    def resolve(self, request):
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id="req-resolver-id",
        )


class _SequenceAgent:
    name = "fixture"
    provider_name = "fixture"

    def __init__(self, contents: list[str], path: str = "result.txt"):
        self.contents = list(contents)
        self.path = path
        self.calls = 0
        self.revision_inputs = []

    def run(self, task):
        import json
        from app.orchestrator.models import TaskResult

        if task.context.get("forge_tool_results"):
            return TaskResult(task.id, True, output="tool round complete")
        revision = task.context.get("forge_revision")
        if not revision:
            for item in task.context.get("forge_execution_context", {}).get("items", []):
                if item.get("kind") == "task_context":
                    ctx = json.loads(item["content"])
                    revision = ctx.get("forge_revision")
                    break
        if revision:
            self.revision_inputs.append(revision)
        index = revision["attempt_number"] if revision else 0
        content = self.contents[min(index, len(self.contents) - 1)]
        self.calls += 1
        return TaskResult(
            task.id,
            True,
            output="fixture proposal",
            tool_invocations=[ToolInvocation(
                WriteProjectFile.TOOL_ID,
                {"relative_path": self.path, "content": content, "overwrite": True},
                f"fixture-write-{self.calls}",
            )],
        )


class RequirementAcceptanceTraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.gate = AcceptanceGate()
        self.events = []
        self.observer = lambda kind, data: self.events.append((kind, data))
        self.passed_v = VerificationResult("v-pass", VerificationStatus.PASS, "matched")
        self.failed_v = VerificationResult("v-fail", VerificationStatus.FAIL, "hash_mismatch")

    def test_valid_requirement_criterion_mapping_passes_validation(self):
        req1 = Requirement("req-1", "First requirement", required=True)
        req2 = Requirement("req-2", "Second requirement", required=False)
        c1 = AcceptanceCriterion("c-1", "Crit 1", required=True, requirement_id="req-1")
        c2 = AcceptanceCriterion("c-2", "Crit 2", required=False, requirement_id="req-2")

        spec = TaskSpecification(
            task_id="task-1",
            title="Task Title",
            description="Task Desc",
            requirements=(req1, req2),
            acceptance_criteria=(c1, c2),
        )
        validation = spec.validate()
        self.assertTrue(validation.valid)
        self.assertEqual(validation.status, SpecificationValidationStatus.PASS)
        self.assertEqual(validation.errors, ())

    def test_invalid_requirement_id_in_criterion_rejected(self):
        req = Requirement("req-1", "First requirement")
        c_empty = AcceptanceCriterion("c-1", "Crit 1", requirement_id="  ")
        spec = TaskSpecification(
            task_id="task-1",
            title="Task Title",
            description="Task Desc",
            requirements=(req,),
            acceptance_criteria=(c_empty,),
        )
        validation = spec.validate()
        self.assertFalse(validation.valid)
        self.assertIn("acceptance_criterion_requirement_id_required", validation.errors)

    def test_duplicate_criterion_id_rejected(self):
        req = Requirement("req-1", "First requirement")
        c1 = AcceptanceCriterion("c-1", "Crit 1", requirement_id="req-1")
        c2 = AcceptanceCriterion("c-1", "Crit 2 duplicate", requirement_id="req-1")
        spec = TaskSpecification(
            task_id="task-1",
            title="Task Title",
            description="Task Desc",
            requirements=(req,),
            acceptance_criteria=(c1, c2),
        )
        validation = spec.validate()
        self.assertFalse(validation.valid)
        self.assertIn("duplicate_acceptance_criterion_id", validation.errors)

    def test_orphan_criterion_referencing_unknown_requirement_rejected(self):
        req = Requirement("req-1", "First requirement")
        c_orphan = AcceptanceCriterion("c-orphan", "Orphan", requirement_id="unknown-req")
        c_valid = AcceptanceCriterion("c-1", "Valid", requirement_id="req-1")
        spec = TaskSpecification(
            task_id="task-1",
            title="Task Title",
            description="Task Desc",
            requirements=(req,),
            acceptance_criteria=(c_valid, c_orphan),
        )
        validation = spec.validate()
        self.assertFalse(validation.valid)
        self.assertIn("orphan_acceptance_criterion", validation.errors)

    def test_required_requirement_without_criterion_rejected(self):
        req1 = Requirement("req-1", "First required", required=True)
        req2 = Requirement("req-2", "Second required without criteria", required=True)
        c1 = AcceptanceCriterion("c-1", "Crit 1", requirement_id="req-1")
        spec = TaskSpecification(
            task_id="task-1",
            title="Task Title",
            description="Task Desc",
            requirements=(req1, req2),
            acceptance_criteria=(c1,),
        )
        validation = spec.validate()
        self.assertFalse(validation.valid)
        self.assertIn("required_requirement_missing_criteria", validation.errors)

    def test_optional_requirement_without_criterion_is_allowed_and_skipped_in_report(self):
        req_required = Requirement("req-req", "Required requirement", required=True)
        req_optional = Requirement("req-opt", "Optional requirement without criteria", required=False)
        c_req = AcceptanceCriterion("c-req", "Crit required", requirement_id="req-req")

        spec = TaskSpecification(
            task_id="task-1",
            title="Task Title",
            description="Task Desc",
            requirements=(req_required, req_optional),
            acceptance_criteria=(c_req,),
        )
        self.assertTrue(spec.validate().valid)

        result = self.gate.evaluate(
            spec.acceptance_criteria,
            {"c-req": self.passed_v},
            requirements=spec.requirements,
            run_id="run-opt",
            observer=self.observer,
        )
        self.assertEqual(result.status, AcceptanceStatus.PASS)
        report = result.report
        self.assertIsNotNone(report)
        self.assertEqual(len(report.requirement_evaluations), 2)
        opt_eval = next(r for r in report.requirement_evaluations if r.requirement_id == "req-opt")
        self.assertEqual(opt_eval.status, RequirementStatus.SKIPPED)
        self.assertEqual(opt_eval.criterion_results, ())
        self.assertEqual(len(report.skipped_requirements), 1)

    def test_required_requirement_failure_fails_overall_acceptance(self):
        req = Requirement("req-1", "Must work", required=True)
        crit = AcceptanceCriterion("c-1", "Check 1", requirement_id="req-1", required=True)

        result = self.gate.evaluate(
            [crit],
            {"c-1": self.failed_v},
            requirements=[req],
            run_id="run-fail",
            observer=self.observer,
        )
        self.assertEqual(result.status, AcceptanceStatus.FAIL)
        self.assertEqual(result.code, "required_criteria_failed")
        report = result.report
        self.assertIsNotNone(report)
        self.assertEqual(report.overall_status, AcceptanceStatus.FAIL)
        req_eval = report.requirement_evaluations[0]
        self.assertEqual(req_eval.requirement_id, "req-1")
        self.assertEqual(req_eval.status, RequirementStatus.FAIL)
        self.assertIn("c-1:verification_failed", req_eval.failure_reasons)
        self.assertEqual(len(report.failed_requirements), 1)

    def test_optional_requirement_failure_does_not_fail_overall_acceptance(self):
        req_req = Requirement("req-req", "Required requirement", required=True)
        req_opt = Requirement("req-opt", "Optional requirement", required=False)
        c_req = AcceptanceCriterion("c-req", "Required crit", requirement_id="req-req", required=True)
        c_opt = AcceptanceCriterion("c-opt", "Optional crit", requirement_id="req-opt", required=True)

        result = self.gate.evaluate(
            [c_req, c_opt],
            {"c-req": self.passed_v, "c-opt": self.failed_v},
            requirements=[req_req, req_opt],
            run_id="run-opt-fail",
            observer=self.observer,
        )
        self.assertEqual(result.status, AcceptanceStatus.PASS)
        report = result.report
        self.assertIsNotNone(report)
        self.assertEqual(report.overall_status, AcceptanceStatus.PASS)

        opt_eval = next(r for r in report.requirement_evaluations if r.requirement_id == "req-opt")
        self.assertEqual(opt_eval.status, RequirementStatus.FAIL)
        self.assertTrue(len(opt_eval.failure_reasons) > 0)

        req_eval = next(r for r in report.requirement_evaluations if r.requirement_id == "req-req")
        self.assertEqual(req_eval.status, RequirementStatus.PASS)

    def test_detailed_acceptance_report_structure_and_safe_event_metadata(self):
        req = Requirement("req-1", "Build artifact", required=True)
        crit = AcceptanceCriterion("c-1", "File exists", requirement_id="req-1", required=True)

        result = self.gate.evaluate(
            [crit],
            {"c-1": self.passed_v},
            requirements=[req],
            run_id="run-meta",
            observer=self.observer,
        )
        self.assertEqual(result.status, AcceptanceStatus.PASS)
        report = result.report
        self.assertIsNotNone(report)
        self.assertEqual(len(report.passed_requirements), 1)
        self.assertEqual(len(report.failed_requirements), 0)

        event = self.events[-1]
        self.assertEqual(event[0], EventType.ACCEPTANCE_COMPLETED)
        self.assertEqual(event[1]["run_id"], "run-meta")
        self.assertEqual(event[1]["status"], "pass")
        self.assertIn("failed_requirement_ids", event[1])
        self.assertEqual(event[1]["failed_requirement_ids"], [])
        self.assertEqual(event[1]["failed_criterion_ids"], [])

    def test_structured_revision_feedback_carries_requirement_traceability(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = Workspace(root)
            good_content = "accepted_content"
            bad_content = "rejected_content"

            req = Requirement("file-req", "Create result file", required=True)
            crit = AcceptanceCriterion(
                "file-crit", "Content matches digest", required=True, requirement_id="file-req"
            )
            spec = TaskSpecification(
                task_id="traceable-task",
                title="Write traceable file",
                description="Make file exist",
                requirements=(req,),
                acceptance_criteria=(crit,),
            )
            expectations = {
                "file-crit": VerificationExpectation(
                    "result.txt", True, hashlib.sha256(good_content.encode()).hexdigest()
                )
            }

            agent = _SequenceAgent([bad_content, good_content])
            agents = AgentRegistry()
            agents.register(agent)
            tools = ToolRegistry()
            tools.register(WriteProjectFile())
            run_executor = RunExecutor(
                Orchestrator(agents, default_provider="fixture"),
                tool_executor=ToolExecutor(
                    tools, approval_policy=ApprovalPolicy(), approval_resolver=_AlwaysApproved()
                ),
            )
            engineering_executor = EngineeringRunExecutor(
                RevisionLoopExecutor(run_executor, max_revision_attempts=1)
            )

            # This Run declares dispatch authority (a tool), so its single frozen
            # perimeter is declared here. No command authority is granted.
            run_scope = RunScope(
                run_id="traceability-run",
                workspace=workspace,
                execution_profile=ProjectExecutionProfile(
                    "traceability-profile", allowed_commands=("python",)
                ),
                allowed_tool_ids=frozenset({WriteProjectFile.TOOL_ID}),
                allowed_execution_commands=frozenset(),
                acceptance_criteria=(crit,),
            )
            run_scope.freeze()
            request = EngineeringRunRequest(
                workspace=workspace,
                snapshot_paths=("result.txt",),
                verification_expectations=expectations,
                max_revision_attempts=1,
                allowed_tool_ids=(WriteProjectFile.TOOL_ID,),
                task_specification=spec,
                run_scope=run_scope,
            )
            result = engineering_executor.execute(request)

            self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
            self.assertEqual(result.task_id, "traceable-task")
            self.assertIsNotNone(result.detailed_acceptance_report)
            self.assertEqual(result.detailed_acceptance_report.overall_status, AcceptanceStatus.PASS)

            # Check that the revision feedback received by the agent had structured requirement information:
            self.assertEqual(len(agent.revision_inputs), 1)
            rev_input = agent.revision_inputs[0]
            self.assertIn("failed_requirements", rev_input)
            self.assertEqual(len(rev_input["failed_requirements"]), 1)
            failed_r = rev_input["failed_requirements"][0]
            self.assertEqual(failed_r["requirement_id"], "file-req")
            self.assertEqual(failed_r["status"], "fail")
            self.assertEqual(failed_r["failed_criteria"][0]["criterion_id"], "file-crit")
            self.assertEqual(failed_r["failed_criteria"][0]["requirement_id"], "file-req")
            self.assertEqual(failed_r["failed_criteria"][0]["verification_code"], "content_hash_mismatch")

    def test_legacy_criteria_without_requirements_retains_backward_compatibility(self):
        c1 = AcceptanceCriterion("legacy-1", "Legacy desc 1", required=True)
        c2 = AcceptanceCriterion("legacy-2", "Legacy desc 2", required=False)

        result = self.gate.evaluate(
            [c1, c2],
            {"legacy-1": self.passed_v, "legacy-2": self.failed_v},
            run_id="run-legacy",
            observer=self.observer,
        )
        self.assertEqual(result.status, AcceptanceStatus.PASS)
        self.assertEqual(len(result.results), 2)
        report = result.report
        self.assertIsNotNone(report)
        self.assertEqual(report.overall_status, AcceptanceStatus.PASS)


if __name__ == "__main__":
    unittest.main()
