"""Unified domain contracts for Forge AI Capability & Resource Fabric v0.1.

Revises capability domains, execution authority boundaries, and run accounting
contracts after independent architecture review.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Union

from app.agents.providers.models import CostTier, ModelCapability, ProviderModelInfo
from app.execution.capabilities import ExecutionCapability
from app.usage import Usage


class CapabilityDomain(str, Enum):
    """Explicit namespaces preventing semantic collision between capability types."""

    MODEL = "model"          # Model inference features (reasoning, vision, fast, code, etc.)
    EXECUTION = "execution"  # OS/Process execution capabilities (EXEC_CHILD, INTERPRET_TEXT, NETWORK)
    TOOL = "tool"            # Registered tool invocations (read_project_file, execute_command, etc.)
    TASK = "task"            # High-level task routing categories (code, analysis, review)
    CUSTOM = "custom"        # Domain-specific extensions


# Alias for backward compatibility
CapabilityType = CapabilityDomain


class ResourceType(str, Enum):
    """Types of resources accessible to actions."""

    WORKSPACE = "WORKSPACE"
    FILE = "FILE"
    API = "API"
    SECRET = "SECRET"
    NETWORK = "NETWORK"
    DATABASE = "DATABASE"
    COMPUTE = "COMPUTE"
    UNDERSTANDING_SNAPSHOT = "UNDERSTANDING_SNAPSHOT"
    PROJECT_MEMORY = "PROJECT_MEMORY"
    PROJECT_KNOWLEDGE = "PROJECT_KNOWLEDGE"


class ResourceAccessMode(str, Enum):
    """Access permissions for a requested resource."""

    READ = "READ"
    WRITE = "WRITE"
    EXECUTE = "EXECUTE"
    ADMIN = "ADMIN"


@dataclass(frozen=True)
class CapabilityRequirement:
    """A typed capability requirement explicitly tied to a CapabilityDomain."""

    domain: CapabilityDomain
    name: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Capability requirement 'name' must be non-empty")
        if isinstance(self.parameters, dict):
            object.__setattr__(self, "parameters", dict(self.parameters))

    @property
    def qualified_name(self) -> str:
        """Return canonical domain:name string representation."""
        return f"{self.domain.value}:{self.name}"

    @classmethod
    def model(cls, cap: Union[ModelCapability, str], **params: Any) -> CapabilityRequirement:
        name = cap.value if isinstance(cap, ModelCapability) else str(cap)
        return cls(domain=CapabilityDomain.MODEL, name=name, parameters=params)

    @classmethod
    def execution(cls, cap: Union[ExecutionCapability, str], **params: Any) -> CapabilityRequirement:
        name = cap.value if isinstance(cap, ExecutionCapability) else str(cap)
        return cls(domain=CapabilityDomain.EXECUTION, name=name, parameters=params)

    @classmethod
    def tool(cls, name: str, **params: Any) -> CapabilityRequirement:
        return cls(domain=CapabilityDomain.TOOL, name=name, parameters=params)

    @classmethod
    def task(cls, name: str, **params: Any) -> CapabilityRequirement:
        return cls(domain=CapabilityDomain.TASK, name=name, parameters=params)

    @classmethod
    def parse(cls, val: Union[CapabilityRequirement, str]) -> CapabilityRequirement:
        """Parse a CapabilityRequirement or domain:name string."""
        if isinstance(val, CapabilityRequirement):
            return val
        if not isinstance(val, str) or not val.strip():
            raise ValueError(f"Invalid capability representation: {val!r}")
        raw = val.strip()
        if ":" in raw:
            domain_part, name_part = raw.split(":", 1)
            domain_norm = domain_part.strip().lower()
            try:
                domain = CapabilityDomain(domain_norm)
            except ValueError:
                domain = CapabilityDomain.CUSTOM
            return cls(domain=domain, name=name_part.strip())
        # If no colon prefix, default to MODEL if matches ModelCapability, otherwise TOOL
        try:
            ModelCapability(raw.lower())
            return cls(domain=CapabilityDomain.MODEL, name=raw.lower())
        except ValueError:
            return cls(domain=CapabilityDomain.TOOL, name=raw)


@dataclass(frozen=True)
class CapabilityDescriptor:
    """Immutable, standardized capability definition."""

    capability_id: str
    domain: CapabilityDomain
    name: str
    description: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.capability_id:
            raise ValueError("capability_id must be a non-empty string")
        if not self.name:
            raise ValueError("name must be a non-empty string")
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def capability_type(self) -> CapabilityDomain:
        """Backward compatibility alias for domain."""
        return self.domain

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable projection of this descriptor.

        Only describing fields are exposed. Descriptors carry no credentials,
        approval state, or frozen scope, so a discovery response built from these
        fields cannot leak authority.
        """
        return {
            "capability_id": self.capability_id,
            "domain": (
                self.domain.value if hasattr(self.domain, "value") else str(self.domain)
            ),
            "name": self.name,
            "description": self.description,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ResourceDescriptor:
    """Immutable representation of a tangible or virtual resource."""

    resource_id: str
    resource_type: ResourceType
    uri_or_path: str
    access_mode: ResourceAccessMode = ResourceAccessMode.READ
    is_available: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.resource_id:
            raise ValueError("resource_id must be a non-empty string")
        if not self.uri_or_path:
            raise ValueError("uri_or_path must be a non-empty string")
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class ExecutionBoundary:
    """Immutable boundary parameters constraining an action's execution."""

    allowed_read_paths: tuple[str, ...] = ()
    allowed_write_paths: tuple[str, ...] = ()
    allow_network: bool = False
    max_duration_seconds: float = 300.0
    cost_ceiling_usd: Optional[float] = None
    token_budget: Optional[int] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.allowed_read_paths, (list, tuple)):
            object.__setattr__(self, "allowed_read_paths", tuple(self.allowed_read_paths))
        if isinstance(self.allowed_write_paths, (list, tuple)):
            object.__setattr__(self, "allowed_write_paths", tuple(self.allowed_write_paths))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class FabricRequest:
    """A structured inquiry asking Fabric to resolve capability and resource requirements."""

    subject_agent: str
    intent_description: str
    required_capabilities: tuple[CapabilityRequirement, ...] = ()
    required_resources: tuple[ResourceDescriptor, ...] = ()
    task_category: Optional[str] = None
    explicit_model: Optional[str] = None
    explicit_provider: Optional[str] = None
    run_id: str = ""
    project_id: str = ""
    context_tokens: int = 0
    allow_paid_providers: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.subject_agent:
            raise ValueError("subject_agent must be a non-empty string")
        if isinstance(self.required_capabilities, (list, tuple)):
            parsed_caps = tuple(CapabilityRequirement.parse(c) for c in self.required_capabilities)
            object.__setattr__(self, "required_capabilities", parsed_caps)
        if isinstance(self.required_resources, (list, tuple)):
            object.__setattr__(self, "required_resources", tuple(self.required_resources))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class FabricResolution:
    """Outcome of resolving a FabricRequest against available capabilities and resources."""

    is_capable: bool
    is_authorized: bool
    selected_agent: Optional[str] = None
    selected_provider: Optional[str] = None
    selected_model: Optional[str] = None
    candidate_providers: tuple[str, ...] = ()
    candidate_models: tuple[ProviderModelInfo, ...] = ()
    selected_tools: tuple[str, ...] = ()
    matched_capabilities: tuple[CapabilityDescriptor, ...] = ()
    allocated_resources: tuple[ResourceDescriptor, ...] = ()
    execution_boundary: ExecutionBoundary = field(default_factory=ExecutionBoundary)
    rejection_reasons: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.candidate_providers, (list, tuple)):
            object.__setattr__(self, "candidate_providers", tuple(self.candidate_providers))
        if isinstance(self.candidate_models, (list, tuple)):
            object.__setattr__(self, "candidate_models", tuple(self.candidate_models))
        if isinstance(self.selected_tools, (list, tuple)):
            object.__setattr__(self, "selected_tools", tuple(self.selected_tools))
        if isinstance(self.matched_capabilities, (list, tuple)):
            object.__setattr__(self, "matched_capabilities", tuple(self.matched_capabilities))
        if isinstance(self.allocated_resources, (list, tuple)):
            object.__setattr__(self, "allocated_resources", tuple(self.allocated_resources))
        if isinstance(self.rejection_reasons, (list, tuple)):
            object.__setattr__(self, "rejection_reasons", tuple(self.rejection_reasons))

    @property
    def can_proceed(self) -> bool:
        """Return True only if the action is both technically capable and within static bounds."""
        return self.is_capable and self.is_authorized and not self.rejection_reasons


@dataclass(frozen=True)
class AttemptUsageRecord:
    """Telemetry and cost accounting for a single provider/agent invocation attempt within a run."""

    attempt_index: int = 1
    agent_name: str = ""
    provider_name: str = ""
    model_name: str = ""
    request_count: int = 1
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    duration_seconds: float = 0.0
    provider_reported_cost: Optional[float] = None
    estimated_cost: Optional[float] = None
    success: bool = True
    error_message: Optional[str] = None
    is_fallback: bool = False

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def effective_cost(self) -> float:
        """Return provider-reported cost if present, otherwise estimated cost."""
        if self.provider_reported_cost is not None:
            return self.provider_reported_cost
        if self.estimated_cost is not None:
            return self.estimated_cost
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "agent_name": self.agent_name,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "request_count": self.request_count,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "total_tokens": self.total_tokens,
            "duration_seconds": self.duration_seconds,
            "provider_reported_cost": self.provider_reported_cost,
            "estimated_cost": self.estimated_cost,
            "effective_cost": self.effective_cost,
            "success": self.success,
            "error_message": self.error_message,
            "is_fallback": self.is_fallback,
        }


# AgentUsageRecord is an alias for AttemptUsageRecord for backward compatibility
AgentUsageRecord = AttemptUsageRecord


@dataclass(frozen=True)
class RunAccountingRecord:
    """Aggregated telemetry and cost accounting record for an entire Run."""

    run_id: str
    task_id: str = ""
    project_id: str = ""
    attempt_records: tuple[AttemptUsageRecord, ...] = ()
    retry_count: int = 0
    fallback_events: tuple[str, ...] = ()
    associated_artifact_ids: tuple[str, ...] = ()
    total_duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if isinstance(self.attempt_records, (list, tuple)):
            object.__setattr__(self, "attempt_records", tuple(self.attempt_records))
        if isinstance(self.fallback_events, (list, tuple)):
            object.__setattr__(self, "fallback_events", tuple(self.fallback_events))
        if isinstance(self.associated_artifact_ids, (list, tuple)):
            object.__setattr__(self, "associated_artifact_ids", tuple(self.associated_artifact_ids))

    @property
    def agent_records(self) -> tuple[AttemptUsageRecord, ...]:
        """Backward compatibility alias for attempt_records."""
        return self.attempt_records

    @property
    def total_attempts(self) -> int:
        return len(self.attempt_records)

    @property
    def successful_attempt(self) -> Optional[AttemptUsageRecord]:
        for rec in reversed(self.attempt_records):
            if rec.success:
                return rec
        return None

    @property
    def total_requests(self) -> int:
        return sum(r.request_count for r in self.attempt_records)

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.attempt_records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.attempt_records)

    @property
    def total_cached_tokens(self) -> int:
        return sum(r.cached_tokens for r in self.attempt_records)

    @property
    def total_tokens(self) -> int:
        return sum(r.total_tokens for r in self.attempt_records)

    @property
    def total_cost(self) -> float:
        return sum(r.effective_cost for r in self.attempt_records)

    @property
    def total_estimated_cost(self) -> float:
        return sum(r.estimated_cost or 0.0 for r in self.attempt_records)

    @property
    def total_provider_reported_cost(self) -> float:
        return sum(r.provider_reported_cost or 0.0 for r in self.attempt_records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
            "total_attempts": self.total_attempts,
            "total_requests": self.total_requests,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "total_estimated_cost": self.total_estimated_cost,
            "total_provider_reported_cost": self.total_provider_reported_cost,
            "total_duration_seconds": self.total_duration_seconds,
            "retry_count": self.retry_count,
            "fallback_events": list(self.fallback_events),
            "associated_artifact_ids": list(self.associated_artifact_ids),
            "attempt_records": [r.to_dict() for r in self.attempt_records],
        }
