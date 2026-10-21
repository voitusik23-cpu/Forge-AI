"""The Discovery / Clarification service.

One small service owns the conversation in front of the existing pipeline:

    start(goal) -> questions -> answer(...) -> specification(...) -> confirm(...)
                                                                        |
                                              the EXISTING Planner/Dispatcher/Run

It creates no executor, no second run system, and no provider call. The only hard
rule it adds to execution is :meth:`require_confirmed`: anything that plans or
runs on behalf of a discovery session must pass through it, and it refuses unless
the session is ``CONFIRMED`` (which the user reaches either by answering, or by an
explicit ``skip`` followed by ``confirm``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple
import uuid

from app.context.validation import find_forbidden_value_content
from app.discovery.analyzer import (
    AREA_ORDER,
    ESSENTIAL_AREAS,
    LIST_AREAS,
    analyze_goal,
    missing_areas,
)
from app.discovery.models import (
    ClarificationAnswer,
    ClarificationQuestion,
    DiscoveryNotConfirmed,
    DiscoveryNotReady,
    DiscoverySession,
    DiscoveryState,
    DiscoveryStateConflict,
    InvalidClarificationAnswer,
    ProjectSpecification,
    RequiredQuestionSkipped,
    UnknownDiscoverySession,
    redaction_marker,
)
from app.discovery.questions import clarification_questions, question_for_area
from app.discovery.store import DiscoveryStore

__all__ = [
    "ACTIVE_DISCOVERY_STATES",
    "DiscoveryService",
    "apply_specification",
    "value_origins",
]


def value_origins(
    specification: ProjectSpecification,
) -> Tuple[list, list]:
    """Two flat, ordered lists: what the user chose and what was auto-selected.

    The API publishes both, side by side and never merged, so a client can show «вы
    выбрали» and «Forge выбрал за вас» as the different facts they are.
    """

    def as_list(mapping: object) -> list:
        return [
            {"field": str(field_name), "values": list(values)}
            for field_name, values in dict(mapping or {}).items()
            if values
        ]

    return (
        as_list(specification.chosen_values),
        as_list(specification.auto_selected_values),
    )


#: States in which the conversation can still change. A confirmed or cancelled
#: session is final: answers recorded afterwards could change what a run executes
#: after its owner had already agreed to it.
ACTIVE_DISCOVERY_STATES = frozenset(
    {DiscoveryState.NEEDS_CLARIFICATION, DiscoveryState.READY_FOR_PLANNING}
)


def _now() -> str:
    """The timestamp written into a session, in UTC ISO form."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _split_values(text: str) -> Tuple[str, ...]:
    """Split a free-text answer into list items, deterministically.

    Commas, semicolons and newlines all separate items; empty items are dropped
    and each item is stripped, so the stored value does not depend on spacing.
    """

    normalised = str(text or "")
    for separator in ("\n", ";", "·"):
        normalised = normalised.replace(separator, ",")
    items: list[str] = []
    for chunk in normalised.split(","):
        item = " ".join(chunk.split()).strip(" .")
        if item:
            items.append(item)
    return tuple(items)


def apply_specification(
    specification: ProjectSpecification, description: object
) -> str:
    """The task description handed to the existing pipeline.

    The confirmed specification is prepended to the caller's own request, so the
    existing Planner, Dispatcher, router, classifier and provider path receive one
    task description that carries both. This is the whole hand-off: no dispatcher,
    orchestrator, context-assembly or run-store internals are involved, and the
    caller's explicit provider/model selection is untouched.
    """

    text = specification.as_task_text()
    request = str(description or "").strip()
    return f"{text}\n\n{request}" if request else text


class DiscoveryService:
    """Create, advance and confirm discovery sessions."""

    def __init__(self, store: Optional[DiscoveryStore] = None) -> None:
        self._store = store if store is not None else DiscoveryStore()

    # ------------------------------------------------------------------ reads
    @property
    def store(self) -> DiscoveryStore:
        return self._store

    def get(self, session_id: object) -> DiscoverySession:
        """The stored session, or :class:`UnknownDiscoverySession`."""

        session = self._store.read(session_id)
        if session is None:
            raise UnknownDiscoverySession(str(session_id))
        return session

    def specification(self, session_id: object) -> ProjectSpecification:
        """The current specification. Available before confirmation, by design.

        Showing the specification is how the user decides whether to confirm it;
        only *acting* on it requires :meth:`require_confirmed`.
        """

        session = self.get(session_id)
        if session.specification is not None:
            return session.specification
        return self._specification_for(session)

    def require_confirmed(self, session_id: object) -> ProjectSpecification:
        """The confirmed specification, or :class:`DiscoveryNotConfirmed`.

        This is the guard every planning or execution attempt must call. It is the
        only thing this layer adds to the security boundary: an unconfirmed or
        cancelled session can never reach the existing pipeline, and an explicit
        ``skip`` does not bypass it -- ``skip`` moves the session to
        ``READY_FOR_PLANNING``, not to ``CONFIRMED``.
        """

        session = self.get(session_id)
        if session.state is not DiscoveryState.CONFIRMED:
            raise DiscoveryNotConfirmed(
                "the discovery session is not confirmed "
                f"(state: {session.state.value}), so nothing was planned or run"
            )
        return session.specification or self._specification_for(session)

    # ------------------------------------------------------------ transitions
    def start(self, goal: object) -> DiscoverySession:
        """Open a session for one idea and return its first questions."""

        raw_goal = str(goal or "")
        screened_goal, goal_redacted = self._screen(raw_goal)
        # A goal that looks like a credential is not stored at all: the user is
        # asked what should work instead, and an answer fills the field. This is
        # the only way a secret-shaped goal can be handled without writing it.
        initial_goal = "" if goal_redacted else screened_goal.strip()
        created = _now()
        session = DiscoverySession(
            session_id=f"disc-{uuid.uuid4().hex[:12]}",
            initial_goal=initial_goal,
            state=DiscoveryState.NEEDS_CLARIFICATION,
            questions=(),
            answers=(),
            specification=None,
            created_at=created,
            updated_at=created,
        )
        session = self._refresh(session, updated_at=created)
        self._store.write(session)
        return session

    def answer(
        self, session_id: object, answers: Iterable[object]
    ) -> DiscoverySession:
        """Record answers, re-derive, and advance the state when nothing is missing.

        Each item is one of the shapes the product allows:

        * ``{question_id, text}`` -- the user's own words (the original shape);
        * ``{question_id, values: [...]}`` -- the options the user clicked;
        * ``{question_id, values: [...], custom: "..."}`` -- clicked options plus
          «Свой вариант»;
        * ``{question_id, unknown: true}`` -- «Не знаю — выбери оптимальный вариант»:
          the recommended option is recorded and marked ``auto_selected``;
        * ``{question_id, skipped: true}`` -- «Пропустить», allowed only for an
          ``optional`` question. A required one raises :class:`RequiredQuestionSkipped`,
          so a required decision can never disappear silently.
        """

        session = self.get(session_id)
        self._require_active(session)
        incoming = self._coerce_answers(answers, session)
        merged = self._merge_answers(session.answers, incoming)
        updated = replace(
            session,
            answers=merged,
            updated_at=_now(),
            skipped=session.skipped,
        )
        updated = self._refresh(updated)
        self._store.write(updated)
        return updated

    def skip(self, session_id: object, reason: object = None) -> DiscoverySession:
        """Let the user end the clarification round without answering everything.

        The session moves to ``READY_FOR_PLANNING`` with ``skipped=True``. It is
        **not** confirmed: planning and execution still require an explicit
        :meth:`confirm`, which is what the guard checks.
        """

        session = self.get(session_id)
        self._require_active(session)
        screened_reason, _ = self._screen(str(reason or ""))
        updated = replace(
            session,
            skipped=True,
            skip_reason=screened_reason.strip(),
            updated_at=_now(),
        )
        updated = replace(updated, state=DiscoveryState.READY_FOR_PLANNING)
        self._store.write(updated)
        return updated

    def confirm(self, session_id: object) -> DiscoverySession:
        """Freeze the specification for execution.

        Refused while essential areas are still unanswered -- unless the user
        explicitly skipped clarification, in which case ``READY_FOR_PLANNING`` plus
        ``skipped`` is exactly the state they asked for.
        """

        session = self.get(session_id)
        if session.state is DiscoveryState.CONFIRMED:
            return session
        if session.state is DiscoveryState.CANCELLED:
            raise DiscoveryStateConflict(
                "a cancelled discovery session cannot be confirmed"
            )
        if session.state is DiscoveryState.NEEDS_CLARIFICATION:
            raise DiscoveryNotReady(
                "the discovery session still needs answers before it can be "
                "confirmed; answer the questions or skip clarification"
            )
        updated = replace(
            session, state=DiscoveryState.CONFIRMED, updated_at=_now()
        )
        self._store.write(updated)
        return updated

    def cancel(self, session_id: object) -> DiscoverySession:
        """Close the session without planning anything."""

        session = self.get(session_id)
        if session.state is DiscoveryState.CANCELLED:
            return session
        if session.state is DiscoveryState.CONFIRMED:
            raise DiscoveryStateConflict(
                "a confirmed discovery session cannot be cancelled: the spec it "
                "produced may already be in use"
            )
        updated = replace(
            session, state=DiscoveryState.CANCELLED, updated_at=_now()
        )
        self._store.write(updated)
        return updated

    # ---------------------------------------------------------------- internals
    def _require_active(self, session: DiscoverySession) -> None:
        if session.state not in ACTIVE_DISCOVERY_STATES:
            raise DiscoveryStateConflict(
                f"the discovery session is {session.state.value} and no longer "
                "accepts changes"
            )

    def _screen(self, text: str) -> Tuple[str, bool]:
        """Replace secret-shaped text with a marker, recording that it happened.

        The detector is the repository's existing one
        (:func:`app.context.validation.find_forbidden_value_content`), so this layer
        invents no second notion of "looks like a secret".
        """

        value = str(text or "")
        reason = find_forbidden_value_content(value)
        if reason is None:
            return value, False
        return redaction_marker(reason), True

    def _coerce_answers(
        self, answers: object, session: DiscoverySession
    ) -> Tuple[ClarificationAnswer, ...]:
        """Validate and screen incoming answers, whatever shape they arrive in."""

        if answers is None:
            return ()
        if isinstance(answers, (str, bytes)) or isinstance(answers, Mapping):
            raise InvalidClarificationAnswer(
                "answers must be a list of {question_id, text} items"
            )
        coerced: list[ClarificationAnswer] = []
        for item in answers:
            if isinstance(item, ClarificationAnswer):
                coerced.append(item)
                continue
            if not isinstance(item, Mapping):
                raise InvalidClarificationAnswer(
                    "each answer must be a ClarificationAnswer or a mapping"
                )
            question_id = str(item.get("question_id", "") or "")
            field_name = self._field_for_question(question_id)
            if field_name is None:
                raise InvalidClarificationAnswer(
                    f"'{question_id}' is not a question this layer asks"
                )
            question = self._question_for_field(session, field_name)
            skipped = bool(item.get("skipped", False))
            if skipped:
                # A required decision cannot be silently dropped: the user is told to
                # answer it or to let Forge pick the recommended value.
                if question is not None and question.required:
                    raise RequiredQuestionSkipped(question_id)
                coerced.append(
                    ClarificationAnswer(
                        question_id=question_id,
                        text="",
                        skipped=True,
                    )
                )
                continue
            coerced.append(
                self._coerced_answer(
                    question_id=question_id,
                    question=question,
                    item=item,
                )
            )
        return tuple(coerced)

    def _coerced_answer(
        self,
        *,
        question_id: str,
        question: Optional[ClarificationQuestion],
        item: Mapping[str, object],
    ) -> ClarificationAnswer:
        """One mapping answer, with its options resolved to the words it will record."""

        unknown = bool(item.get("unknown", False))
        raw_values = item.get("values") or ()
        if isinstance(raw_values, (str, bytes)):
            raw_values = (raw_values,)
        chosen_values = (
            tuple(
                str(value).strip()
                for value in raw_values
                if str(value).strip()
            )
            if isinstance(raw_values, (list, tuple))
            else ()
        )
        custom = str(item.get("custom", "") or "")
        raw_text = str(item.get("text", "") or "")

        auto_selected = False
        if unknown:
            if question is None:
                raise InvalidClarificationAnswer(
                    f"'{question_id}' offers no options, so it cannot be auto-answered"
                )
            chosen_values = question.recommended_values()
            auto_selected = True
            if not chosen_values:
                # A free-text question has no recommended option, so «Не знаю» is an
                # explicit unknown rather than an invented answer.
                return ClarificationAnswer(
                    question_id=question_id, text="", skipped=True
                )
            labels = [question.label_for(value) for value in chosen_values]
            text = "; ".join(labels)
        elif chosen_values:
            labels = [
                question.label_for(value) if question is not None else value
                for value in chosen_values
            ]
            text = "; ".join(labels)
            if custom.strip():
                text = f"{text}; {custom.strip()}"
        else:
            text = custom.strip() or raw_text

        screened, redacted = self._screen(text)
        screened_custom, custom_redacted = self._screen(custom)
        declined = bool(
            question is not None
            and question.control == "yes_no"
            and chosen_values == ("нет",)
        )
        return ClarificationAnswer(
            question_id=question_id,
            text=screened.strip(),
            skipped=False,
            redacted=redacted or custom_redacted,
            values=chosen_values,
            custom=screened_custom.strip() if not declined else "",
            auto_selected=auto_selected,
            declined=declined,
        )

    def _question_for_field(
        self, session: DiscoverySession, field_name: str
    ) -> Optional[ClarificationQuestion]:
        """The question this session asked for an area, or the canonical one."""

        for question in session.questions:
            if question.field == field_name:
                return question
        return question_for_area(field_name, session.initial_goal)

    @staticmethod
    def _field_for_question(question_id: str) -> Optional[str]:
        if not question_id.startswith("q-"):
            return None
        field_name = question_id[2:]
        return field_name if field_name in AREA_ORDER else None

    @staticmethod
    def _merge_answers(
        existing: Sequence[ClarificationAnswer],
        incoming: Sequence[ClarificationAnswer],
    ) -> Tuple[ClarificationAnswer, ...]:
        """Apply new answers over old ones, keeping the first-seen order.

        Re-answering a question replaces its value rather than duplicating it, so a
        user who mistyped can correct themselves without the session accumulating
        contradictory answers.
        """

        order: list[str] = [answer.question_id for answer in existing]
        by_id = {answer.question_id: answer for answer in existing}
        for answer in incoming:
            if answer.question_id not in by_id:
                order.append(answer.question_id)
            by_id[answer.question_id] = answer
        return tuple(by_id[question_id] for question_id in order)

    def _refresh(
        self, session: DiscoverySession, *, updated_at: str = ""
    ) -> DiscoverySession:
        """Recompute questions, specification and state from the session itself."""

        specification = self._specification_for(session)
        questions = clarification_questions(session.initial_goal, session.answers)
        missing_essential = [
            area for area in AREA_ORDER
            if area in ESSENTIAL_AREAS and not getattr(specification, area, "")
        ]
        state = (
            DiscoveryState.READY_FOR_PLANNING
            if session.skipped or not missing_essential
            else DiscoveryState.NEEDS_CLARIFICATION
        )
        return replace(
            session,
            questions=questions,
            specification=specification,
            state=state,
            updated_at=updated_at or _now(),
        )

    def _specification_for(self, session: DiscoverySession) -> ProjectSpecification:
        """Build the specification: answers first, deterministic derivation second.

        Two records travel beside the values themselves: ``chosen_values`` (what the user
        picked) and ``auto_selected_values`` (what «Не знаю — выбери оптимальный вариант»
        picked for them). They are never merged, so a reader can always tell a decision
        the user made from one Forge made.
        """

        analysis = analyze_goal(session.initial_goal)
        sources: dict[str, str] = {}
        chosen: dict[str, Tuple[str, ...]] = {}
        auto_selected: dict[str, Tuple[str, ...]] = {}
        values: dict[str, Any] = {}
        for area in AREA_ORDER:
            answer = session.answer_for(area)
            answer_text = ""
            if answer is not None:
                sources[area] = answer.text
                question = self._question_for_field(session, area)
                labels = tuple(
                    question.label_for(value) if question is not None else value
                    for value in answer.values
                )
                if answer.skipped or answer.redacted:
                    pass
                elif answer.auto_selected:
                    auto_selected[area] = labels or (answer.text.strip(),)
                elif labels:
                    chosen[area] = (
                        labels + ((answer.custom.strip(),) if answer.custom.strip() else ())
                    )
                elif answer.text.strip():
                    chosen[area] = (answer.text.strip(),)
                if (
                    not answer.skipped
                    and not answer.redacted
                    and not answer.declined
                ):
                    answer_text = answer.text.strip()
            if area == "goal":
                values[area] = session.initial_goal.strip() or analysis.goal
            elif answer_text:
                values[area] = (
                    _split_values(answer_text) if area in LIST_AREAS else answer_text
                )
            else:
                values[area] = analysis.value_for(area)
        return ProjectSpecification(
            goal=str(values.get("goal", "")),
            project_type=str(values.get("project_type", "")),
            users=str(values.get("users", "")),
            core_features=tuple(values.get("core_features") or ()),
            platform=str(values.get("platform", "")),
            integrations=tuple(values.get("integrations") or ()),
            data_storage=str(values.get("data_storage", "")),
            auth=str(values.get("auth", "")),
            notifications=str(values.get("notifications", "")),
            constraints=tuple(values.get("constraints") or ()),
            priorities=tuple(values.get("priorities") or ()),
            acceptance_criteria=tuple(
                values.get("acceptance_criteria") or ()
            ),
            source_answers=sources,
            chosen_values=chosen,
            auto_selected_values=auto_selected,
        )
