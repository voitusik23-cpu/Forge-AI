"""Data contracts for the Forge AI Agent Skill System v0.1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Any

from app.orchestrator.trace import FORBIDDEN_METADATA_SUBSTRINGS


class SkillTrustLevel(str, Enum):
    """Categorization of skill trust and provenance origin."""

    BUILTIN = "BUILTIN"      # Bundled with Forge core; verified by repository tests
    LOCAL = "LOCAL"          # Project-local repository skill; trusted for current project
    UNTRUSTED = "UNTRUSTED"  # External or unverified candidate skill


_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_TOOL_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


@dataclass(frozen=True)
class SkillProvenance:
    """Cryptographic and author provenance for a skill."""

    origin: str
    author: str
    content_hash: str

    def __post_init__(self) -> None:
        if not self.origin or not isinstance(self.origin, str) or not self.origin.strip():
            raise ValueError("origin must be a non-empty string")
        if not self.author or not isinstance(self.author, str) or not self.author.strip():
            raise ValueError("author must be a non-empty string")
        if not self.content_hash or not isinstance(self.content_hash, str) or not self.content_hash.strip():
            raise ValueError("content_hash must be a non-empty string")


@dataclass(frozen=True)
class SkillManifest:
    """Metadata describing a versioned, reusable procedure.

    Contains declarative capabilities and advisory tool identifiers only.
    Possesses zero execution authority.
    """

    skill_id: str
    name: str
    version: str
    description: str
    required_capabilities: tuple[str, ...] = ()
    requested_tools: tuple[str, ...] = ()
    trust_level: SkillTrustLevel = SkillTrustLevel.BUILTIN
    provenance: SkillProvenance | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.skill_id or not isinstance(self.skill_id, str) or not self.skill_id.strip():
            raise ValueError("skill_id must be a non-empty string")
        if not _IDENTIFIER_PATTERN.match(self.skill_id.strip()):
            raise ValueError(f"skill_id contains invalid characters: '{self.skill_id}'")

        if not self.name or not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("name must be a non-empty string")
        if not self.version or not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("version must be a non-empty string")
        if not self.description or not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("description must be a non-empty string")

        if not isinstance(self.trust_level, SkillTrustLevel):
            if isinstance(self.trust_level, str):
                try:
                    object.__setattr__(self, "trust_level", SkillTrustLevel(self.trust_level.upper()))
                except ValueError:
                    raise ValueError(f"Invalid trust_level: '{self.trust_level}'")
            else:
                raise ValueError(f"trust_level must be a SkillTrustLevel, got {type(self.trust_level)}")

        # Convert list to tuple if provided
        if isinstance(self.required_capabilities, list):
            object.__setattr__(self, "required_capabilities", tuple(self.required_capabilities))
        elif not isinstance(self.required_capabilities, tuple):
            raise ValueError("required_capabilities must be a tuple of strings")

        for cap in self.required_capabilities:
            if not isinstance(cap, str) or not cap.strip():
                raise ValueError("Each required_capability must be a non-empty string")

        if isinstance(self.requested_tools, list):
            object.__setattr__(self, "requested_tools", tuple(self.requested_tools))
        elif not isinstance(self.requested_tools, tuple):
            raise ValueError("requested_tools must be a tuple of strings")

        # Explicit validation for tool IDs: must be valid identifiers, NEVER raw shell commands
        for tool_id in self.requested_tools:
            if not isinstance(tool_id, str) or not tool_id.strip():
                raise ValueError("Each requested_tool must be a non-empty string")
            trimmed = tool_id.strip()
            # Explicitly reject spaces, shell metacharacters, or flags
            if any(char in trimmed for char in (" ", "\t", "\n", ";", "|", "&", "$", ">", "<", "`")):
                raise ValueError(
                    f"requested_tools must contain discrete Tool IDs only, not raw shell commands: '{tool_id}'"
                )
            if trimmed.startswith("-"):
                raise ValueError(
                    f"requested_tools cannot be command flags: '{tool_id}'"
                )
            if not _TOOL_ID_PATTERN.match(trimmed):
                raise ValueError(
                    f"requested_tools contains invalid tool ID format: '{tool_id}'"
                )

        # Strict metadata validation: do NOT silently sanitize, reject forbidden content explicitly
        if self.metadata:
            for k, v in self.metadata.items():
                if not isinstance(k, str):
                    raise ValueError(f"Metadata key must be a string, got {type(k)}")
                k_lower = k.lower()
                if any(bad in k_lower for bad in FORBIDDEN_METADATA_SUBSTRINGS):
                    raise ValueError(f"Skill metadata key contains forbidden sensitive word: '{k}'")
                v_str = str(v).lower()
                if any(bad in v_str for bad in FORBIDDEN_METADATA_SUBSTRINGS):
                    raise ValueError(f"Skill metadata value for '{k}' contains forbidden sensitive word")


@dataclass(frozen=True)
class SkillDefinition:
    """Complete skill container combining manifest metadata and procedural instructions."""

    manifest: SkillManifest
    instructions: str

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, SkillManifest):
            raise ValueError(f"manifest must be a SkillManifest instance, got {type(self.manifest)}")
        if not self.instructions or not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("instructions must be a non-empty string")
        if len(self.instructions) > 10000:
            raise ValueError(f"instructions exceed maximum allowed length: {len(self.instructions)} > 10000")


@dataclass(frozen=True)
class SkillApplicability:
    """Deterministic result of evaluating whether a skill is applicable to a run context."""

    skill_id: str
    is_applicable: bool
    reason: str
    matched_capabilities: tuple[str, ...] = ()
    missing_capabilities: tuple[str, ...] = ()
