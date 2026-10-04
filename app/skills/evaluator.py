"""Deterministic evaluation of skill applicability.

Evaluates whether a skill matches run capabilities and task constraints.
Possesses zero execution or policy-mutation authority.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.skills.models import SkillApplicability, SkillDefinition, SkillTrustLevel

if TYPE_CHECKING:
    from app.tasks.specification import TaskSpecification


class SkillEvaluator:
    """Pure deterministic evaluator for skill eligibility.

    Performs boolean capability and constraint matching. Never invokes
    tools, calls subprocesses, executes network operations, alters
    permissions, or makes policy authorization decisions.
    """

    def __init__(
        self,
        allowed_trust_levels: frozenset[SkillTrustLevel] | None = None,
    ) -> None:
        self.allowed_trust_levels = allowed_trust_levels

    def evaluate(
        self,
        skill: SkillDefinition,
        *,
        task: TaskSpecification | None = None,
        available_capabilities: frozenset[str] = frozenset(),
        available_tool_ids: frozenset[str] = frozenset(),
        allowed_trust_levels: frozenset[SkillTrustLevel] | None = None,
    ) -> SkillApplicability:
        """Evaluate a single skill against available capabilities, tools, and constraints."""
        skill_id = skill.manifest.skill_id
        required_caps = frozenset(skill.manifest.required_capabilities)

        # 1. Check trust constraints if configured
        effective_trust = (
            allowed_trust_levels
            if allowed_trust_levels is not None
            else self.allowed_trust_levels
        )
        if effective_trust is not None and skill.manifest.trust_level not in effective_trust:
            return SkillApplicability(
                skill_id=skill_id,
                is_applicable=False,
                reason=f"trust_level_not_allowed: {skill.manifest.trust_level.value}",
                matched_capabilities=(),
                missing_capabilities=(),
            )

        # 2. Check required capabilities
        matched = tuple(sorted(required_caps & available_capabilities))
        missing = tuple(sorted(required_caps - available_capabilities))

        if missing:
            return SkillApplicability(
                skill_id=skill_id,
                is_applicable=False,
                reason=f"missing_required_capabilities: {missing}",
                matched_capabilities=matched,
                missing_capabilities=missing,
            )

        # 3. Check requested tools if tool filtering is enabled
        if available_tool_ids and skill.manifest.requested_tools:
            requested = frozenset(skill.manifest.requested_tools)
            # If none of the requested tools are available in the run, the skill cannot operate
            if not (requested & available_tool_ids):
                return SkillApplicability(
                    skill_id=skill_id,
                    is_applicable=False,
                    reason=f"none_of_requested_tools_available: {sorted(requested)}",
                    matched_capabilities=matched,
                    missing_capabilities=(),
                )

        # 4. Check task specification constraints if available
        if task is not None:
            task_type = getattr(task, "task_type", None)
            target_types = skill.manifest.metadata.get("target_task_types")
            if task_type and target_types and isinstance(target_types, (list, tuple)):
                if task_type not in target_types:
                    return SkillApplicability(
                        skill_id=skill_id,
                        is_applicable=False,
                        reason=f"task_type_mismatch: {task_type} not in {target_types}",
                        matched_capabilities=matched,
                        missing_capabilities=(),
                    )

        return SkillApplicability(
            skill_id=skill_id,
            is_applicable=True,
            reason="all_requirements_satisfied",
            matched_capabilities=matched,
            missing_capabilities=(),
        )

    def find_applicable_skills(
        self,
        skills: Iterable[SkillDefinition],
        *,
        task: TaskSpecification | None = None,
        available_capabilities: frozenset[str] = frozenset(),
        available_tool_ids: frozenset[str] = frozenset(),
        allowed_trust_levels: frozenset[SkillTrustLevel] | None = None,
    ) -> tuple[SkillDefinition, ...]:
        """Filter an iterable of skills to those that deterministically match the context."""
        applicable: list[SkillDefinition] = []
        for skill in skills:
            app = self.evaluate(
                skill,
                task=task,
                available_capabilities=available_capabilities,
                available_tool_ids=available_tool_ids,
                allowed_trust_levels=allowed_trust_levels,
            )
            if app.is_applicable:
                applicable.append(skill)

        # Sort deterministically by skill_id
        applicable.sort(key=lambda s: s.manifest.skill_id)
        return tuple(applicable)
