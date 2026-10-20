"""The intent a model answer *declares*, stated as one provider-neutral value.

This module answers exactly one question: *what did the model say it was doing?*
It is deliberately **pure and deterministic** -- standard library only, no clock, no
I/O, no provider, no project store -- so the same answer text always produces the
same :class:`ModelIntent`, and a caller can test the rules without a run.

Why it exists
-------------

``app/agent_runtime/outcome.py`` decides the *outcome* of a run from the facts that
exist on disk: a project record, a workspace, an artifact manifest, a verification and
an acceptance verdict. That is the right rule for completion, and it is unchanged here.

What was missing is the other half of the same question. A model answer is prose. Prose
can say "here are three questions before I start", "here is the plan", "here are the
files" or "here is the review" -- and the runtime could only infer that from a narrow
question heuristic. A provider-neutral envelope makes the declaration explicit and
machine-readable *without* letting the model decide anything: this module never sets a
project status, never writes a file and never claims completion. It only classifies the
answer, and records why it could not be classified.

The five intent types
---------------------

=======================  ===========================================================
``CLARIFICATION_REQUIRED``  the answer asks the owner questions; work has not started
``PLAN_READY``              the answer proposes a plan that needs confirmation
``GENERATE_PROJECT``        the answer declares files to write through the write path
``REVIEW``                  the answer judges existing work
``FINAL_ANSWER``            the answer is text only; it declares no work
=======================  ===========================================================

Truthfulness rules, and what this module may **not** do
-------------------------------------------------------

* ``FINAL_ANSWER``, ``CLARIFICATION_REQUIRED`` and ``PLAN_READY`` can never yield a
  completed project. ``GENERATE_PROJECT`` is the only intent that may, and only when
  the files it names were *actually written* through the existing write path and then
  verified and accepted -- that decision belongs to
  :func:`app.agent_runtime.outcome.derive_outcome`, which receives the intent type as
  one more fact. Nothing in this module sets a status.
* A malformed envelope, an unknown ``intent_type`` or a missing required field **never
  raises**. The run continues: the answer is reported as ``FINAL_ANSWER`` (or as the
  recognised type with ``validation.valid=False`` and the named errors), the raw text is
  preserved, and the caller decides what to do with an answer it could not classify.
* No envelope at all falls back to a deterministic heuristic, in this order:
  a question list (two or more question lines) -> ``CLARIFICATION_REQUIRED``; a fenced
  block that names a file (``path=`` or ``**File:**``) -> ``GENERATE_PROJECT``; a
  numbered plan with no code fence -> ``PLAN_READY``; otherwise ``FINAL_ANSWER``.

The response payload deliberately carries ``raw_text_preserved`` instead of the raw text:
the answer can contain prompt material or a credential-shaped string, and the run
response is a client-facing document.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple

from app.agent_runtime.outcome import (
    extract_clarifying_questions,
    intent_allows_completion as _intent_allows_completion,
)

__all__ = [
    "INTENT_TYPES",
    "IntentType",
    "IntentValidation",
    "ModelIntent",
    "NEXT_ACTIONS",
    "SOURCE_ENVELOPE",
    "SOURCE_HEURISTIC",
    "intent_allows_completion",
    "parse_model_intent",
]


class IntentType(str, Enum):
    """What a model answer declares it is doing. Exactly five values."""

    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    PLAN_READY = "PLAN_READY"
    GENERATE_PROJECT = "GENERATE_PROJECT"
    REVIEW = "REVIEW"
    FINAL_ANSWER = "FINAL_ANSWER"


#: The five wire words, in declaration order.
INTENT_TYPES: Tuple[str, ...] = tuple(member.value for member in IntentType)

#: The one action each intent asks for next. Fixed strings, never model prose.
NEXT_ACTIONS = {
    IntentType.CLARIFICATION_REQUIRED: "answer_questions",
    IntentType.PLAN_READY: "confirm_plan",
    IntentType.GENERATE_PROJECT: "apply_files",
    IntentType.REVIEW: "decide_on_review",
    IntentType.FINAL_ANSWER: "read_answer",
}

#: Where the classification came from. Both are recordable; neither is a status.
SOURCE_ENVELOPE = "envelope"
SOURCE_HEURISTIC = "heuristic"

#: A summary is one line of prose; anything longer is truncated, not invented.
MAX_SUMMARY_CHARS = 500

#: ``path=tools/foo.py`` anywhere in a fence info string.
_PATH_IN_FENCE_INFO = re.compile(r"""path\s*=\s*["']?([^"'\s`]+)""", re.IGNORECASE)

#: ``**File: tools/foo.py**`` / ``File: tools/foo.py`` on one line of its own.
_FILE_MARKER = re.compile(
    r"^\s*(?:\*\*)?File:\s*(?P<path>[^\s*]+)(?:\*\*)?\s*$", re.IGNORECASE
)

#: A fenced code block. The body is bounded by the closing fence or the end of text.
_FENCE = re.compile(
    r"```[ \t]*(?P<info>[^\n`]*)\n(?P<body>.*?)(?:```|\Z)", re.DOTALL
)

#: A numbered line: ``1. text``, ``2) text``. Three of them are a plan.
_NUMBERED_LINE = re.compile(r"^\s*\d+[.)]\s+\S")

#: A quoted ``"intent_type"`` key in the text. Its presence without a parsing object
#: is what "malformed envelope" means -- never a heuristic guess over broken JSON.
_INTENT_KEY = re.compile(r'"intent_type"\s*:')


class IntentValidation:
    """Why an intent is (or is not) usable, and where it came from.

    ``valid`` is False exactly when at least one required fact for the declared type is
    missing or malformed. ``errors`` are fixed, lower-case codes with a short
    explanation; they never echo the answer's content, so a credential-shaped answer
    cannot leak through a validation message.
    """

    __slots__ = ("valid", "errors", "source")

    def __init__(
        self,
        valid: bool = True,
        errors: Sequence[str] = (),
        source: str = SOURCE_HEURISTIC,
    ) -> None:
        self.valid = bool(valid)
        self.errors: Tuple[str, ...] = tuple(errors)
        self.source = str(source)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": list(self.errors),
            "source": self.source,
        }

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"IntentValidation(valid={self.valid!r}, errors={self.errors!r}, "
            f"source={self.source!r})"
        )


@dataclass(frozen=True)
class ModelIntent:
    """One answer's declared intent. Seven fields, plus the preserved raw text.

    ``files`` maps a path to its declared body when the answer really carries one. A
    ``{"path": ..., "purpose": ...}`` entry declares only a purpose, and the purpose is
    recorded in place of a body; the parser never fabricates file content. Nothing here
    proves a file was written -- only the write path can say that.
    """

    intent_type: IntentType
    summary: str
    questions: Tuple[str, ...] = ()
    plan: Tuple[str, ...] = ()
    files: Mapping[str, str] = field(default_factory=dict)
    validation: IntentValidation = field(default_factory=IntentValidation)
    next_action: str = ""
    raw_text: str = ""

    # -------------------------------------------------------------- conveniences
    @property
    def valid(self) -> bool:
        return self.validation.valid

    @property
    def errors(self) -> Tuple[str, ...]:
        return self.validation.errors

    @property
    def source(self) -> str:
        return self.validation.source

    @property
    def file_paths(self) -> Tuple[str, ...]:
        return tuple(self.files.keys())

    def response_payload(self) -> Dict[str, Any]:
        """The row a run response publishes. Never the raw text itself."""

        return {
            "intent_type": self.intent_type.value,
            "summary": self.summary,
            "valid": self.validation.valid,
            "validation_errors": list(self.validation.errors),
            "next_action": self.next_action,
            # The answer was kept in ``raw_text``; it is not echoed to the client.
            "raw_text_preserved": True,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent_type": self.intent_type.value,
            "summary": self.summary,
            "questions": list(self.questions),
            "plan": list(self.plan),
            "files": dict(self.files),
            "validation": self.validation.to_dict(),
            "next_action": self.next_action,
        }


def intent_allows_completion(intent_type: object) -> bool:
    """Whether a declared intent may ever justify a completed project.

    The rule itself lives in :mod:`app.agent_runtime.outcome`, beside the classifier
    that applies it; this function re-exports it so a reader of this module can see the
    whole truthfulness contract without following an import. Only ``GENERATE_PROJECT``
    may, and ``""``/``None`` (no declared intent) keeps the pre-existing behaviour.
    """

    return _intent_allows_completion(intent_type)


# --------------------------------------------------------------------------- #
# envelope extraction
# --------------------------------------------------------------------------- #


def _json_objects(text: str) -> Iterator[Tuple[int, Mapping[str, Any]]]:
    """Every JSON object that parses inside ``text``, in text order.

    A bare object, a fenced block and an object embedded in prose are all just text
    containing a ``{``; scanning for decodable objects covers all three without a
    separate code path for each, and a truncated object simply does not parse.
    """

    decoder = json.JSONDecoder()
    index = 0
    length = len(text)
    while index < length:
        start = text.find("{", index)
        if start < 0:
            return
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            index = start + 1
            continue
        if isinstance(value, Mapping):
            yield start, value
        index = end if end > start else start + 1


def _find_envelope(text: str) -> Optional[Mapping[str, Any]]:
    """The first decodable JSON object that declares ``intent_type``."""

    for _position, value in _json_objects(text):
        if "intent_type" in value:
            return value
    return None


def _looks_like_broken_envelope(text: str) -> bool:
    """Whether the text *intends* an envelope but nothing in it parses.

    Two shapes mean that, and only these two: the text names the ``"intent_type"``
    key, or it carries a fence labelled ``json`` whose body holds an object. A plain
    code block or a paragraph that merely contains braces is not an envelope, and
    guessing over it would be the fabrication this module refuses to make.
    """

    if _INTENT_KEY.search(text) is not None:
        return True
    for match in _FENCE.finditer(text):
        info = (match.group("info") or "").strip().lower()
        if not info.startswith("json"):
            continue
        if "{" in (match.group("body") or ""):
            return True
    return False


# --------------------------------------------------------------------------- #
# field coercion -- a wrong shape is a named error, never an exception
# --------------------------------------------------------------------------- #


def _string_list(value: object) -> Optional[Tuple[str, ...]]:
    """A list of non-empty strings, or ``None`` when the shape is wrong."""

    if not isinstance(value, list):
        return None
    cleaned: List[str] = []
    for item in value:
        if not isinstance(item, str):
            return None
        text = item.strip()
        if not text:
            return None
        cleaned.append(text)
    return tuple(cleaned)


def _file_mapping(value: object) -> Optional[Dict[str, str]]:
    """``path -> content`` from a mapping or from ``[{path, purpose}]``.

    A mapping may carry any string body (an empty one included: a declared but empty
    file is still a declared file). A list entry must name a path; its ``content`` is
    used when present, otherwise its ``purpose``. ``None`` means the shape is wrong.
    """

    if isinstance(value, Mapping):
        files: Dict[str, str] = {}
        for key, body in value.items():
            if not isinstance(key, str) or not key.strip():
                return None
            if not isinstance(body, str):
                return None
            files[key.strip()] = body
        return files
    if isinstance(value, list):
        files = {}
        for item in value:
            if not isinstance(item, Mapping):
                return None
            path = item.get("path")
            if not isinstance(path, str) or not path.strip():
                return None
            body = item.get("content")
            if body is None:
                body = item.get("purpose", "")
            if not isinstance(body, str):
                return None
            files[path.strip()] = body
        return files
    return None


def _summary_of(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    return text[:MAX_SUMMARY_CHARS]


# --------------------------------------------------------------------------- #
# the parser
# --------------------------------------------------------------------------- #


def _failed(
    raw: str,
    *,
    summary: str = "",
    errors: Sequence[str],
    source: str,
) -> ModelIntent:
    """A ``FINAL_ANSWER`` that could not be trusted, with the reason recorded."""

    return ModelIntent(
        intent_type=IntentType.FINAL_ANSWER,
        summary=summary,
        validation=IntentValidation(False, tuple(errors), source),
        next_action=NEXT_ACTIONS[IntentType.FINAL_ANSWER],
        raw_text=raw,
    )


def _from_envelope(envelope: Mapping[str, Any], raw: str) -> ModelIntent:
    """Classify one parsed envelope, recording every field that is wrong."""

    summary = _summary_of(envelope.get("summary"))
    declared = envelope.get("intent_type")
    declared_text = declared.strip().upper() if isinstance(declared, str) else ""
    if declared_text not in INTENT_TYPES:
        # An unknown type is not silently coerced into a known one: the answer is
        # reported as text only, and the unknown word is named (bounded, no prose).
        return _failed(
            raw,
            summary=summary,
            errors=(
                "unknown_intent_type: "
                + (declared_text[:64] if declared_text else "missing"),
            ),
            source=SOURCE_ENVELOPE,
        )

    intent_type = IntentType(declared_text)
    errors: List[str] = []
    if not summary:
        errors.append("summary_missing: a non-empty 'summary' string is required")

    questions: Tuple[str, ...] = ()
    plan: Tuple[str, ...] = ()
    files: Dict[str, str] = {}

    if intent_type is IntentType.CLARIFICATION_REQUIRED:
        parsed = _string_list(envelope.get("questions"))
        if not parsed:
            errors.append(
                "questions_invalid: 'questions' must be a non-empty list of "
                "non-empty strings"
            )
        else:
            questions = parsed
    elif intent_type is IntentType.PLAN_READY:
        parsed_plan = _string_list(envelope.get("plan"))
        if not parsed_plan:
            errors.append(
                "plan_invalid: 'plan' must be a non-empty list of non-empty strings"
            )
        else:
            plan = parsed_plan
    elif intent_type is IntentType.GENERATE_PROJECT:
        parsed_files = _file_mapping(envelope.get("files"))
        if parsed_files is None:
            errors.append(
                "files_invalid: 'files' must be a mapping path->content or a list of "
                "{path, purpose}"
            )
        elif not parsed_files:
            errors.append("files_empty: at least one file path is required")
        else:
            files = parsed_files

    return ModelIntent(
        intent_type=intent_type,
        summary=summary,
        questions=questions,
        plan=plan,
        files=files,
        validation=IntentValidation(not errors, tuple(errors), SOURCE_ENVELOPE),
        next_action=NEXT_ACTIONS[intent_type],
        raw_text=raw,
    )


def _fenced_files(text: str) -> Dict[str, str]:
    """Paths a fenced answer really declares, with the bodies it really carries.

    Two forms are recognised, and they are the two the guarded writer
    (``app/agent_runtime/code_writer.py``) accepts: ``path=`` in the fence info string,
    and a ``**File: path**`` line immediately before the fence. A ``**File:**`` marker
    with no fence after it still declares a path, with no body.
    """

    files: Dict[str, str] = {}
    consumed_end = 0
    for match in _FENCE.finditer(text):
        header = match.group("info") or ""
        path = ""
        found = _PATH_IN_FENCE_INFO.search(header)
        if found:
            path = found.group(1).strip()
        if not path:
            before = text[consumed_end : match.start()].splitlines()
            for line in reversed(before):
                if not line.strip():
                    continue
                marker = _FILE_MARKER.match(line)
                if marker:
                    path = marker.group("path").strip()
                break
        consumed_end = match.end()
        if path:
            files[path] = match.group("body") or ""

    # A ``**File:**`` marker whose fence never followed still declares a path.
    for line in text.splitlines():
        marker = _FILE_MARKER.match(line)
        if marker:
            path = marker.group("path").strip()
            if path and path not in files:
                files[path] = ""
    return files


def _numbered_lines(text: str) -> Tuple[str, ...]:
    """The numbered steps of a plan, in answer order, with the markers removed."""

    steps: List[str] = []
    for line in text.splitlines():
        if not _NUMBERED_LINE.match(line):
            continue
        cleaned = re.sub(r"^\s*\d+[.)]\s+", "", line).strip()
        if cleaned:
            steps.append(cleaned)
    return tuple(steps)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if stripped and not stripped.startswith("```"):
            return stripped[:MAX_SUMMARY_CHARS]
    return ""


def _heuristic(raw: str) -> ModelIntent:
    """The deterministic fallback when the answer declares no envelope.

    The order is fixed and documented: a question list, then a fenced file
    declaration, then a numbered plan with no code fence, then plain text.
    """

    questions = extract_clarifying_questions(raw)
    if len(questions) >= 2:
        return ModelIntent(
            intent_type=IntentType.CLARIFICATION_REQUIRED,
            summary=_first_line(raw),
            questions=questions,
            validation=IntentValidation(True, (), SOURCE_HEURISTIC),
            next_action=NEXT_ACTIONS[IntentType.CLARIFICATION_REQUIRED],
            raw_text=raw,
        )

    files = _fenced_files(raw)
    if files:
        return ModelIntent(
            intent_type=IntentType.GENERATE_PROJECT,
            summary=_first_line(raw),
            files=files,
            validation=IntentValidation(True, (), SOURCE_HEURISTIC),
            next_action=NEXT_ACTIONS[IntentType.GENERATE_PROJECT],
            raw_text=raw,
        )

    steps = _numbered_lines(raw)
    if len(steps) >= 3 and not _FENCE.search(raw):
        return ModelIntent(
            intent_type=IntentType.PLAN_READY,
            summary=_first_line(raw),
            plan=steps,
            validation=IntentValidation(True, (), SOURCE_HEURISTIC),
            next_action=NEXT_ACTIONS[IntentType.PLAN_READY],
            raw_text=raw,
        )

    summary = _first_line(raw)
    errors = () if summary else ("summary_missing: the answer is empty",)
    return ModelIntent(
        intent_type=IntentType.FINAL_ANSWER,
        summary=summary,
        validation=IntentValidation(not errors, errors, SOURCE_HEURISTIC),
        next_action=NEXT_ACTIONS[IntentType.FINAL_ANSWER],
        raw_text=raw,
    )


def parse_model_intent(answer_text: object) -> ModelIntent:
    """Classify one model answer. Pure, deterministic and non-raising.

    The three paths, in order:

    1. a decodable JSON object that declares ``intent_type`` -> validated against the
       required fields of that type;
    2. the answer names ``"intent_type"`` but no object parses -> ``FINAL_ANSWER`` with
       an ``invalid_json`` error (the run continues);
    3. no envelope at all -> the deterministic heuristic.

    ``answer_text`` may be anything; ``None`` is an empty answer.
    """

    raw = "" if answer_text is None else str(answer_text)
    envelope = _find_envelope(raw)
    if envelope is not None:
        return _from_envelope(envelope, raw)
    if _looks_like_broken_envelope(raw):
        return _failed(
            raw,
            summary=_first_line(raw),
            errors=(
                "invalid_json: the answer carries an intent envelope but no JSON "
                "object in it parses",
            ),
            source=SOURCE_ENVELOPE,
        )
    return _heuristic(raw)
