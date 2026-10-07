"""Zero-copy adapters wrapping existing Forge subsystems into unified Fabric interfaces."""

from __future__ import annotations

from typing import Optional, Sequence, Union

from app.agents.providers.model_registry import ModelRegistry
from app.agents.providers.models import ModelCapability, ProviderModelInfo
from app.fabric.models import (
    AgentUsageRecord,
    AttemptUsageRecord,
    CapabilityDescriptor,
    CapabilityDomain,
    CapabilityRequirement,
    ResourceAccessMode,
    ResourceDescriptor,
    ResourceType,
    RunAccountingRecord,
)
from app.skills.models import SkillManifest
from app.skills.registry import SkillRegistry
from app.tools.contracts import ToolDefinition
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace
from app.usage import Usage


class ModelRegistryAdapter:
    """Adapter exposing ModelRegistry models and capabilities to Fabric."""

    def __init__(self, registry: ModelRegistry) -> None:
        self._registry = registry

    def get_capabilities_for_model(self, model_info: ProviderModelInfo) -> tuple[CapabilityDescriptor, ...]:
        caps: list[CapabilityDescriptor] = []
        for cap in model_info.capabilities:
            caps.append(
                CapabilityDescriptor(
                    capability_id=f"cap:model:{cap.value}",
                    domain=CapabilityDomain.MODEL,
                    name=cap.value,
                    description=f"Model capability {cap.value} supported by {model_info.canonical_id}",
                    metadata={"provider_id": model_info.provider_id, "model_id": model_info.model_id},
                )
            )
        return tuple(caps)

    def find_satisfying_models(
        self,
        required_capabilities: Sequence[Union[CapabilityRequirement, ModelCapability, str]],
        allow_paid: bool = False,
    ) -> tuple[ProviderModelInfo, ...]:
        model_caps: list[ModelCapability] = []
        for req in required_capabilities:
            if isinstance(req, CapabilityRequirement):
                if req.domain != CapabilityDomain.MODEL:
                    continue
                try:
                    model_caps.append(ModelCapability(req.name.lower()))
                except ValueError:
                    pass
            elif isinstance(req, ModelCapability):
                model_caps.append(req)
            elif isinstance(req, str):
                try:
                    parsed = CapabilityRequirement.parse(req)
                    if parsed.domain == CapabilityDomain.MODEL:
                        model_caps.append(ModelCapability(parsed.name.lower()))
                except ValueError:
                    pass

        return self._registry.find_models(
            capabilities=model_caps,
            max_cost_tier=None,  # Checked by caller policy / fabric boundary
        )


class ToolRegistryAdapter:
    """Adapter exposing ToolRegistry tools as Fabric capabilities."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def list_tool_capabilities(self) -> tuple[CapabilityDescriptor, ...]:
        tools = self._registry.list_tools()
        caps: list[CapabilityDescriptor] = []
        for t in tools:
            caps.append(
                CapabilityDescriptor(
                    capability_id=f"cap:tool:{t.id}",
                    domain=CapabilityDomain.TOOL,
                    name=t.name,
                    description=t.description,
                    metadata={"tool_id": t.id},
                )
            )
        return tuple(caps)


class SkillRegistryAdapter:
    """Adapter inspecting SkillManifests without granting automatic execution authority."""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry

    def get_skill_required_capabilities(self, skill_id: str) -> tuple[str, ...]:
        """Return raw declarative capability requirements declared in the manifest."""
        skill_def = self._registry.get(skill_id)
        return skill_def.manifest.required_capabilities

    def get_skill_typed_requirements(self, skill_id: str) -> tuple[CapabilityRequirement, ...]:
        """Return typed CapabilityRequirement descriptors declared by the skill."""
        skill_def = self._registry.get(skill_id)
        return tuple(CapabilityRequirement.parse(c) for c in skill_def.manifest.required_capabilities)

    def get_skill_requested_tools(self, skill_id: str) -> tuple[str, ...]:
        """Return tool names requested by the skill (declarative only, no execution authority)."""
        skill_def = self._registry.get(skill_id)
        return skill_def.manifest.requested_tools


class WorkspaceResourceAdapter:
    """Adapter exposing Workspace root as a Fabric ResourceDescriptor."""

    def __init__(self, workspace: Workspace) -> None:
        self._workspace = workspace

    def to_resource_descriptor(self, access_mode: ResourceAccessMode = ResourceAccessMode.READ) -> ResourceDescriptor:
        return ResourceDescriptor(
            resource_id=f"res:workspace:{hash(str(self._workspace.root))}",
            resource_type=ResourceType.WORKSPACE,
            uri_or_path=str(self._workspace.root),
            access_mode=access_mode,
            is_available=True,
            metadata={"root": str(self._workspace.root)},
        )


class UsageAccountingAdapter:
    """Adapter constructing AttemptUsageRecord and RunAccountingRecord from execution telemetry."""

    @staticmethod
    def calculate_cost(
        *,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        model_info: Optional[ProviderModelInfo] = None,
        input_cost_per_1m: Optional[float] = None,
        output_cost_per_1m: Optional[float] = None,
        cached_cost_per_1m: Optional[float] = None,
    ) -> Optional[float]:
        """Calculate estimated cost using token counts and 1M-token pricing rates."""
        inp_rate = input_cost_per_1m if input_cost_per_1m is not None else (model_info.input_cost_per_1m if model_info else None)
        out_rate = output_cost_per_1m if output_cost_per_1m is not None else (model_info.output_cost_per_1m if model_info else None)
        if cached_cost_per_1m is None and model_info is not None and isinstance(model_info.metadata, dict):
            cached_cost_per_1m = model_info.metadata.get("cached_cost_per_1m")

        if inp_rate is None and out_rate is None and cached_cost_per_1m is None:
            return None

        total = 0.0
        if inp_rate is not None and inp_rate > 0:
            total += (input_tokens * inp_rate) / 1_000_000.0
        if out_rate is not None and out_rate > 0:
            total += (output_tokens * out_rate) / 1_000_000.0
        if cached_cost_per_1m is not None and cached_cost_per_1m > 0 and cached_tokens > 0:
            total += (cached_tokens * cached_cost_per_1m) / 1_000_000.0
        return total

    @staticmethod
    def create_attempt_usage(
        *,
        attempt_index: int = 1,
        agent_name: str,
        provider_name: str,
        model_name: str,
        usage: Optional[Usage] = None,
        duration_seconds: float = 0.0,
        model_info: Optional[ProviderModelInfo] = None,
        provider_reported_cost: Optional[float] = None,
        success: bool = True,
        error_message: Optional[str] = None,
        is_fallback: bool = False,
    ) -> AttemptUsageRecord:
        input_tokens = usage.input_tokens if usage else 0
        output_tokens = usage.output_tokens if usage else 0
        cached_tokens = getattr(usage, "cached_tokens", 0) if usage else 0
        estimated_cost = usage.estimated_cost if usage and usage.estimated_cost is not None else None

        # If estimated cost is missing in Usage but model pricing exists, calculate it:
        if estimated_cost is None and model_info is not None:
            estimated_cost = UsageAccountingAdapter.calculate_cost(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                model_info=model_info,
            )

        return AttemptUsageRecord(
            attempt_index=attempt_index,
            agent_name=agent_name,
            provider_name=provider_name,
            model_name=model_name,
            request_count=1,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            duration_seconds=duration_seconds,
            provider_reported_cost=provider_reported_cost,
            estimated_cost=estimated_cost,
            success=success,
            error_message=error_message,
            is_fallback=is_fallback,
        )

    # create_agent_usage is a backward compatible alias for create_attempt_usage
    create_agent_usage = create_attempt_usage

    @staticmethod
    def create_run_accounting(
        *,
        run_id: str,
        task_id: str = "",
        project_id: str = "",
        attempt_records: Sequence[AttemptUsageRecord] = (),
        agent_records: Optional[Sequence[AttemptUsageRecord]] = None,
        retry_count: int = 0,
        fallback_events: Sequence[str] = (),
        associated_artifact_ids: Sequence[str] = (),
        total_duration_seconds: float = 0.0,
    ) -> RunAccountingRecord:
        records = tuple(agent_records if agent_records is not None else attempt_records)
        return RunAccountingRecord(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            attempt_records=records,
            retry_count=retry_count,
            fallback_events=tuple(fallback_events),
            associated_artifact_ids=tuple(associated_artifact_ids),
            total_duration_seconds=total_duration_seconds,
        )
