"""Advisory AI Decision Provider for Forge AI Run Control.

Architectural Rule:
AI recommendation != authorization != execution.

The AI Decision Provider produces purely advisory control-flow recommendations.
It has ZERO execution authority, cannot execute tools or spawn subprocesses,
cannot modify ProjectState, and strictly preserves all security, permission,
and approval boundaries.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import re
from typing import Any, Optional
from uuid import uuid4

from app.agents.providers.base import (
    Provider,
    ProviderError,
    ProviderRequest,
    ProviderResponse,
    Usage,
)
from app.agents.providers.openai import OpenAIProvider
from app.decision.models import (
    Decision,
    DecisionAction,
    DecisionRequest,
    DecisionType,
    FORBIDDEN_DECISION_METADATA_SUBSTRINGS,
    sanitize_decision_metadata,
)
from app.decision.validator import (
    DecisionValidationReport,
    validate_decision,
)
from app.projects.state import ProjectState


class AIDecisionProvider:
    """Advisory, bounded AI Decision Provider coordinating with the Decision Layer.

    Composes with an underlying model Provider (defaulting to OpenAIProvider) to
    produce typed control-flow recommendations. Fails closed safely upon any model,
    network, parsing, or validation error.
    """

    def __init__(
        self,
        provider: Provider | None = None,
        *,
        model_name: str | None = None,
        validator: (
            Callable[[Decision, DecisionRequest, ProjectState | None], DecisionValidationReport]
            | None
        ) = None,
        strict_validation: bool = False,
    ) -> None:
        self._provider = provider or OpenAIProvider()
        self._model_name = model_name or self._provider.model_name
        self._validator = validator or validate_decision
        self._strict_validation = strict_validation
        self.last_validation_report: DecisionValidationReport | None = None
        # Physical measurement of the most recent provider call. It is recorded
        # so the run loop can measure the attempt; it carries no authority and no
        # money. It stays ``None`` when no provider call produced a response, and
        # a missing measurement is never replaced with a guessed value.
        self.last_usage: Usage | None = None
        self.last_provider_name: str = ""
        self.last_model_name: str = ""

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def build_prompt(self, request: DecisionRequest) -> str:
        """Construct a minimal, deterministic, secret-free prompt from the DecisionRequest."""
        run_id = request.run_id
        attempt_number = request.attempt_number if request.attempt_number is not None else 0
        task_id = request.task_id or ""

        ps = request.current_project_state
        project_state_status = (
            ps.status.value
            if ps and hasattr(ps, "status") and hasattr(ps.status, "value")
            else (str(ps.status) if ps and hasattr(ps, "status") else "INITIAL")
        )
        acceptance_status = request.acceptance_status
        if not acceptance_status and ps and getattr(ps, "acceptance_status", None):
            acceptance_status = str(ps.acceptance_status)
        if not acceptance_status:
            acceptance_status = "none"

        verification_status = request.verification_status_summary
        if not verification_status:
            verification_status = "none"

        conditions = sorted(str(c) for c in request.blocking_conditions)
        actions = (
            sorted(a.value if hasattr(a, "value") else str(a) for a in request.available_actions)
            if request.available_actions
            else [
                "EXECUTE",
                "RUN_VERIFICATION",
                "REQUEST_REVISION",
                "REQUEST_USER_APPROVAL",
                "WAIT_FOR_APPROVAL",
                "COMPLETE_RUN",
                "FAIL_RUN",
            ]
        )

        safe_items: list[dict[str, str]] = []
        if request.context_envelope and hasattr(request.context_envelope, "context_items"):
            for item in request.context_envelope.context_items:
                sens = getattr(item, "sensitivity", None)
                sens_val = sens.value if hasattr(sens, "value") else str(sens)
                if sens_val in ("CONFIDENTIAL", "RESTRICTED"):
                    continue
                val = str(getattr(item, "value", ""))
                val_lower = val.lower()
                if any(bad in val_lower for bad in FORBIDDEN_DECISION_METADATA_SUBSTRINGS):
                    continue
                safe_items.append({
                    "item_id": getattr(item, "item_id", ""),
                    "item_type": getattr(item, "item_type", ""),
                    "content": val[:2000],
                })

        safe_items.sort(key=lambda x: x["item_id"])

        lines = [
            "ROLE: You are the Forge AI Advisory Run Controller.",
            "GOAL: Recommend the single next control-flow Decision for the Engineering Run.",
            "AUTHORITY: You have ADVISORY recommendation authority ONLY. You cannot execute tools, bypass permissions, or modify state.",
            "",
            "CURRENT RUN CONTEXT:",
            f"- Run ID: {run_id}",
            f"- Attempt Number: {attempt_number}",
            f"- Task ID: {task_id}",
            f"- Project State Status: {project_state_status}",
            f"- Acceptance Status: {acceptance_status}",
            f"- Verification Status: {verification_status}",
            f"- Blocking Conditions: {json.dumps(conditions, ensure_ascii=False)}",
            f"- Available Actions: {json.dumps(actions, ensure_ascii=False)}",
        ]

        if safe_items:
            lines.append(
                f"- Context Items: {json.dumps(safe_items, ensure_ascii=False, sort_keys=True)}"
            )

        lines.extend([
            "",
            "DECISION SCHEMA RULES:",
            "- decision_type MUST be one of: CONTINUE, VERIFY, REVISE, REQUEST_APPROVAL, WAIT, FAIL, COMPLETE",
            "- action MUST be one of: EXECUTE, RUN_VERIFICATION, REQUEST_REVISION, REQUEST_USER_APPROVAL, WAIT_FOR_APPROVAL, COMPLETE_RUN, FAIL_RUN",
            "- reason_code: short identifier string (e.g. 'execution_required', 'verification_required')",
            "- rationale: concise explanation",
            "- confidence: float between 0.0 and 1.0",
            "",
            "OUTPUT FORMAT:",
            "Respond ONLY with a single JSON object matching this schema. Do not include markdown code fences, backticks, or any conversational text.",
            '{"decision_type": "...", "action": "...", "reason_code": "...", "rationale": "...", "confidence": 1.0}',
        ])

        return "\n".join(lines)

    def decide(self, request: DecisionRequest) -> Decision:
        """Query the model provider and return a strictly validated Decision recommendation.

        Fails closed on any error, invalid payload, or unknown action.
        """
        prompt = self.build_prompt(request)
        provider_req = ProviderRequest(prompt=prompt, model_name=self._model_name)

        # Each decision is one physical provider call. Clear the previous
        # measurement first so a failure never reports the earlier call's tokens.
        self.last_usage = None
        self.last_provider_name = ""
        self.last_model_name = ""

        try:
            provider_res = self._provider.generate(provider_req)
        except Exception as exc:
            return self._fail_closed_decision(
                request=request,
                reason_code="provider_failure",
                rationale="Model provider execution failed safely",
                error_type=type(exc).__name__,
            )

        self.last_usage = getattr(provider_res, "usage", None)
        self.last_provider_name = str(
            getattr(provider_res, "provider_name", "") or self.provider_name
        )
        self.last_model_name = str(
            getattr(provider_res, "model_name", "") or self.model_name
        )

        output_text = getattr(provider_res, "output", "")
        if not isinstance(output_text, str) or not output_text.strip():
            return self._fail_closed_decision(
                request=request,
                reason_code="empty_model_output",
                rationale="Model provider returned empty response",
            )

        cleaned = output_text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                cleaned = "\n".join(lines[1:-1]).strip()

        try:
            parsed = json.loads(cleaned)
        except Exception:
            return self._fail_closed_decision(
                request=request,
                reason_code="malformed_json",
                rationale="Model output could not be parsed as JSON",
            )

        if not isinstance(parsed, dict):
            return self._fail_closed_decision(
                request=request,
                reason_code="malformed_model_output",
                rationale="Parsed model output is not a JSON object",
            )

        raw_type = parsed.get("decision_type")
        raw_action = parsed.get("action")

        if not raw_type or not raw_action:
            return self._fail_closed_decision(
                request=request,
                reason_code="missing_decision_fields",
                rationale="Model output missing decision_type or action",
            )

        decision_type = DecisionType._missing_(raw_type)
        if decision_type is None:
            return self._fail_closed_decision(
                request=request,
                reason_code="unknown_decision_type",
                rationale=f"Unknown decision_type '{raw_type}'",
            )

        action = DecisionAction._missing_(raw_action)
        if action is None:
            return self._fail_closed_decision(
                request=request,
                reason_code="unknown_action",
                rationale=f"Unknown action '{raw_action}'",
            )

        reason_code = str(parsed.get("reason_code", "")).strip() or "ai_recommendation"
        rationale = str(parsed.get("rationale", "")).strip() or reason_code
        try:
            confidence = float(parsed.get("confidence", 1.0))
        except (ValueError, TypeError):
            confidence = 1.0

        decision = Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            decision_type=decision_type,
            action=action,
            reason_code=reason_code,
            rationale=rationale,
            confidence=confidence,
            references={
                "source": "ai_decision_provider",
                "provider_name": self.provider_name,
                "model_name": self.model_name,
                "context_id": request.context_id
                or (
                    request.context_envelope.context_id
                    if request.context_envelope
                    else None
                ),
                "context_fingerprint": request.context_fingerprint
                or (
                    request.context_envelope.context_fingerprint
                    if request.context_envelope
                    else None
                ),
            },
        )

        val_report = self._validator(decision, request, request.current_project_state)
        self.last_validation_report = val_report

        if self._strict_validation and not val_report.valid:
            return self._fail_closed_decision(
                request=request,
                reason_code="decision_validation_failed",
                rationale=f"AI decision failed validation: {', '.join(val_report.errors)}",
                validation_errors=val_report.errors,
            )

        return decision

    def _fail_closed_decision(
        self,
        request: DecisionRequest,
        reason_code: str,
        rationale: str,
        *,
        error_type: str | None = None,
        validation_errors: tuple[str, ...] = (),
    ) -> Decision:
        refs: dict[str, object] = {
            "source": "ai_decision_provider",
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "fail_closed": True,
        }
        if error_type:
            refs["error_type"] = error_type
        if validation_errors:
            refs["validation_errors"] = list(validation_errors)
        if request.context_id:
            refs["context_id"] = request.context_id
        if request.context_fingerprint:
            refs["context_fingerprint"] = request.context_fingerprint

        return Decision(
            decision_id=str(uuid4()),
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            decision_type=DecisionType.FAIL,
            action=DecisionAction.FAIL_RUN,
            reason_code=reason_code,
            rationale=rationale,
            confidence=0.0,
            references=refs,
        )
