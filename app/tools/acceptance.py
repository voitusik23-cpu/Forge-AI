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


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    required: bool = True


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    status: AcceptanceStatus
    code: str


@dataclass(frozen=True)
class AcceptanceResult:
    status: AcceptanceStatus
    results: tuple[CriterionResult, ...]
    code: str


class AcceptanceGate:
    """Aggregate verification observations without executing or repairing work."""

    def evaluate(
        self,
        criteria: Iterable[AcceptanceCriterion],
        verifications: Mapping[str, VerificationResult],
        *,
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
            for c in criteria
        )
        duplicate = len(ids) != len(set(ids))
        if invalid or duplicate or not criteria:
            code = "invalid_criteria" if invalid else "duplicate_criterion_id" if duplicate else "empty_criteria"
            result = AcceptanceResult(AcceptanceStatus.FAIL, (), code)
            self._emit(result, criteria, run_id, observer)
            return result

        results = []
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
            results.append(CriterionResult(criterion.criterion_id, status, code))

        required_results = [r for r, c in zip(results, criteria) if c.required]
        status = (
            AcceptanceStatus.PASS
            if required_results and all(r.status == AcceptanceStatus.PASS for r in required_results)
            else AcceptanceStatus.FAIL
        )
        code = "required_criteria_passed" if status == AcceptanceStatus.PASS else "required_criteria_failed"
        result = AcceptanceResult(status, tuple(results), code)
        self._emit(result, criteria, run_id, observer)
        return result

    @staticmethod
    def _emit(result, criteria, run_id, observer):
        by_id = {c.criterion_id: c for c in criteria if isinstance(c, AcceptanceCriterion)}
        required = [r for r in result.results if by_id.get(r.criterion_id) and by_id[r.criterion_id].required]
        passed = sum(r.status == AcceptanceStatus.PASS for r in required)
        failed = len(required) - passed
        observer(EventType.ACCEPTANCE_COMPLETED, {
            "run_id": run_id,
            "status": result.status.value,
            "required_criteria_count": len(required),
            "passed_count": passed,
            "failed_count": failed,
            "code": result.code,
        })
