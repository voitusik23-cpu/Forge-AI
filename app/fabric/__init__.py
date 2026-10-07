"""Forge AI Capability & Resource Fabric Subsystem v0.1."""

from __future__ import annotations

from app.fabric.adapters import (
    ModelRegistryAdapter,
    SkillRegistryAdapter,
    ToolRegistryAdapter,
    UsageAccountingAdapter,
    WorkspaceResourceAdapter,
)
from app.fabric.fabric import CapabilityFabric
from app.fabric.models import (
    AgentUsageRecord,
    AttemptUsageRecord,
    CapabilityDescriptor,
    CapabilityDomain,
    CapabilityRequirement,
    CapabilityType,
    ExecutionBoundary,
    FabricRequest,
    FabricResolution,
    ResourceAccessMode,
    ResourceDescriptor,
    ResourceType,
    RunAccountingRecord,
)

__all__ = [
    "AgentUsageRecord",
    "AttemptUsageRecord",
    "CapabilityDescriptor",
    "CapabilityDomain",
    "CapabilityFabric",
    "CapabilityRequirement",
    "CapabilityType",
    "ExecutionBoundary",
    "FabricRequest",
    "FabricResolution",
    "ModelRegistryAdapter",
    "ResourceAccessMode",
    "ResourceDescriptor",
    "ResourceType",
    "RunAccountingRecord",
    "SkillRegistryAdapter",
    "ToolRegistryAdapter",
    "UsageAccountingAdapter",
    "WorkspaceResourceAdapter",
]
