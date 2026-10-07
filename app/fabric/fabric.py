"""Unified Capability & Resource Fabric coordinator resolving capabilities, resources, and usage."""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional, Sequence

from app.agents.providers.model_registry import ModelRegistry
from app.agents.providers.models import CostTier, ModelCapability, ProviderModelInfo
from app.execution.capabilities import ExecutionCapability
from app.fabric.adapters import (
    ModelRegistryAdapter,
    SkillRegistryAdapter,
    ToolRegistryAdapter,
    UsageAccountingAdapter,
    WorkspaceResourceAdapter,
)
from app.fabric.models import (
    AgentUsageRecord,
    AttemptUsageRecord,
    CapabilityDescriptor,
    CapabilityDomain,
    CapabilityRequirement,
    ExecutionBoundary,
    FabricRequest,
    FabricResolution,
    ResourceDescriptor,
    RunAccountingRecord,
)
from app.skills.registry import SkillRegistry
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace


class CapabilityFabric:
    """Integration and coordination layer for capabilities, resources, and run accounting.

    Fabric does not act as a shadow registry, does not execute routing, and does NOT
    grant execution authority or mutate RunScope.
    """

    def __init__(
        self,
        *,
        model_registry: Optional[ModelRegistry] = None,
        tool_registry: Optional[ToolRegistry] = None,
        skill_registry: Optional[SkillRegistry] = None,
        workspace: Optional[Workspace] = None,
    ) -> None:
        self._model_registry = model_registry
        self._tool_registry = tool_registry
        self._skill_registry = skill_registry
        self._workspace = workspace

        self._model_adapter = ModelRegistryAdapter(model_registry) if model_registry else None
        self._tool_adapter = ToolRegistryAdapter(tool_registry) if tool_registry else None
        self._skill_adapter = SkillRegistryAdapter(skill_registry) if skill_registry else None
        self._workspace_adapter = WorkspaceResourceAdapter(workspace) if workspace else None

        # In-memory run telemetry aggregation
        self._run_usage: Dict[str, List[AttemptUsageRecord]] = defaultdict(list)

    def resolve(self, request: FabricRequest) -> FabricResolution:
        """Resolve an incoming FabricRequest against available capabilities and resources."""
        rejection_reasons: list[str] = []
        matched_caps: list[CapabilityDescriptor] = []
        allocated_resources: list[ResourceDescriptor] = []
        selected_tools: list[str] = []
        candidate_models: list[ProviderModelInfo] = []
        candidate_providers: list[str] = []
        selected_provider: Optional[str] = request.explicit_provider
        selected_model: Optional[str] = request.explicit_model

        # 1. Resource Availability Checks
        for req_res in request.required_resources:
            if not req_res.is_available:
                rejection_reasons.append(f"Required resource '{req_res.resource_id}' ({req_res.uri_or_path}) is unavailable")
            else:
                allocated_resources.append(req_res)

        # Include default workspace if available and not explicitly overridden
        if self._workspace_adapter and not any(r.resource_type.value == "WORKSPACE" for r in allocated_resources):
            allocated_resources.append(self._workspace_adapter.to_resource_descriptor())

        # 2. Capability Resolution across distinct domains
        is_capable = True
        is_authorized = True

        # Group required capabilities by domain
        model_reqs: list[CapabilityRequirement] = []
        tool_reqs: list[CapabilityRequirement] = []
        exec_reqs: list[CapabilityRequirement] = []
        task_reqs: list[CapabilityRequirement] = []
        custom_reqs: list[CapabilityRequirement] = []

        for req_cap in request.required_capabilities:
            if req_cap.domain == CapabilityDomain.MODEL:
                model_reqs.append(req_cap)
            elif req_cap.domain == CapabilityDomain.TOOL:
                tool_reqs.append(req_cap)
            elif req_cap.domain == CapabilityDomain.EXECUTION:
                exec_reqs.append(req_cap)
            elif req_cap.domain == CapabilityDomain.TASK:
                task_reqs.append(req_cap)
            elif req_cap.domain == CapabilityDomain.CUSTOM:
                custom_reqs.append(req_cap)

        unmet_capabilities: list[str] = []

        # 2a. Model Domain Resolution
        if request.explicit_model and self._model_registry:
            try:
                model_info = self._model_registry.get_model(request.explicit_model, provider_id=request.explicit_provider)
                selected_provider = model_info.provider_id
                selected_model = model_info.canonical_id
                candidate_models.append(model_info)
                if selected_provider not in candidate_providers:
                    candidate_providers.append(selected_provider)
                if model_info.cost_tier == CostTier.PAID and not request.allow_paid_providers:
                    is_authorized = False
                    rejection_reasons.append(f"Model '{selected_model}' requires paid authorization which is not enabled")
                if self._model_adapter:
                    matched_caps.extend(self._model_adapter.get_capabilities_for_model(model_info))
            except LookupError:
                is_capable = False
                rejection_reasons.append(f"Explicit model '{request.explicit_model}' is not registered in ModelRegistry")
        elif self._model_registry is not None:
            # Resolve model capabilities and/or task category
            parsed_model_caps: list[ModelCapability] = []
            unsupported_caps: list[str] = []
            for req in model_reqs:
                try:
                    parsed_model_caps.append(ModelCapability(req.name.lower()))
                except ValueError:
                    unsupported_caps.append(req.qualified_name)

            if unsupported_caps:
                unmet_capabilities.extend(unsupported_caps)
            else:
                models = self._model_registry.find_models(
                    capabilities=parsed_model_caps if parsed_model_caps else None,
                    task_category=request.task_category,
                    min_context=request.context_tokens if request.context_tokens > 0 else None,
                    max_cost_tier=None if request.allow_paid_providers else CostTier.CHEAP,
                    provider_id=request.explicit_provider,
                )
                if parsed_model_caps and not models:
                    unmet_capabilities.extend(req.qualified_name for req in model_reqs)
                else:
                    for m in models:
                        if m not in candidate_models:
                            candidate_models.append(m)
                        if m.provider_id not in candidate_providers:
                            candidate_providers.append(m.provider_id)
                    for c in parsed_model_caps:
                        matched_caps.append(
                            CapabilityDescriptor(
                                capability_id=f"cap:model:{c.value}",
                                domain=CapabilityDomain.MODEL,
                                name=c.value,
                            )
                        )
                    if not selected_model and candidate_models:
                        selected_model = candidate_models[0].canonical_id
                        selected_provider = candidate_models[0].provider_id

        # 2b. Tool Domain Resolution
        for req in tool_reqs:
            if self._tool_registry and self._tool_registry.contains(req.name):
                selected_tools.append(req.name)
                matched_caps.append(
                    CapabilityDescriptor(
                        capability_id=f"cap:tool:{req.name}",
                        domain=CapabilityDomain.TOOL,
                        name=req.name,
                    )
                )
            else:
                unmet_capabilities.append(req.qualified_name)

        # 2c. Execution Domain Resolution
        for req in exec_reqs:
            try:
                enum_exec = ExecutionCapability(req.name.upper())
                matched_caps.append(
                    CapabilityDescriptor(
                        capability_id=f"cap:execution:{enum_exec.value}",
                        domain=CapabilityDomain.EXECUTION,
                        name=enum_exec.value,
                    )
                )
            except ValueError:
                unmet_capabilities.append(req.qualified_name)

        # 2d. Task & Custom Domains Resolution
        for req in task_reqs:
            matched_caps.append(
                CapabilityDescriptor(
                    capability_id=f"cap:task:{req.name}",
                    domain=CapabilityDomain.TASK,
                    name=req.name,
                )
            )
        for req in custom_reqs:
            matched_caps.append(
                CapabilityDescriptor(
                    capability_id=f"cap:custom:{req.name}",
                    domain=CapabilityDomain.CUSTOM,
                    name=req.name,
                )
            )

        if unmet_capabilities:
            is_capable = False
            rejection_reasons.append(f"Unmet required capabilities: {', '.join(unmet_capabilities)}")

        # 3. Form Execution Boundary (constraints proposal, not authorization authority)
        read_paths = [r.uri_or_path for r in allocated_resources if r.resource_type.value in ("WORKSPACE", "FILE")]
        boundary = ExecutionBoundary(
            allowed_read_paths=tuple(read_paths),
            allow_network=any(r.resource_type.value == "NETWORK" for r in allocated_resources),
            cost_ceiling_usd=0.0 if not request.allow_paid_providers else None,
        )

        reason = (
            f"Successfully resolved request for agent '{request.subject_agent}'"
            if not rejection_reasons
            else f"Failed to resolve request: {'; '.join(rejection_reasons)}"
        )

        return FabricResolution(
            is_capable=is_capable and len(rejection_reasons) == 0,
            is_authorized=is_authorized,
            selected_agent=request.subject_agent,
            selected_provider=selected_provider,
            selected_model=selected_model,
            candidate_providers=tuple(candidate_providers),
            candidate_models=tuple(candidate_models),
            selected_tools=tuple(selected_tools),
            matched_capabilities=tuple(matched_caps),
            allocated_resources=tuple(allocated_resources),
            execution_boundary=boundary,
            rejection_reasons=tuple(rejection_reasons),
            reason=reason,
        )

    def record_usage(
        self,
        run_id: str,
        usage_record: AttemptUsageRecord,
    ) -> None:
        """Record usage telemetry for an attempt within a specific run."""
        if not run_id:
            raise ValueError("run_id must be a non-empty string")
        self._run_usage[run_id].append(usage_record)

    def get_run_accounting(
        self,
        run_id: str,
        task_id: str = "",
        project_id: str = "",
        retry_count: int = 0,
        fallback_events: Sequence[str] = (),
        associated_artifact_ids: Sequence[str] = (),
        total_duration_seconds: float = 0.0,
    ) -> RunAccountingRecord:
        """Generate a complete RunAccountingRecord aggregating all recorded attempt usages."""
        records = self._run_usage.get(run_id, [])
        return UsageAccountingAdapter.create_run_accounting(
            run_id=run_id,
            task_id=task_id,
            project_id=project_id,
            attempt_records=records,
            retry_count=retry_count,
            fallback_events=fallback_events,
            associated_artifact_ids=associated_artifact_ids,
            total_duration_seconds=total_duration_seconds,
        )
