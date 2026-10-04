"""Provider-neutral input contracts for engineering task specifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.tools.acceptance import AcceptanceCriterion


@dataclass(frozen=True)
class Requirement:
    """A statement of what a task must accomplish."""

    requirement_id: str
    description: str
    required: bool = True


class SpecificationValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class SpecificationValidationResult:
    status: SpecificationValidationStatus
    errors: tuple[str, ...] = ()

    @property
    def valid(self) -> bool:
        return self.status == SpecificationValidationStatus.PASS


class InvalidTaskSpecificationError(ValueError):
    """Raised when a specification is rejected before an Engineering Run starts."""

    def __init__(self, result: SpecificationValidationResult) -> None:
        self.validation_result = result
        super().__init__("TaskSpecification is invalid: " + ", ".join(result.errors))


@dataclass(frozen=True)
class TaskSpecification:
    """User-owned task data, separate from execution and policy inputs."""

    task_id: str
    title: str
    description: str
    requirements: tuple[Requirement, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]

    def __post_init__(self) -> None:
        # Lists are convenient at API boundaries; store immutable collections.
        if isinstance(self.requirements, list):
            object.__setattr__(self, "requirements", tuple(self.requirements))
        if isinstance(self.acceptance_criteria, list):
            object.__setattr__(self, "acceptance_criteria", tuple(self.acceptance_criteria))

    def validate(self) -> SpecificationValidationResult:
        errors: list[str] = []
        for field_name in ("task_id", "title", "description"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{field_name}_required")

        if not isinstance(self.requirements, tuple):
            errors.append("requirements_required")
            requirements = ()
        else:
            requirements = self.requirements
            if not requirements:
                errors.append("requirements_required")
            if any(not isinstance(item, Requirement) for item in requirements):
                errors.append("invalid_requirement")
        requirement_ids: list[str] = []
        for item in requirements:
            if not isinstance(item, Requirement):
                continue
            if not isinstance(item.requirement_id, str) or not item.requirement_id.strip():
                errors.append("requirement_id_required")
            else:
                requirement_ids.append(item.requirement_id)
            if not isinstance(item.description, str) or not item.description.strip():
                errors.append("requirement_description_required")
            if not isinstance(item.required, bool):
                errors.append("requirement_required_must_be_bool")
        if len(requirement_ids) != len(set(requirement_ids)):
            errors.append("duplicate_requirement_id")

        if not isinstance(self.acceptance_criteria, tuple):
            errors.append("acceptance_criteria_required")
            criteria = ()
        else:
            criteria = self.acceptance_criteria
            if not criteria:
                errors.append("acceptance_criteria_required")
            if any(not isinstance(item, AcceptanceCriterion) for item in criteria):
                errors.append("invalid_acceptance_criterion")
        criterion_ids: list[str] = []
        for item in criteria:
            if not isinstance(item, AcceptanceCriterion):
                continue
            if not isinstance(item.criterion_id, str) or not item.criterion_id.strip():
                errors.append("acceptance_criterion_id_required")
            else:
                criterion_ids.append(item.criterion_id)
            if not isinstance(item.description, str) or not item.description.strip():
                errors.append("acceptance_criterion_description_required")
            if not isinstance(item.required, bool):
                errors.append("acceptance_criterion_required_must_be_bool")
        if len(criterion_ids) != len(set(criterion_ids)):
            errors.append("duplicate_acceptance_criterion_id")

        return SpecificationValidationResult(
            SpecificationValidationStatus.FAIL if errors else SpecificationValidationStatus.PASS,
            tuple(errors),
        )

    def to_context_data(self) -> dict[str, object]:
        """Return only specification data for the existing ContextAssembler."""
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "requirements": [
                {
                    "requirement_id": item.requirement_id,
                    "description": item.description,
                    "required": item.required,
                }
                for item in self.requirements
            ],
            "acceptance_criteria": [
                {
                    "criterion_id": item.criterion_id,
                    "description": item.description,
                    "required": item.required,
                }
                for item in self.acceptance_criteria
            ],
        }
