"""Deterministic aggregation of task acceptance criteria."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from app.orchestrator.models import EventType
from app.tools.verification import VerificationResult, VerificationStatus


class AcceptanceStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"


class RequirementStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    required: bool = True
    requirement_id: str = ""


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    status: AcceptanceStatus
    code: str
    requirement_id: str = ""
    verification_result: VerificationResult | None = None


@dataclass(frozen=True)
class RequirementEvaluation:
    requirement_id: str
    status: RequirementStatus
    required: bool
    criterion_results: tuple[CriterionResult, ...]
    failure_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DetailedAcceptanceReport:
    overall_status: AcceptanceStatus
    requirement_evaluations: tuple[RequirementEvaluation, ...]
    code: str

    @property
    def passed_requirements(self) -> tuple[RequirementEvaluation, ...]:
        return tuple(r for r in self.requirement_evaluations if r.status == RequirementStatus.PASS)

    @property
    def failed_requirements(self) -> tuple[RequirementEvaluation, ...]:
        return tuple(r for r in self.requirement_evaluations if r.status == RequirementStatus.FAIL)

    @property
    def skipped_requirements(self) -> tuple[RequirementEvaluation, ...]:
        return tuple(r for r in self.requirement_evaluations if r.status == RequirementStatus.SKIPPED)


@dataclass(frozen=True)
class AcceptanceResult:
    status: AcceptanceStatus
    results: tuple[CriterionResult, ...]
    code: str
    report: DetailedAcceptanceReport | None = None


class AcceptanceGate:
    """Aggregate verification observations without executing or repairing work."""

    def evaluate(
        self,
        criteria: Iterable[AcceptanceCriterion],
        verifications: Mapping[str, VerificationResult],
        *,
        requirements: Iterable[Any] | None = None,
        run_id: str,
        observer: Callable[[EventType, dict[str, object]], None],
    ) -> AcceptanceResult:
        criteria = tuple(criteria)
        ids = [c.criterion_id for c in criteria if isinstance(c, AcceptanceCriterion)]
        invalid = any(
            not isinstance(c, AcceptanceCriterion)
            or not isinstance(c.criterion_id, str) or not c.criterion_id.strip()
            or not isinstance(c.description, str)
            or not isinstance(c.required, bool)
            or not isinstance(c.requirement_id, str)
            for c in criteria
        )
        duplicate = len(ids) != len(set(ids))
        if invalid or duplicate or not criteria:
            code = "invalid_criteria" if invalid else "duplicate_criterion_id" if duplicate else "empty_criteria"
            result = AcceptanceResult(AcceptanceStatus.FAIL, (), code, None)
            self._emit(result, criteria, run_id, observer)
            return result

        results: list[CriterionResult] = []
        for criterion in criteria:
            verification = verifications.get(criterion.criterion_id)
            if verification is None:
                status, code = AcceptanceStatus.FAIL, "verification_missing"
            elif not isinstance(verification, VerificationResult):
                status, code = AcceptanceStatus.FAIL, "invalid_verification"
            elif verification.status == VerificationStatus.PASS:
                status, code = AcceptanceStatus.PASS, "verification_passed"
            else:
                status, code = AcceptanceStatus.FAIL, "verification_failed"
            results.append(CriterionResult(
                criterion_id=criterion.criterion_id,
                status=status,
                code=code,
                requirement_id=criterion.requirement_id,
                verification_result=verification if isinstance(verification, VerificationResult) else None,
            ))

        if requirements is not None:
            resolved_requirements = tuple(requirements)
        else:
            from app.tasks.specification import Requirement
            req_map: dict[str, bool] = {}
            for c in criteria:
                rid = c.requirement_id if c.requirement_id.strip() else c.criterion_id
                req_map[rid] = req_map.get(rid, False) or c.required
            resolved_requirements = tuple(
                Requirement(requirement_id=rid, description=f"Requirement {rid}", required=req_req)
                for rid, req_req in req_map.items()
            )

        results_by_id = {r.criterion_id: r for r in results}
        req_evaluations: list[RequirementEvaluation] = []
        for req in resolved_requirements:
            req_id = req.requirement_id
            req_required = req.required

            matching_criteria = [
                c for c in criteria
                if (c.requirement_id == req_id if c.requirement_id.strip() else c.criterion_id == req_id)
            ]
            matching_results = [
                results_by_id[c.criterion_id] for c in matching_criteria
                if c.criterion_id in results_by_id
            ]

            if not matching_criteria:
                if req_required:
                    r_status = RequirementStatus.FAIL
                    reasons = ("missing_criteria_for_required_requirement",)
                else:
                    r_status = RequirementStatus.SKIPPED
                    reasons = ()
            else:
                required_matching_criteria = [c for c in matching_criteria if c.required]
                if required_matching_criteria:
                    any_required_failed = any(
                        results_by_id[c.criterion_id].status != AcceptanceStatus.PASS
                        for c in required_matching_criteria
                    )
                    r_status = RequirementStatus.FAIL if any_required_failed else RequirementStatus.PASS
                else:
                    all_passed = all(
                        results_by_id[c.criterion_id].status == AcceptanceStatus.PASS
                        for c in matching_criteria
                    )
                    r_status = RequirementStatus.PASS if all_passed else RequirementStatus.FAIL

                reasons = tuple(
                    f"{cr.criterion_id}:{cr.code}"
                    for cr in matching_results
                    if cr.status != AcceptanceStatus.PASS
                )

            req_evaluations.append(RequirementEvaluation(
                requirement_id=req_id,
                status=r_status,
                required=req_required,
                criterion_results=tuple(matching_results),
                failure_reasons=reasons,
            ))

        required_evals = [re for re in req_evaluations if re.required]
        if required_evals and all(re.status == RequirementStatus.PASS for re in required_evals):
            status = AcceptanceStatus.PASS
            code = "required_criteria_passed"
        else:
            status = AcceptanceStatus.FAIL
            code = "required_criteria_failed"

        report = DetailedAcceptanceReport(
            overall_status=status,
            requirement_evaluations=tuple(req_evaluations),
            code=code,
        )
        result = AcceptanceResult(status, tuple(results), code, report)
        self._emit(result, criteria, run_id, observer)
        return result

    @staticmethod
    def _emit(result, criteria, run_id, observer):
        by_id = {c.criterion_id: c for c in criteria if isinstance(c, AcceptanceCriterion)}
        required = [r for r in result.results if by_id.get(r.criterion_id) and by_id[r.criterion_id].required]
        passed = sum(r.status == AcceptanceStatus.PASS for r in required)
        failed = len(required) - passed
        event_data: dict[str, object] = {
            "run_id": run_id,
            "status": result.status.value,
            "required_criteria_count": len(required),
            "passed_count": passed,
            "failed_count": failed,
            "code": result.code,
        }
        if result.report is not None:
            event_data["failed_requirement_ids"] = [
                r.requirement_id
                for r in result.report.requirement_evaluations
                if r.status == RequirementStatus.FAIL
            ]
            event_data["failed_criterion_ids"] = [
                r.criterion_id
                for r in result.results
                if r.status == AcceptanceStatus.FAIL
            ]
        observer(EventType.ACCEPTANCE_COMPLETED, event_data)
