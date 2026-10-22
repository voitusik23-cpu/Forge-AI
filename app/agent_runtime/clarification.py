"""The deterministic clarification block that carries the owner's answers forward.

Why this module exists
----------------------

A run can stop because the model asked questions instead of writing files
(``RunOutcome.CLARIFICATION_REQUIRED``). The product then offered a single control,
«Ответить», and that control led back into the *discovery* card: its questions are
derived from the goal text by :mod:`app.discovery.questions`, so the owner answered
questions the model never asked, pressed «приступить», and the next run received the
**same description** -- the model asked the same questions again. Measured: an
endless loop, five recorded events, no file.

The answer to that is not a new architecture. It is one deterministic text the code
path appends to the task it is already building:

    Уточнения от пользователя:
    - <вопрос> → <ответ>
    ...
    Продолжай и СЕЙЧАС создай файлы. Больше вопросов не задавай.

and one counter, :data:`MAX_CLARIFICATION_ROUNDS`, so the same unanswered question
set can never produce a second clarification round. Everything here is pure:
standard library plus the repository's existing secret screen, no I/O, no clock, no
provider. The service owns the durable record; this module owns the words and the
rules that decide them, so both can be tested without a run.

Round numbering
---------------

* **1** -- the first dispatch: no answers yet. It may ask.
* **2** -- a dispatch that carries answers. It is *forced*: the block above plus the
  ``intent=GENERATE_PROJECT`` instruction. If it still produces no file, the loop is
  over and the card says so. :data:`MAX_CLARIFICATION_ROUNDS` is 2, so there is no
  round 3.

The block is appended **after** the description and **before** the output contract
(:mod:`app.agent_runtime.output_contract`), because the last text a model reads is
the one that decides the answer's shape.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

__all__ = [
    "CLARIFICATION_ANSWER_LIMIT",
    "CLARIFICATION_ROUNDS",
    "ClarificationAnswer",
    "MAX_CLARIFICATION_ROUNDS",
    "MAX_CLARIFICATION_ANSWERS",
    "answers_block",
    "clarification_prompt",
    "coerce_answers",
    "compose_with_answers",
    "merge_answers",
    "next_round",
    "normalise_question",
    "round_is_final",
    "screen_answer",
    "unanswered_questions",
    "WRITE_NOW_SENTENCE",
    "ANSWERS_TITLE",
]

#: The number of clarification rounds that may ever exist: 1 (ask) -> 2 (forced).
#: A third round is impossible by construction, not by a loop guard somewhere else.
MAX_CLARIFICATION_ROUNDS = 2

#: Every round number, in order. There is deliberately no member after the final one.
CLARIFICATION_ROUNDS: tuple[int, ...] = tuple(range(1, MAX_CLARIFICATION_ROUNDS + 1))

#: How many answers one block may carry, and how long one answer may be. Bounded so a
#: caller cannot turn the block into an unbounded prompt or a place to hide a payload.
MAX_CLARIFICATION_ANSWERS = 8
CLARIFICATION_ANSWER_LIMIT = 600
CLARIFICATION_QUESTION_LIMIT = 600

#: The canonical heading. One spelling, in Russian, matched exactly by the tests.
ANSWERS_TITLE = "Уточнения от пользователя:"

#: The sentence that ends the block: the model is told to stop asking and write.
WRITE_NOW_SENTENCE = "Продолжай и СЕЙЧАС создай файлы. Больше вопросов не задавай."

#: The forced-generation paragraph of the final round. ``intent=GENERATE_PROJECT`` is
#: the same word :func:`app.agent_runtime.intent.parse_model_intent` recognises, and
#: the English sentence is included verbatim because the models this app talks to
#: follow it more reliably than its Russian translation.
FORCE_GENERATE_INSTRUCTION = (
    "Режим генерации (последний раунд уточнений): верни intent=GENERATE_PROJECT и файлы.\n"
    "Do not ask further questions — write the files with the information you have."
)

#: The marker stored in place of an answer that looks like a credential. It reuses the
#: discovery layer's own marker (:data:`app.discovery.models.REDACTION_MARKER_PREFIX`)
#: so one word means the same thing everywhere; repeated here as a literal because
#: this module must not import the discovery layer (which imports the API service).
_REDACTION_MARKER_PREFIX = "[REDACTED"

#: Punctuation that does not change *which* question is asked: stripped on both sides
#: before two questions are compared, so the owner's answer to
#: "Нужен ли детский стул на НГ?" also covers a re-asked "Нужен ли детский стул на НГ".
_EDGE_PUNCTUATION = " \t\r\n?!.,;:—–-\"'«»()[]{}…"

#: Words a model uses to introduce the same question list again. Removed before the
#: comparison so "Уточните, пожалуйста:" + the same question still matches. A word, its
#: separating punctuation and any run of filler words are removed one at a time.
_FILLER_WORD = re.compile(
    r"^(?:уточните|пожалуйста|скажите|подскажите|ответьте|please)\b[\s,:;.!—–-]*",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClarificationAnswer:
    """One question the model asked and the answer the owner gave.

    ``question`` is kept verbatim (bounded) because it is quoted back to the model and
    shown in the card; ``answer`` is the owner's own words, screened and bounded.
    """

    question: str
    answer: str

    def to_dict(self) -> dict:
        return {"question": self.question, "answer": self.answer}


def _bounded(text: object, limit: int) -> str:
    """One line of text: whitespace collapsed, bounded, never a newline.

    A newline would let an answer forge a second list item (``- fake → fake``), which
    is exactly the kind of prompt injection this deterministic block must refuse.
    """

    value = " ".join(str(text if text is not None else "").split())
    value = value.replace("→", "->")
    return value[:limit].strip()


def normalise_question(text: object) -> str:
    """The comparison form of a question: what makes two questions "the same one".

    Case, surrounding punctuation, a leading list marker, the «Уточните, пожалуйста:»
    filler and the difference between ``ё``/``е`` are ignored. The words themselves are
    not: two different questions never compare equal.
    """

    value = unicodedata.normalize("NFKC", str(text if text is not None else ""))
    value = value.replace("ё", "е").replace("Ё", "Е").lower()
    value = " ".join(value.split())
    value = re.sub(r"^\s*(?:[-*\u2022\u2013\u2014]|\d+[.)]|[a-zA-Z\u0430-\u044f]\))\s+", "", value)
    value = value.strip(_EDGE_PUNCTUATION)
    # "Уточните, пожалуйста: <question>" asks the same question as "<question>". Filler words
    # are removed one token at a time, never from the middle of the question.
    for _ in range(4):
        match = _FILLER_WORD.match(value)
        if match is None:
            break
        remainder = value[match.end():].strip(_EDGE_PUNCTUATION)
        if not remainder:
            break
        value = remainder
    return value.strip(_EDGE_PUNCTUATION)


def screen_answer(value: object) -> str:
    """The answer as it may be stored: screened of secret-shaped text, bounded.

    The detector is the repository's existing one
    (:func:`app.context.validation.find_forbidden_value_content`), so this layer
    invents no second notion of "looks like a secret" and never stores or sends a
    credential the owner pasted into an answer field.
    """

    text = _bounded(value, CLARIFICATION_ANSWER_LIMIT)
    if not text:
        return ""
    from app.context.validation import find_forbidden_value_content

    reason = find_forbidden_value_content(text)
    if reason is None:
        return text
    return f"{_REDACTION_MARKER_PREFIX}:{reason}]"


def coerce_answers(raw: object) -> tuple[ClarificationAnswer, ...]:
    """Validate a caller's answer list into the canonical tuple.

    Accepted shape: a sequence of mappings with non-empty ``question`` and ``answer``
    strings (or :class:`ClarificationAnswer` objects). Anything else is dropped rather
    than guessed at -- a malformed answer must never become prompt text. Answers are
    deduplicated by :func:`normalise_question`, the later answer winning, and the list
    is bounded by :data:`MAX_CLARIFICATION_ANSWERS`.
    """

    if raw is None or isinstance(raw, (str, bytes, Mapping)):
        return ()
    if not isinstance(raw, Iterable):
        return ()
    kept: dict[str, ClarificationAnswer] = {}
    for item in raw:
        if isinstance(item, ClarificationAnswer):
            question, answer = item.question, item.answer
        elif isinstance(item, Mapping):
            question = item.get("question")
            answer = item.get("answer")
        else:
            continue
        question_text = _bounded(question, CLARIFICATION_QUESTION_LIMIT)
        answer_text = screen_answer(answer)
        if not question_text or not answer_text:
            continue
        key = normalise_question(question_text)
        if not key:
            continue
        kept[key] = ClarificationAnswer(question=question_text, answer=answer_text)
        if len(kept) >= MAX_CLARIFICATION_ANSWERS:
            break
    return tuple(kept.values())


def merge_answers(
    *earlier: Sequence[ClarificationAnswer],
) -> tuple[ClarificationAnswer, ...]:
    """Merge answer batches for one clarification context, later answers winning.

    The context is what makes a retry keep the answers the owner already gave: the
    run that carries a new batch still carries every earlier pair, so a model is never
    told less on the second attempt than it was told on the first.
    """

    merged: dict[str, ClarificationAnswer] = {}
    for batch in earlier:
        for item in tuple(batch or ()):
            if not isinstance(item, ClarificationAnswer):
                continue
            key = normalise_question(item.question)
            if key:
                merged[key] = item
    return tuple(merged.values())


def answers_block(answers: Sequence[ClarificationAnswer], *, round_number: int = 1) -> str:
    """The canonical block: the heading, one ``- question → answer`` line each, the rule.

    Byte-identical for the same answers, so the same run always produces the same
    prompt. The closing sentence is part of the block (the requirement's own format),
    and the final round appends :data:`FORCE_GENERATE_INSTRUCTION` after it.
    """

    lines = [ANSWERS_TITLE]
    for item in tuple(answers or ()):
        if not isinstance(item, ClarificationAnswer):
            continue
        lines.append(f"- {item.question} → {item.answer}")
    lines.append(WRITE_NOW_SENTENCE)
    block = "\n".join(lines)
    if round_is_final(round_number):
        block = f"{block}\n\n{FORCE_GENERATE_INSTRUCTION}"
    return block


def compose_with_answers(
    description: object,
    answers: Sequence[ClarificationAnswer],
    *,
    round_number: int = 1,
) -> str:
    """``description`` followed by the canonical block (nothing else).

    The description is never rewritten and never dropped: the owner's own words stay
    first, exactly as the output contract keeps them first. With no answers the
    description is returned unchanged, so every existing caller is byte-identical.
    """

    text = str(description if description is not None else "")
    if not answers:
        return text
    block = answers_block(answers, round_number=round_number)
    if not text.strip():
        return block
    return f"{text}\n\n{block}"


def clarification_prompt(
    description: object,
    answers: Sequence[ClarificationAnswer],
    *,
    round_number: int = 1,
    write_mode: bool = False,
    workspace_context: str = "",
) -> str:
    """The task text a write or read run is given when answers are carried.

    The same order everywhere, and it is the order the requirement names: the
    description, then the answers block, then the output contract **last**. ``write``
    mode is the only mode that carries the contract (the contract asks for files, and
    a read-only run owes none), which is why it is a flag rather than a guess.
    """

    prompt = compose_with_answers(description, answers, round_number=round_number)
    if not answers:
        return prompt
    if write_mode:
        from app.agent_runtime.output_contract import append_output_contract

        context = str(workspace_context or "").strip()
        if context and context not in prompt:
            prompt = f"{prompt}\n\n{context}"
        return append_output_contract(prompt)
    return prompt


def next_round(previous_round: object, *, has_answers: bool) -> int:
    """The round this dispatch belongs to (1 or 2, never more).

    ``1`` is a dispatch that has no answers yet: it may ask. ``2`` is a dispatch that
    carries answers, and it is forced. A previous round of ``2`` stays ``2``, so a
    retry of the final round can never reopen a third round.
    """

    try:
        seen = int(previous_round or 0)
    except (TypeError, ValueError):
        seen = 0
    if seen >= MAX_CLARIFICATION_ROUNDS:
        return MAX_CLARIFICATION_ROUNDS
    if has_answers:
        return min(max(seen + 1, 2), MAX_CLARIFICATION_ROUNDS)
    return 1


def round_is_final(round_number: object) -> bool:
    """Whether this round is the last one: no further questions may be surfaced."""

    try:
        return int(round_number or 0) >= MAX_CLARIFICATION_ROUNDS
    except (TypeError, ValueError):
        return False


def unanswered_questions(
    questions: Iterable[object],
    answered: Iterable[ClarificationAnswer],
) -> tuple[str, ...]:
    """``questions`` minus every question the owner has already answered.

    Comparison is :func:`normalise_question`, so a re-worded but identical question is
    recognised. The result keeps the model's own order and wording for the questions
    that are genuinely new; an empty tuple means "nothing new to ask", which is the
    escalation condition of the caller (round 2, terminal card).
    """

    known = {
        normalise_question(getattr(item, "question", ""))
        for item in tuple(answered or ())
        if isinstance(item, ClarificationAnswer)
    }
    known.discard("")
    fresh: list[str] = []
    for raw in tuple(questions or ()):
        text = str(raw or "").strip()
        if not text:
            continue
        if normalise_question(text) in known:
            continue
        if text not in fresh:
            fresh.append(text)
    return tuple(fresh)
