"""Turn missing areas into a few short, practical questions.

Deterministic and offline: the question list for one goal and one set of answers
is always the same. An optional LLM phrasing hook could be added behind the
existing ``Provider`` interface, but it is deliberately **not** implemented here
and is not required anywhere: a missing provider, a disabled one, or a test
without network must all behave identically to what this module does today.

The rules:

* only areas that are still unknown are asked about, in the canonical
  :data:`app.discovery.analyzer.AREA_ORDER`, with essential areas first;
* an area the user already answered -- or explicitly skipped -- is never asked
  again;
* at most :data:`MAX_QUESTIONS` questions are returned, which is why the
  essential areas are ordered first: capping can never drop a required one.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from app.discovery.analyzer import (
    AREA_ORDER,
    ESSENTIAL_AREAS,
    GoalAnalysis,
    analyze_goal,
    answered_fields,
    missing_areas,
)
from app.discovery.models import ClarificationQuestion

__all__ = [
    "MAX_QUESTIONS",
    "QUESTION_TEXTS",
    "clarification_questions",
    "question_for",
]


#: A few short questions, never an interrogation.
MAX_QUESTIONS = 6

#: ``area -> (question text, why it matters)``. Russian, one sentence each, in the
#: vocabulary of the product rather than of an LLM prompt.
QUESTION_TEXTS: Mapping[str, Tuple[str, str]] = {
    "goal": (
        "Что в итоге должно работать?",
        "Цель задаёт границы проекта и критерий готовности.",
    ),
    "project_type": (
        "Какой это тип проекта: сайт, бот, API, дашборд или CLI?",
        "Тип проекта определяет шаблон плана.",
    ),
    "users": (
        "Кто будет пользоваться и какие у них роли?",
        "Роли определяют сценарии и права доступа.",
    ),
    "core_features": (
        "Назовите 3–5 главных функций первой версии.",
        "Без этого нельзя определить объём работ.",
    ),
    "platform": (
        "Где это должно работать: веб-сайт, Telegram, мобильное приложение или CLI?",
        "Платформа определяет интерфейс и способ запуска.",
    ),
    "integrations": (
        "Какие внешние сервисы нужны: оплата, email, SMS, Telegram?",
        "Интеграции влияют на архитектуру и сроки.",
    ),
    "data_storage": (
        "Какие данные хранить и где: база данных или файлы?",
        "Хранение определяет модель данных.",
    ),
    "auth": (
        "Нужна ли авторизация и какая?",
        "Авторизация определяет доступ и безопасность.",
    ),
    "notifications": (
        "Какие уведомления нужны и по какому каналу?",
        "Уведомления — отдельная подсистема.",
    ),
    "constraints": (
        "Есть ли ограничения: бюджет, сроки, стек технологий?",
        "Ограничения влияют на выбор решения.",
    ),
    "acceptance_criteria": (
        "По какому признаку поймём, что готово?",
        "Критерий приёмки нужен, чтобы проверить результат.",
    ),
    "priorities": (
        "Что важнее в первой версии: скорость, функции или качество?",
        "Приоритеты задают порядок работ.",
    ),
}

_QUESTION_ID_PREFIX = "q-"


def question_for(area: str) -> Optional[ClarificationQuestion]:
    """The single question for one area, or ``None`` when the area is unknown."""

    entry = QUESTION_TEXTS.get(area)
    if entry is None:
        return None
    text, why = entry
    return ClarificationQuestion(
        id=f"{_QUESTION_ID_PREFIX}{area}",
        text=text,
        field=area,
        why=why,
        required=area in ESSENTIAL_AREAS,
    )


def clarification_questions(
    goal: object,
    answers: object = None,
    *,
    limit: int = MAX_QUESTIONS,
) -> Tuple[ClarificationQuestion, ...]:
    """The questions still worth asking, at most ``limit`` of them.

    ``answers`` is either a mapping of ``field -> text`` or an iterable of
    :class:`app.discovery.models.ClarificationAnswer`. Both forms are accepted so
    a stored session and a caller-supplied mapping cannot diverge.
    """

    analysis: GoalAnalysis = analyze_goal(goal)
    unknown = set(missing_areas(analysis, answers))
    already_covered = set(answered_fields(answers))
    asked: list[ClarificationQuestion] = []
    for area in AREA_ORDER:
        if area not in unknown or area in already_covered:
            continue
        question = question_for(area)
        if question is None:
            continue
        asked.append(question)
        if len(asked) >= max(0, int(limit)):
            break
    return tuple(asked)
