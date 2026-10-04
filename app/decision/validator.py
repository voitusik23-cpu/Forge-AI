from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from app.decision.models import Decision, DecisionAction, DecisionRequest, DecisionType

if TYPE_CHECKING:
    from app.projects.state import ProjectState

DECISION_TO_ACTION_COMPATIBILITY: dict[DecisionType, tuple[DecisionAction, ...]] = {
    DecisionType.COMPLETE: (DecisionAction.COMPLETE_RUN,),
    DecisionType.VERIFY: (DecisionAction.RUN_VERIFICATION,),
    DecisionType.REVISE: (DecisionAction.REQUEST_REVISION,),
    DecisionType.REQUEST_APPROVAL: (
        DecisionAction.REQUEST_USER_APPROVAL,
        DecisionAction.WAIT_FOR_APPROVAL,
    ),
    DecisionType.WAIT: (DecisionAction.WAIT_FOR_APPROVAL,),
    DecisionType.CONTINUE: (DecisionAction.EXECUTE,),
    DecisionType.FAIL: (DecisionAction.FAIL_RUN,),
}


@dataclass(frozen=True)
class DecisionValidationReport:
    """Validation report for a Decision recommendation."""

    valid: bool
    errors: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.valid

    def __iter__(self):
        return iter(self.errors)

    def __len__(self) -> int:
        return len(self.errors)


def validate_decision(
    decision: Decision,
    request: DecisionRequest,
    state: Optional[ProjectState] = None,
) -> DecisionValidationReport:
    """Validate a Decision against the originating request and optional ProjectState.

    Returns a DecisionValidationReport with boolean valid and tuple of error codes.
    """
    errors: list[str] = []

    # 1. Run binding check
    if decision.run_id != request.run_id:
        errors.append("run_id_mismatch")

    # 2. DecisionType and Action validity
    if not isinstance(decision.decision_type, DecisionType):
        errors.append("invalid_decision_type")
    if not isinstance(decision.action, DecisionAction):
        errors.append("invalid_action")

    # 3. Decision-Action compatibility
    allowed_actions = DECISION_TO_ACTION_COMPATIBILITY.get(decision.decision_type, ())
    if decision.action not in allowed_actions:
        errors.append(f"incompatible_action:{decision.decision_type.value}->{decision.action.value}")

    # 4. Capability / available action check
    if request.available_actions and decision.action not in request.available_actions:
        errors.append(f"unavailable_action:{decision.action.value}")

    # 5. Attempt number consistency
    if decision.attempt_number is not None:
        if decision.attempt_number < 0:
            errors.append("malformed_attempt_number")
        elif request.attempt_number is not None and decision.attempt_number != request.attempt_number:
            errors.append("attempt_number_mismatch")

    # 6. Cross-run references check
    if decision.references:
        ref_run_id = decision.references.get("run_id")
        if ref_run_id is not None and ref_run_id != request.run_id:
            errors.append("cross_run_reference")

    return DecisionValidationReport(valid=len(errors) == 0, errors=tuple(errors))

