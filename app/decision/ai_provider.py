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


def strip_code_fence(output_text: str) -> str:
    """Remove one surrounding markdown code fence from model output.

    Module-level and public because two callers need the *same* unpacking: this
    provider's validation ladder, and the generation stage that validates a whole
    model-produced project. Two copies of this rule would drift, and a difference
    in fence handling is a difference in what counts as valid model output.
    """
    cleaned = output_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            cleaned = "\n".join(lines[1:-1]).strip()
    return cleaned


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
        mock_transport: bool = False,
    ) -> None:
        self._provider = provider or OpenAIProvider()
        self._model_name = model_name or self._provider.model_name
        self._validator = validator or validate_decision
        self._strict_validation = strict_validation
        # Whether the composed transport is an injected/mock one. It grants
        # nothing and changes no behaviour; it travels with the provider so a
        # caller can record *from data* that the text was not produced by a real
        # remote model. Defaulting to False is the honest default: this class
        # does not decide what its provider is, so a caller that composes a mock
        # must say so.
        self.mock_transport = bool(mock_transport)
        self.last_validation_report: DecisionValidationReport | None = None
        # Physical measurement of the most recent provider call. It is recorded
        # so the run loop can measure the attempt; it carries no authority and no
        # money. It stays ``None`` when no provider call produced a response, and
        # a missing measurement is never replaced with a guessed value.
        #
        # These three fields describe **the attempt**, not a producer: they are set
        # as soon as a response arrives, *before* the reply is judged, so a caller
        # can measure a call that produced nothing usable. Reading them directly
        # therefore does NOT prove that a model answered. Use the
        # ``producer_*`` properties below for that question; they withhold the
        # identity unless the reply really produced a validated decision.
        self.last_usage: Usage | None = None
        self.last_provider_name: str = ""
        self.last_model_name: str = ""
        #: Whether the most recent reply produced a usable decision. It is the gate
        #: the ``producer_*`` properties below read, so "a call happened" and "a
        #: model answered" can never be conflated.
        self.last_response_produced_a_decision: bool = False

    @property
    def producer_provider_name(self) -> str:
        """The provider that produced a decision, or ``""`` when none did.

        ``last_provider_name`` is set as soon as a response arrives, so it names a
        transport even for a reply that was rejected as empty or malformed. This
        property answers the different, narrower question a report must ask before
        attributing a result to a model: did a reply actually become a decision?
        """

        if not self.last_response_produced_a_decision:
            return ""
        return self.last_provider_name

    @property
    def producer_model_name(self) -> str:
        """The model that produced a decision, or ``""`` when none did."""

        if not self.last_response_produced_a_decision:
            return ""
        return self.last_model_name

    @property
    def producer_usage(self) -> Usage | None:
        """The measurement of the call that produced a decision, or ``None``.

        A rejected reply has no decision to measure, so no usage is attributable to
        it: a caller cannot add an attempt's tokens to a project that was never
        produced.
        """

        if not self.last_response_produced_a_decision:
            return None
        return self.last_usage

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

    def build_generation_prompt(self, task_specification: Any, acceptance_criteria: Any) -> str:
        """Construct the deterministic prompt that asks for a whole project.

        Additive sibling of :meth:`build_prompt`; that method is unchanged. Like
        it, this composes a prompt and nothing else: it reads no file, runs no
        command, and returns no decision.

        ``task_specification`` and ``acceptance_criteria`` are the operator's own
        objects (a ``TaskSpecification`` and its ``AcceptanceCriterion`` tuple).
        They are rendered as inert text. Text a model produces from this prompt
        remains data: it is parsed, validated, and path-checked before any byte
        reaches a disk, and it carries no execution authority at any point.
        """
        requirements = tuple(getattr(task_specification, "requirements", ()) or ())
        criterion_items = tuple(acceptance_criteria or ())

        lines = [
            "ROLE: You are a Forge AI project author.",
            "GOAL: Produce a complete, self-contained, runnable software project "
            "that satisfies the acceptance criteria below.",
            "AUTHORITY: You have NONE. You cannot execute commands, choose paths, "
            "grant approval, or widen any perimeter. Every path you name is "
            "validated before anything is written.",
            "SAFETY: Never emit private keys, access keys, sign-in values, or "
            "environment files holding them. If a settings template is needed, "
            'name it with an ".example" suffix.',
            "",
            f"TITLE: {getattr(task_specification, 'title', '') or ''}",
            f"DESCRIPTION: {getattr(task_specification, 'description', '') or ''}",
        ]
        if requirements:
            rendered = []
            for requirement in requirements:
                rendered.append(
                    "- "
                    f"{getattr(requirement, 'requirement_id', '')}: "
                    f"{getattr(requirement, 'description', '')}"
                )
            lines.append("REQUIREMENTS:")
            lines.extend(rendered)
        if criterion_items:
            lines.append("ACCEPTANCE CRITERIA (each must hold in the produced project):")
            for criterion in criterion_items:
                lines.append(
                    f"- {getattr(criterion, 'criterion_id', '')}: "
                    f"{getattr(criterion, 'description', '')}"
                )
        lines.extend([
            "",
            "OUTPUT SCHEMA RULES:",
            '- Respond with ONE JSON object shaped exactly as:',
            '  {"decision": {"decision_type": "...", "action": "...", '
            '"reason_code": "...", "rationale": "...", "confidence": 1.0}, '
            '"files": {"<relative/posix/path>": "<entire file body>"}}',
            "- decision.decision_type MUST be one of: CONTINUE, VERIFY, REVISE, "
            "REQUEST_APPROVAL, WAIT, FAIL, COMPLETE",
            "- decision.action MUST be one of: EXECUTE, RUN_VERIFICATION, "
            "REQUEST_REVISION, REQUEST_USER_APPROVAL, WAIT_FOR_APPROVAL, "
            "COMPLETE_RUN, FAIL_RUN",
            '- every key of "files" MUST be a relative path using "/" separators; '
            'absolute paths and ".." segments are refused',
            "- every file body MUST be text, and SHOULD be valid UTF-8",
            "- keep every file small; a single response is bounded in size",
            "",
            "OUTPUT FORMAT:",
            "Respond ONLY with that single JSON object. Do not include markdown "
            "code fences, backticks, or any conversational text.",
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
        self.last_response_produced_a_decision = False

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

        return self._decide_from_text(
            request, getattr(provider_res, "output", ""), in_process_call=True
        )

    @staticmethod
    def _has_usable_text(output_text: Any) -> bool:
        return isinstance(output_text, str) and bool(output_text.strip())

    def decide_from_output(
        self, request: DecisionRequest, output_text: Any
    ) -> Decision:
        """Validate an **already produced** model output as a Decision.

        Additive sibling of :meth:`decide` for a caller that obtained the model
        text through a different, existing route (for example a dispatched task
        whose ``TaskResult.output`` carries it) and must not spend a second
        provider call to have it validated.

        It applies the *same* fail-closed ladder as :meth:`decide` --
        ``empty_model_output``, ``malformed_json``, ``malformed_model_output``,
        ``missing_decision_fields``, ``unknown_decision_type``, ``unknown_action``
        -- and it does not touch the measurement fields (``last_usage``,
        ``last_provider_name``, ``last_model_name``), which belong to whatever
        call really happened, except in the one case where the supplied output is
        empty: then there is no measurement to report and the fields are cleared,
        so a caller cannot read a stale identity.

        It still has zero execution authority: the returned ``Decision`` is a
        recommendation and nothing in this module acts on it.
        """
        return self._decide_from_text(request, output_text, in_process_call=False)

    def _decide_from_text(
        self, request: DecisionRequest, output_text: Any, *, in_process_call: bool
    ) -> Decision:
        """The single fail-closed validation ladder shared by both entry points.

        ``in_process_call`` says whether this validation is consuming the output of
        a provider call this object just made. It matters only for the empty-output
        case: when a caller supplies empty text there is no measurement belonging
        to it, so the fields are cleared rather than left describing an earlier
        call.
        """
        if not self._has_usable_text(output_text):
            if not in_process_call:
                self.last_usage = None
                self.last_provider_name = ""
                self.last_model_name = ""
            # No reply produced a decision, so none may be attributed to a model.
            self.last_response_produced_a_decision = False
            return self._fail_closed_decision(
                request=request,
                reason_code="empty_model_output",
                rationale="Model provider returned empty response",
            )

        cleaned = strip_code_fence(output_text)

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

        # A validated decision was really produced, so -- and only now -- the reply
        # may be attributed to the provider/model that answered.
        self.last_response_produced_a_decision = True
        return decision

    @staticmethod
    def _strip_code_fence(output_text: str) -> str:
        """Backward-compatible alias for the module-level :func:`strip_code_fence`."""
        return strip_code_fence(output_text)

    def _fail_closed_decision(
        self,
        request: DecisionRequest,
        reason_code: str,
        rationale: str,
        *,
        error_type: str | None = None,
        validation_errors: tuple[str, ...] = (),
    ) -> Decision:
        # Every fail-closed reply is, by construction, one that produced no
        # decision, so no caller may attribute it to a model. Setting the gate here
        # covers every reason code on the ladder in one place.
        self.last_response_produced_a_decision = False
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
