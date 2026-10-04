"""In-memory registry for discoverable, provider-neutral skills."""

from __future__ import annotations

from app.skills.models import SkillDefinition, SkillManifest


class SkillNotFoundError(LookupError):
    """Raised when an operation references an unregistered skill."""


class DuplicateSkillError(ValueError):
    """Raised when a skill ID is registered more than once."""


class SkillRegistry:
    """Deterministic, thread-safe in-memory registry for SkillDefinitions."""

    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}

    def register(self, skill: SkillDefinition) -> None:
        if not isinstance(skill, SkillDefinition):
            raise TypeError(f"Expected SkillDefinition, got {type(skill).__name__}")
        skill_id = skill.manifest.skill_id
        if skill_id in self._skills:
            raise DuplicateSkillError(f"Skill '{skill_id}' is already registered")
        self._skills[skill_id] = skill

    def get(self, skill_id: str) -> SkillDefinition:
        try:
            return self._skills[skill_id]
        except KeyError as exc:
            raise SkillNotFoundError(f"Skill '{skill_id}' is not registered") from exc

    def list_manifests(self) -> list[SkillManifest]:
        return [self._skills[key].manifest for key in sorted(self._skills)]

    def list_skills(self) -> list[SkillDefinition]:
        return [self._skills[key] for key in sorted(self._skills)]

    def contains(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def clear(self) -> None:
        """Reset the registry (primarily for isolated test fixtures)."""
        self._skills.clear()
