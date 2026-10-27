"""Optional model-written questions -- **off by default**, and never required.

Why this module exists
----------------------

The deterministic banks in :mod:`app.discovery.banks` already ask about the task's own
domain, and they are the only path a session needs. This module is the opt-in refinement
the owner asked for *without* handing the questions to a model by default: when he turns
on «Вопросы по смыслу задачи (через модель)» in «Настройки», a ready non-mock provider is
asked for up to three questions written for this exact idea, and they are merged **before**
the bank's own.

The rules that make it safe to enable:

* **off by default.** ``questions_from_model`` is ``False`` unless the owner turns it on,
  and the composed assistant asks the settings on every call;
* **a ready, non-mock provider only.** The offline provider is never called, and a
  provider the composition did not enable or whose key is absent is never called;
* **strict JSON, validated.** A question without options, about a field already answered,
  or outside the domain's own bank (the relevance guard) is dropped -- the model cannot
  smuggle a generic question into a domain's card;
* **never blocking.** Any failure -- no provider, mock provider, a network error, a
  timeout, malformed JSON, a rejected question -- silently falls back to the deterministic
  bank. The session never fails because the optional path did;
* **cached per (idea, domain)** in memory, so one session does not spend tokens on every
  refresh.

Tests use a fake provider; nothing here reaches the network by itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from app.discovery.banks import is_relevant, normalise_question_text
from app.discovery.domain import domain_label
from app.discovery.models import (
    CONTROL_KINDS,
    ClarificationOption,
    ClarificationQuestion,
)

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MODEL_QUESTION_LIMIT",
    "PROMPT_TEMPLATE",
    "ModelQuestionAssistant",
    "model_question_prompt",
    "parse_model_questions",
]


#: The most questions the optional path may contribute. Three: the deterministic bank
#: still owns the rest of the round, and a model writing more would undo the fix.
MODEL_QUESTION_LIMIT = 3

#: How long one optional call may take. The provider's own request timeout is usually
#: larger; this bound is what keeps a slow service from delaying the questions card.
DEFAULT_TIMEOUT_SECONDS = 8.0

#: How much of the idea the prompt carries. A pasted document is not a question source.
MAX_PROMPT_IDEA_CHARACTERS = 2000

#: The strict shape the model has to answer in. Everything outside it is discarded.
PROMPT_TEMPLATE = """Ты помогаешь уточнить задачу перед разработкой.
Задача человека:
{idea}

Тип задачи: {domain}.

Задай не больше {limit} вопросов, ответы на которые действительно меняют объём
работы. Не спрашивай то, что уже сказано в задаче. Пиши по-русски, коротко, с
конкретными вариантами ответа.

Верни СТРОГО JSON без пояснений и без markdown:
{{"questions": [{{"id": "короткий-идентификатор", "text": "вопрос?", "field": "поле", "why": "зачем это решение", "relevance": "почему это важно именно для такой задачи", "control": "radio", "options": [{{"value": "значение", "label": "что видит человек", "recommended": true}}]}}]}}

В каждом вопросе должен быть хотя бы один вариант ответа и ровно один
"recommended": true. "control" — один из: radio, checkbox, select, yes_no.
"""

#: One entry per (idea hash, domain). Bounded: the cache is a courtesy, not a record.
_CACHE_LIMIT = 64
_CACHE: Dict[Tuple[str, str], Tuple[ClarificationQuestion, ...]] = {}
_CACHE_LOCK = threading.Lock()

_FIELD_PATTERN = re.compile(r"[^a-z0-9_-]+")


def _idea_key(idea: object) -> str:
    """A stable, short key for one idea -- case and spacing do not matter."""

    normalised = " ".join(str(idea or "").casefold().split())
    return hashlib.sha256(normalised.encode("utf-8", "replace")).hexdigest()[:24]


def model_question_prompt(idea: object, domain: object) -> str:
    """The prompt one optional call receives. Deterministic and bounded."""

    text = str(idea or "").strip()[:MAX_PROMPT_IDEA_CHARACTERS]
    return PROMPT_TEMPLATE.format(
        idea=text,
        domain=domain_label(domain),
        limit=MODEL_QUESTION_LIMIT,
    )


def _field_from(value: object) -> str:
    """A question field as a safe id: lower-case Latin, digits, ``-`` and ``_``."""

    cleaned = _FIELD_PATTERN.sub("_", str(value or "").strip().lower()).strip("_")
    return cleaned[:64]


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _options_from(raw: object) -> Tuple[ClarificationOption, ...]:
    """The clickable options of one model question; several recommendations collapse.

    A question without a single usable option is not a question this product can show,
    so it is rejected by the caller: a bare text box is exactly the generic behaviour
    this layer is replacing.
    """

    if not isinstance(raw, list):
        return ()
    options: list[ClarificationOption] = []
    recommended_seen = False
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        value = str(item.get("value", "") or "").strip()
        label = str(item.get("label", "") or "").strip() or value
        if not value or not label:
            continue
        recommended = bool(item.get("recommended", False)) and not recommended_seen
        recommended_seen = recommended_seen or recommended
        options.append(
            ClarificationOption(value=value, label=label, recommended=recommended)
        )
    if not options:
        return ()
    if not recommended_seen:
        # Exactly one honest default: the first option, marked as the default.
        options[0] = ClarificationOption(
            value=options[0].value, label=options[0].label, recommended=True
        )
    return tuple(options[:12])


def _control_from(raw: object, options: Sequence[ClarificationOption]) -> str:
    control = str(raw or "").strip().lower()
    if control == "yes_no" and len(options) != 2:
        return "radio"
    if control in CONTROL_KINDS:
        return control
    return "radio" if len(options) <= 2 else "checkbox"


def _strip_code_fence(text: str) -> str:
    """Drop a ```json fence if the model wrapped its JSON in one."""

    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if body.lower().startswith("json"):
        body = body[4:]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def parse_model_questions(
    output: object,
    *,
    domain: object,
    answered_fields: Sequence[str] = (),
    limit: int = MODEL_QUESTION_LIMIT,
) -> Tuple[ClarificationQuestion, ...]:
    """Validate one model answer into at most ``limit`` usable questions.

    Every rejection is silent and local: malformed JSON, a missing ``questions`` list, a
    question without options, a question about a field that was already answered, a
    duplicate field or text, and a question the domain's own bank does not recognise.
    Returning an empty tuple is the normal answer to a model that did not follow the
    shape, and it is always better than showing an unvalidated question.
    """

    text = _strip_code_fence(str(output or ""))
    if not text:
        return ()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return ()
    if not isinstance(data, Mapping):
        return ()
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, list):
        return ()

    answered = {str(field).strip() for field in answered_fields if str(field).strip()}
    accepted: list[ClarificationQuestion] = []
    seen_fields: set[str] = set()
    seen_texts: set[str] = set()
    for item in raw_questions:
        if not isinstance(item, Mapping):
            continue
        question_text = str(item.get("text", "") or "").strip()
        field = _field_from(item.get("field", "") or item.get("id", ""))
        if not question_text or not field:
            continue
        if field in answered or field in seen_fields:
            continue
        text_key = normalise_question_text(question_text)
        if not text_key or text_key in seen_texts:
            continue
        options = _options_from(item.get("options"))
        if not options:
            # No options means a bare text box: rejected by the same schema rule the
            # product applies to its own questions.
            continue
        control = _control_from(item.get("control"), options)
        relevance = str(item.get("relevance", "") or "").strip()
        why = str(item.get("why", "") or "").strip()
        question = ClarificationQuestion(
            id=f"q-{field}",
            text=question_text,
            field=field,
            why=why or relevance or "Ответ меняет объём первой версии.",
            required=False,
            control=control,
            options=options,
            allow_custom=control != "yes_no",
            allow_unknown=True,
            relevance=relevance or why or "Модель считает, что это решение меняет объём работы.",
        )
        if not is_relevant(question, domain):
            # The relevance guard: a question outside the domain's own bank is exactly
            # the «не по теме» the owner complained about.
            continue
        accepted.append(question)
        seen_fields.add(field)
        seen_texts.add(text_key)
        if len(accepted) >= max(0, int(limit)):
            break
    return tuple(accepted)


def _is_mock(provider: object) -> bool:
    """Whether a provider is the offline one, or names none at all."""

    name = str(getattr(provider, "provider_name", "") or "").strip().lower()
    return name in ("", "mock")


class ModelQuestionAssistant:
    """The optional path: ask a ready provider for questions, never require one.

    ``provider_resolver`` is a zero-argument callable returning the composition's
    provider, or ``None`` when none is ready. ``enabled`` is another zero-argument
    callable -- it reads ``questions_from_model`` on every call, so turning the switch
    off takes effect on the next round without a restart.
    """

    def __init__(
        self,
        provider_resolver: Callable[[], Any],
        *,
        enabled: Optional[Callable[[], bool]] = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        cache: Optional[Dict[Tuple[str, str], Tuple[ClarificationQuestion, ...]]] = None,
    ) -> None:
        self._resolve = provider_resolver
        self._enabled = enabled or (lambda: False)
        self._timeout = float(timeout_seconds)
        self._cache = cache if cache is not None else _CACHE
        self._lock = _CACHE_LOCK

    def questions(
        self,
        idea: object,
        domain: object,
        *,
        answered_fields: Sequence[str] = (),
    ) -> Tuple[ClarificationQuestion, ...]:
        """At most three validated questions, or ``()`` on any failure at all."""

        try:
            if not self._enabled():
                return ()
        except Exception:  # noqa: BLE001 - an unreadable switch means "off"
            return ()
        name = str(domain or "").strip().lower()
        if not name or name == "other":
            return ()
        key = (_idea_key(idea), name)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return tuple(
                question
                for question in cached
                if question.field not in set(answered_fields)
            )
        try:
            provider = self._resolve()
        except Exception:  # noqa: BLE001 - a resolver failure means "no provider"
            provider = None
        if provider is None or _is_mock(provider):
            return ()
        output = self._generate(provider, model_question_prompt(idea, name))
        if not output:
            return ()
        questions = parse_model_questions(output, domain=name)
        with self._lock:
            if len(self._cache) >= _CACHE_LIMIT:
                # Bounded on purpose: the cache is a courtesy, not a record.
                self._cache.pop(next(iter(self._cache)), None)
            self._cache[key] = questions
        return tuple(
            question for question in questions if question.field not in set(answered_fields)
        )

    def _generate(self, provider: object, prompt: str) -> str:
        """One provider call, bounded; any failure is an empty answer."""

        try:
            from app.agents.providers.base import ProviderRequest

            request = ProviderRequest(prompt=prompt)
            if self._timeout <= 0:
                response = provider.generate(request)
            else:
                executor = ThreadPoolExecutor(max_workers=1)
                try:
                    future = executor.submit(provider.generate, request)
                    response = future.result(timeout=self._timeout)
                finally:
                    # A slow provider must not delay the questions card, and it must not
                    # keep the process alive at shutdown: the wait is deliberately off.
                    executor.shutdown(wait=False)
            return str(getattr(response, "output", "") or "")
        except Exception:  # noqa: BLE001 - timeout, network, auth, malformed reply
            return ""
