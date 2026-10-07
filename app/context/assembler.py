"""Deterministic, read-only assembly of task and Decision Context Envelopes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
import json
from typing import Any
from uuid import uuid4

from app.context.models import (
    ContextFreshness,
    ContextItem,
    ContextSensitivity,
    ContextSource,
    ContextSourceType,
    ContextTrust,
    ContextTrustLevel,
    DecisionContextEnvelope,
    ExecutionContext,
    TraceSummary,
)
from app.context.selector import (
    ContextAssemblyError,
    ContextBudgetPolicy,
    ContextSelector,
    CriticalContextUnfitError,
    InsufficientContextBudgetError,
    SelectionReport,
)
from app.context.validation import (
    MAX_CONTEXT_ITEMS,
    MAX_METADATA_ITEMS,
    MAX_STRING_LENGTH,
    sanitize_context_metadata,
    validate_decision_context,
)
from app.orchestrator.models import Task
from app.skills.models import SkillDefinition


class ContextBudgetExceededError(ContextAssemblyError):
    """Raised when an explicit context set exceeds configured bounds."""

    def __init__(
        self,
        *,
        item_count: int,
        character_count: int,
        max_items: int,
        max_characters: int,
    ) -> None:
        self.item_count = item_count
        self.character_count = character_count
        self.max_items = max_items
        self.max_characters = max_characters
        super().__init__(
            "Execution context exceeds configured budget "
            f"(items {item_count}/{max_items}, characters "
            f"{character_count}/{max_characters})"
        )


class ContextAssembler:
    """Assemble only caller-provided content; never reads files or networks (legacy)."""

    def __init__(
        self,
        *,
        max_items: int = 16,
        max_characters: int = 20_000,
        selector: Optional[ContextSelector] = None,
        budget_policy: Optional[ContextBudgetPolicy] = None,
    ) -> None:
        if isinstance(max_items, bool) or not isinstance(max_items, int) or max_items < 1:
            raise ValueError("max_items must be a positive integer")
        if (
            isinstance(max_characters, bool)
            or not isinstance(max_characters, int)
            or max_characters < 1
        ):
            raise ValueError("max_characters must be a positive integer")
        self.max_items = max_items
        self.max_characters = max_characters
        self._selector = selector
        self._budget_policy = budget_policy

    def assemble(
        self,
        task: Task,
        run_id: str,
        explicit_inputs: Iterable[str | ContextItem] = (),
        *,
        model_info: Any | None = None,
        budget_policy: Optional[ContextBudgetPolicy] = None,
        selector: Optional[ContextSelector] = None,
    ) -> ExecutionContext:
        """Build a bounded context with caller inputs filtered and prioritized through ContextSelector."""
        if not isinstance(task, Task):
            raise ContextAssemblyError("task must be a Task instance")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ContextAssemblyError("run_id must not be empty")

        items = [
            ContextItem(
                id=f"task:{task.id}",
                kind="user_task",
                content=task.description,
                source=ContextSource.USER_TASK,
                trust=ContextTrust.TRUSTED,
                freshness=ContextFreshness.CURRENT,
            )
        ]
        if task.context:
            try:
                task_context_content = json.dumps(
                    task.context, ensure_ascii=False, sort_keys=True
                )
            except (TypeError, ValueError) as exc:
                raise ContextAssemblyError(
                    "task context must contain JSON-compatible values"
                ) from exc
            items.append(
                ContextItem(
                    id=f"task-context:{task.id}",
                    kind="task_context",
                    content=task_context_content,
                    source=ContextSource.USER_TASK,
                    trust=ContextTrust.UNTRUSTED,
                    freshness=ContextFreshness.CURRENT,
                )
            )
        character_count = sum(len(item.content) for item in items)
        self._check_budget(len(items), character_count)
        if isinstance(explicit_inputs, str):
            explicit_inputs = (explicit_inputs,)
        for index, value in enumerate(explicit_inputs, start=1):
            if isinstance(value, str):
                item = ContextItem(
                    id=f"input:{index}",
                    kind="explicit_input",
                    content=value,
                    source=ContextSource.EXPLICIT_INPUT,
                    trust=ContextTrust.UNTRUSTED,
                    freshness=ContextFreshness.UNKNOWN,
                )
            elif isinstance(value, ContextItem):
                item = replace(value, source_type=ContextSourceType.EXPLICIT_INPUT)
            else:
                raise ContextAssemblyError(
                    "explicit inputs must be text or ContextItem values"
                )
            items.append(item)
            character_count += len(item.content)
            self._check_budget(len(items), character_count)
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ContextAssemblyError("context item IDs must be unique")

        eff_policy = budget_policy or (
            ContextBudgetPolicy.from_model_info(
                model_info,
                max_items=self.max_items,
            )
            if model_info is not None
            else (
                self._budget_policy
                or ContextBudgetPolicy(
                    total_context_window=32_768,
                    max_output_tokens=4_096,
                    safety_headroom_tokens=2_048,
                    min_input_budget_tokens=0,
                    max_items=self.max_items,
                    max_item_characters=self.max_characters,
                    allow_truncation=False,
                )
            )
        )
        eff_selector = selector or self._selector or ContextSelector(policy=eff_policy)
        selected_items, _ = eff_selector.select(
            items,
            budget_policy=eff_policy,
            raise_on_critical_unfit=True,
        )

        return ExecutionContext(run_id=run_id, items=tuple(selected_items))

    def _check_budget(self, item_count: int, character_count: int) -> None:
        if item_count > self.max_items or character_count > self.max_characters:
            raise ContextBudgetExceededError(
                item_count=item_count,
                character_count=character_count,
                max_items=self.max_items,
                max_characters=self.max_characters,
            )


class DecisionContextAssembler:
    """Assembles a bounded, deterministic DecisionContextEnvelope from explicit structured state."""

    def __init__(
        self,
        max_context_items: int = MAX_CONTEXT_ITEMS,
        max_metadata_items: int = MAX_METADATA_ITEMS,
        max_string_length: int = MAX_STRING_LENGTH,
        *,
        selector: ContextSelector | None = None,
        budget_policy: ContextBudgetPolicy | None = None,
    ) -> None:
        self.max_context_items = max_context_items
        self.max_metadata_items = max_metadata_items
        self.max_string_length = max_string_length
        self._selector = selector
        self._budget_policy = budget_policy

    def assemble(
        self,
        *,
        run_id: str,
        attempt_number: int,
        task_id: str | None = None,
        task_specification: Any | None = None,
        project_state: Any | None = None,
        verification_results: Iterable[Any] = (),
        acceptance_result: Any | None = None,
        revision_result: Any | None = None,
        run_trace: Any | None = None,
        blocking_conditions: Iterable[str] = (),
        available_actions: Iterable[Any] = (),
        metadata: Mapping[str, object] | None = None,
        context_items: Iterable[ContextItem] = (),
        context_id: str | None = None,
        requirements: Iterable[Any] = (),
        acceptance_criteria: Iterable[Any] = (),
        skills: Iterable[SkillDefinition] = (),
        project_memory: Iterable[Any] = (),
        knowledge: Iterable[Any] = (),
        understanding_snapshot: Any | None = None,
        selector: ContextSelector | None = None,
        budget_policy: ContextBudgetPolicy | None = None,
        model_info: Any | None = None,
    ) -> DecisionContextEnvelope:
        if not run_id or not isinstance(run_id, str):
            raise ContextAssemblyError("run_id must be a non-empty string")
        if attempt_number < 0:
            raise ContextAssemblyError(f"attempt_number cannot be negative: {attempt_number}")

        sanitized_meta = sanitize_context_metadata(metadata)
        cid = context_id or str(uuid4())
        items: list[ContextItem] = []

        # 1. Task specification / identity / requirements
        eff_task_id = task_id
        reqs_summary: dict[str, object] = {}
        reqs: Iterable[Any] = ()
        if task_specification is not None:
            eff_task_id = eff_task_id or getattr(task_specification, "task_id", None)
            reqs = getattr(task_specification, "requirements", ()) or ()
        if not reqs and requirements:
            reqs = tuple(requirements)
        if reqs:
            req_ids = tuple(
                getattr(r, "requirement_id", None) or getattr(r, "id", None) or str(r)
                for r in reqs
            )
            reqs_summary = {"count": len(reqs), "requirement_ids": list(req_ids)}
            for r in reqs:
                rid = getattr(r, "requirement_id", None) or getattr(r, "id", None) or str(r)
                rdesc = getattr(r, "description", str(r))
                items.append(
                    ContextItem(
                        item_id=f"requirement:{rid}",
                        item_type="requirement",
                        source_type=ContextSourceType.REQUIREMENT,
                        value=f"{rid}:{rdesc}",
                        trust_level=ContextTrustLevel.VERIFIED,
                        source_id=rid,
                    )
                )

        # 2. ProjectState
        ps_status = None
        if project_state is not None:
            ps_status = project_state.status.value if hasattr(project_state.status, "value") else str(project_state.status)
            items.append(
                ContextItem(
                    item_id=f"project_state:{run_id}:{attempt_number}",
                    item_type="project_state",
                    source_type=ContextSourceType.PROJECT_STATE,
                    value=f"status={ps_status},snapshot={getattr(project_state, 'snapshot_id', None)},changeset={getattr(project_state, 'changeset_id', None)}",
                    trust_level=ContextTrustLevel.VERIFIED,
                    source_id=f"run:{run_id}",
                )
            )

        # 3. Acceptance summary
        acc_summary: dict[str, object] = {}
        if acceptance_result is not None:
            a_status = acceptance_result.status.value if hasattr(acceptance_result.status, "value") else str(acceptance_result.status)
            acc_summary = {
                "status": a_status,
                "code": getattr(acceptance_result, "code", ""),
            }
            items.append(
                ContextItem(
                    item_id=f"acceptance:{run_id}:{attempt_number}",
                    item_type="acceptance_result",
                    source_type=ContextSourceType.ACCEPTANCE,
                    value=f"status={a_status},code={getattr(acceptance_result, 'code', '')}",
                    trust_level=ContextTrustLevel.VERIFIED,
                    source_id=f"run:{run_id}",
                )
            )
        elif acceptance_criteria:
            crit_list = list(acceptance_criteria)
            crit_ids = [
                getattr(c, "criterion_id", None) or getattr(c, "id", None) or str(c)
                for c in crit_list
            ]
            acc_summary = {"criteria_count": len(crit_ids), "criteria_ids": crit_ids}
            for c in crit_list:
                cid_val = getattr(c, "criterion_id", None) or getattr(c, "id", None) or str(c)
                cdesc = getattr(c, "description", str(c))
                items.append(
                    ContextItem(
                        item_id=f"acceptance_criterion:{cid_val}",
                        item_type="acceptance_criterion",
                        source_type=ContextSourceType.ACCEPTANCE,
                        value=f"{cid_val}:{cdesc}",
                        trust_level=ContextTrustLevel.VERIFIED,
                        source_id=cid_val,
                    )
                )

        # 4. Verification summary
        ver_summary: dict[str, object] = {}
        ver_list = list(verification_results)
        if ver_list:
            passed = sum(
                1
                for v in ver_list
                if hasattr(v, "status")
                and str(getattr(v.status, "value", v.status)).lower() == "pass"
            )
            failed = sum(
                1
                for v in ver_list
                if hasattr(v, "status")
                and str(getattr(v.status, "value", v.status)).lower() == "fail"
            )
            summary_status = "pass" if failed == 0 and passed > 0 else ("fail" if failed > 0 else "unknown")
            ver_summary = {
                "total": len(ver_list),
                "passed": passed,
                "failed": failed,
                "status": summary_status,
            }
            items.append(
                ContextItem(
                    item_id=f"verification:{run_id}:{attempt_number}",
                    item_type="verification_summary",
                    source_type=ContextSourceType.VERIFICATION,
                    value=f"passed={passed},failed={failed},total={len(ver_list)}",
                    trust_level=ContextTrustLevel.VERIFIED,
                    source_id=f"run:{run_id}",
                )
            )

        # 5. Revision summary
        rev_summary: dict[str, object] = {}
        if revision_result is not None:
            rev_status = revision_result.status.value if hasattr(revision_result.status, "value") else str(revision_result.status)
            rev_summary = {
                "status": rev_status,
                "attempt_number": getattr(revision_result, "attempt_number", attempt_number),
            }
            items.append(
                ContextItem(
                    item_id=f"revision:{run_id}:{attempt_number}",
                    item_type="revision_state",
                    source_type=ContextSourceType.REVISION,
                    value=f"status={rev_status},attempt={rev_summary['attempt_number']}",
                    trust_level=ContextTrustLevel.VERIFIED,
                    source_id=f"run:{run_id}",
                )
            )

        # 6. Trace summary (bounded)
        trace_summary_obj: TraceSummary | None = None
        if run_trace is not None and hasattr(run_trace, "events"):
            evs = run_trace.events
            latest_types = tuple(e.event_type.value for e in evs[-5:])
            latest_seq = evs[-1].sequence_number if evs else None
            latest_dec = next((e.decision_id for e in reversed(evs) if getattr(e, "decision_id", None)), None)
            latest_ver = next((e.verification_id for e in reversed(evs) if getattr(e, "verification_id", None)), None)
            latest_acc = next((e.criterion_id for e in reversed(evs) if getattr(e, "criterion_id", None)), None)
            trace_summary_obj = TraceSummary(
                event_count=len(evs),
                latest_event_types=latest_types,
                latest_sequence_number=latest_seq,
                latest_decision_reference=latest_dec,
                latest_verification_reference=latest_ver,
                latest_acceptance_reference=latest_acc,
            )
            items.append(
                ContextItem(
                    item_id=f"trace_summary:{run_id}:{attempt_number}",
                    item_type="trace_summary",
                    source_type=ContextSourceType.RUN_TRACE,
                    value=f"events={len(evs)},latest_seq={latest_seq}",
                    trust_level=ContextTrustLevel.VERIFIED,
                    source_id=f"run:{run_id}",
                )
            )

        # 7. Blocking conditions
        cond_tuple = tuple(sorted(str(c) for c in blocking_conditions))
        if cond_tuple:
            items.append(
                ContextItem(
                    item_id=f"blocking_conditions:{run_id}:{attempt_number}",
                    item_type="blocking_conditions",
                    source_type=ContextSourceType.SYSTEM_POLICY,
                    value=",".join(cond_tuple),
                    trust_level=ContextTrustLevel.CONFIRMED,
                    source_id=f"run:{run_id}",
                )
            )

        # 8. Available actions
        acts_tuple = tuple(str(a.value if hasattr(a, "value") else a) for a in available_actions)

        # 9. Additional user-supplied context items
        for ci in context_items:
            items.append(ci)

        # 10. Active Skills (advisory procedural guidance)
        for skill in skills:
            if not isinstance(skill, SkillDefinition):
                continue
            manifest = skill.manifest
            skill_id = str(manifest.skill_id)
            name = str(manifest.name or skill_id)
            trust_level = manifest.trust_level
            trust_val = trust_level.value if hasattr(trust_level, "value") else str(trust_level or "UNTRUSTED")

            if trust_val == "BUILTIN":
                ctx_trust = ContextTrustLevel.VERIFIED
            elif trust_val == "LOCAL":
                ctx_trust = ContextTrustLevel.CONFIRMED
            else:
                ctx_trust = ContextTrustLevel.UNVERIFIED

            instructions = str(skill.instructions or "").strip()
            # Bounded procedural guidance (max 1000 characters)
            bounded_instructions = instructions[:1000]
            skill_payload = {
                "name": name,
                "skill_id": skill_id,
                "guidance": bounded_instructions,
            }
            items.append(
                ContextItem(
                    item_id=f"skill:{skill_id}",
                    item_type="skill",
                    source_type=ContextSourceType.SKILL,
                    value=json.dumps(skill_payload, ensure_ascii=False),
                    trust_level=ctx_trust,
                    source_id=f"skill:{skill_id}",
                    sensitivity=ContextSensitivity.INTERNAL,
                )
            )

        # 11. Project Memory (bounded informational context)
        for mem in project_memory:
            mem_id = getattr(mem, "memory_id", str(uuid4()))
            cat = getattr(mem, "category", "")
            cat_val = cat.value if hasattr(cat, "value") else str(cat)
            title = str(getattr(mem, "title", ""))[:120]
            content = str(getattr(mem, "content", ""))[:1000]
            prov = getattr(mem, "provenance", None)
            prov_dict = prov.to_dict() if hasattr(prov, "to_dict") else {}
            trust = getattr(mem, "trust_level", ContextTrustLevel.CONFIRMED)

            mem_payload = {
                "memory_id": mem_id,
                "category": cat_val,
                "title": title,
                "content": content,
                "provenance": prov_dict,
            }
            items.append(
                ContextItem(
                    item_id=f"memory:{mem_id}",
                    item_type="project_memory",
                    source_type=ContextSourceType.PROJECT_MEMORY,
                    value=json.dumps(mem_payload, ensure_ascii=False),
                    trust_level=trust if isinstance(trust, ContextTrustLevel) else ContextTrustLevel.CONFIRMED,
                    source_id=f"memory:{mem_id}",
                    sensitivity=ContextSensitivity.INTERNAL,
                )
            )

        # 12. Governed Forge Knowledge (advisory context)
        for k_item in knowledge:
            k_id = getattr(k_item, "knowledge_id", str(uuid4()))
            cat = getattr(k_item, "category", "")
            cat_val = cat.value if hasattr(cat, "value") else str(cat)
            statement = str(getattr(k_item, "statement", ""))[:240]
            rationale = str(getattr(k_item, "rationale", ""))[:1000]
            scope = getattr(k_item, "scope", "")
            scope_val = scope.value if hasattr(scope, "value") else str(scope)
            app_obj = getattr(k_item, "applicability", None)
            app_dict = app_obj.to_dict() if hasattr(app_obj, "to_dict") else {}
            non_app_obj = getattr(k_item, "non_applicability", None)
            non_app_dict = non_app_obj.to_dict() if hasattr(non_app_obj, "to_dict") else {}

            k_payload = {
                "knowledge_id": k_id,
                "scope": scope_val,
                "category": cat_val,
                "statement": statement,
                "rationale": rationale,
                "applicability": app_dict,
                "non_applicability": non_app_dict,
            }
            items.append(
                ContextItem(
                    item_id=f"knowledge:{k_id}",
                    item_type="forge_knowledge",
                    source_type=ContextSourceType.FORGE_KNOWLEDGE,
                    value=json.dumps(k_payload, ensure_ascii=False),
                    trust_level=ContextTrustLevel.CONFIRMED,
                    source_id=f"knowledge:{k_id}",
                    sensitivity=ContextSensitivity.INTERNAL,
                )
            )

        # 13. Project Understanding (compact architectural summary)
        if understanding_snapshot is not None:
            u_id = getattr(understanding_snapshot, "snapshot_id", str(uuid4()))
            if hasattr(understanding_snapshot, "facts") and hasattr(understanding_snapshot, "topology"):
                try:
                    from app.understanding.summary import summarize_snapshot

                    u_summary = summarize_snapshot(understanding_snapshot)
                    u_payload = json.dumps(u_summary, ensure_ascii=False, sort_keys=True)
                except Exception:
                    u_payload = json.dumps(
                        {"snapshot_id": u_id, "summary": str(understanding_snapshot)[:1000]},
                        ensure_ascii=False,
                    )
            elif isinstance(understanding_snapshot, dict):
                u_payload = json.dumps(understanding_snapshot, ensure_ascii=False, sort_keys=True)
            else:
                u_payload = str(understanding_snapshot)[:1000]

            items.append(
                ContextItem(
                    item_id=f"understanding:{u_id}",
                    item_type="project_understanding",
                    source_type=ContextSourceType.PROJECT_UNDERSTANDING,
                    value=u_payload,
                    trust_level=ContextTrustLevel.VERIFIED,
                    source_id=f"understanding:{u_id}",
                    sensitivity=ContextSensitivity.INTERNAL,
                )
            )

        # Selection & Budget Packing
        has_selector = (
            selector is not None
            or self._selector is not None
            or budget_policy is not None
            or self._budget_policy is not None
            or model_info is not None
        )

        if has_selector:
            eff_policy = budget_policy or (
                ContextBudgetPolicy.from_model_info(model_info)
                if model_info is not None
                else (
                    self._budget_policy
                    or ContextBudgetPolicy(
                        max_items=self.max_context_items,
                        max_item_characters=self.max_string_length,
                    )
                )
            )
            eff_selector = selector or self._selector or ContextSelector(policy=eff_policy)
            selected_items, selection_report = eff_selector.select(items, budget_policy=eff_policy)
        else:
            if len(items) > self.max_context_items:
                raise ContextBudgetExceededError(
                    item_count=len(items),
                    character_count=sum(len(it.value) for it in items),
                    max_items=self.max_context_items,
                    max_characters=self.max_context_items * self.max_string_length,
                )
            selected_items = items
            eff_policy = ContextBudgetPolicy(
                max_items=self.max_context_items,
                max_item_characters=self.max_string_length,
            )
            eff_selector = ContextSelector(policy=eff_policy)
            _, selection_report = eff_selector.select(items, budget_policy=eff_policy)

        envelope = DecisionContextEnvelope(
            context_id=cid,
            run_id=run_id,
            attempt_number=attempt_number,
            task_id=eff_task_id,
            project_state_status=ps_status,
            requirements_summary=reqs_summary,
            acceptance_summary=acc_summary,
            verification_summary=ver_summary,
            revision_summary=rev_summary,
            blocking_conditions=cond_tuple,
            available_actions=acts_tuple,
            trace_summary=trace_summary_obj,
            context_items=tuple(selected_items),
            metadata=sanitized_meta,
            project_state=project_state,
            selection_report=selection_report,
        )

        report = validate_decision_context(envelope)
        if not report.valid:
            raise ContextAssemblyError(f"Envelope validation failed: {', '.join(report.errors)}")

        return envelope
