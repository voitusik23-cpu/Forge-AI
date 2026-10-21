"""Turn missing areas into a few short, practical, *clickable* questions.

Deterministic and offline: the question list for one goal and one set of answers is
always the same. An optional LLM phrasing hook could be added behind the existing
``Provider`` interface, but it is deliberately **not** implemented here and is not
required anywhere: a missing provider, a disabled one, or a test without network must
all behave identically to what this module does today.

The rules:

* only areas that are still unknown are asked about, in the canonical
  :data:`app.discovery.analyzer.AREA_ORDER`, with essential areas first;
* an area the user already answered -- or explicitly skipped -- is never asked again;
* a short idea gets at most :data:`app.discovery.analyzer.SHORT_IDEA_MAX_QUESTIONS`
  questions and any idea at most :data:`MAX_QUESTIONS`, which is why the essential
  areas are ordered first: capping can never drop a required one;
* every question carries the typed control its answer deserves -- a radio, a checkbox,
  a select, a yes/no switch or a text field -- with real options and one honest
  ``recommended`` default, so «Не знаю — выбери оптимальный вариант» can pick it.
"""

from __future__ import annotations

from typing import Mapping, Optional, Tuple

from app.discovery.analyzer import (
    AREA_ORDER,
    ESSENTIAL_AREAS,
    SHORT_IDEA_MAX_QUESTIONS,
    GoalAnalysis,
    analyze_goal,
    answered_fields,
    is_short_idea,
    missing_areas,
)
from app.discovery.models import ClarificationOption, ClarificationQuestion

__all__ = [
    "MAX_QUESTIONS",
    "QUESTION_TEXTS",
    "clarification_questions",
    "options_for",
    "question_for",
    "question_for_area",
]


#: The hard bound for any idea. A detailed brief may have a few genuinely open
#: decisions; a short one is bounded tighter, by ``SHORT_IDEA_MAX_QUESTIONS``.
MAX_QUESTIONS = 5

#: ``area -> (question text, why it matters)``. Russian, one sentence each, in the
#: vocabulary of the product rather than of an LLM prompt.
QUESTION_TEXTS: Mapping[str, Tuple[str, str]] = {
    "goal": (
        "Что в итоге должно работать?",
        "Цель задаёт границы проекта и критерий готовности.",
    ),
    "project_type": (
        "Тип проекта",
        "Тип проекта определяет шаблон плана и набор проверок.",
    ),
    "users": (
        "Кто будет пользоваться",
        "От пользователей зависят сценарии и права доступа.",
    ),
    "core_features": (
        "Функции первой версии",
        "Это объём первой версии: без него план будет угадыванием.",
    ),
    "platform": (
        "Где запускать",
        "От этого зависит, как приложение запускается и где хранит данные.",
    ),
    "integrations": (
        "Импорт/экспорт",
        "Импорт и экспорт — отдельная работа, и её лучше решить сразу.",
    ),
    "data_storage": (
        "Хранение данных",
        "Хранение определяет модель данных и способ резервной копии.",
    ),
    "auth": (
        "Нужна ли авторизация",
        "Авторизация определяет доступ и безопасность.",
    ),
    "notifications": (
        "Нужны ли уведомления",
        "Уведомления — отдельная подсистема, её легко забыть.",
    ),
    "constraints": (
        "Ограничения",
        "Ограничения влияют на выбор решения.",
    ),
    "acceptance_criteria": (
        "Что считать готовым результатом",
        "Критерий приёмки нужен, чтобы проверить результат, а не поверить на слово.",
    ),
    "priorities": (
        "Приоритет первой версии",
        "Приоритет задаёт порядок работ.",
    ),
}

#: The exact option sets the product defines. Labels are the user-visible words and are
#: recorded in the specification verbatim.
_PROJECT_TYPE_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("веб-приложение", "веб-приложение", True),
    ("приложение для Windows", "приложение для Windows", False),
    ("Telegram-бот", "Telegram-бот", False),
    ("мобильное приложение", "мобильное приложение", False),
    ("API-сервис", "API-сервис", False),
    ("CLI", "CLI", False),
    ("другое", "другое", False),
)

_USERS_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("только я", "только я", True),
    ("моя семья", "моя семья", False),
    ("сотрудники", "сотрудники", False),
    ("клиенты", "клиенты", False),
    ("публичные пользователи", "публичные пользователи", False),
    ("другое", "другое", False),
)

_PLATFORM_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("только на этом компьютере", "только на этом компьютере", True),
    ("в локальной сети", "в локальной сети", False),
    ("в интернете", "в интернете", False),
    ("пока не знаю", "пока не знаю", False),
)

_STORAGE_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("без постоянного хранения", "без постоянного хранения", False),
    ("JSON-файл", "JSON-файл", False),
    ("SQLite", "SQLite", True),
    ("PostgreSQL", "PostgreSQL", False),
    ("другое", "другое", False),
)

#: Feature options for the first version. The expense list is the owner's own domain; a
#: goal about something else gets the generic list, so a checkbox is never filled with
#: options that do not apply.
_EXPENSE_FEATURE_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("добавление расходов", "добавление расходов", True),
    ("редактирование", "редактирование", False),
    ("удаление", "удаление", False),
    ("месячный отчёт", "месячный отчёт", True),
    ("отчёт по категориям", "отчёт по категориям", False),
    ("импорт", "импорт", False),
    ("экспорт", "экспорт", False),
)

_GENERIC_FEATURE_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("основной сценарий работает", "основной сценарий работает", True),
    ("список и поиск записей", "список и поиск записей", False),
    ("отчёт или сводка", "отчёт или сводка", False),
    ("импорт", "импорт", False),
    ("экспорт", "экспорт", False),
)

_INTEGRATIONS_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("импорт-экспорт", "Да, нужны импорт и экспорт", False),
    ("нет", "Нет, не нужны", True),
)

_AUTH_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("требуется авторизация", "Да, нужна", False),
    ("нет", "Нет, не нужна", True),
)

_NOTIFICATIONS_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("уведомления", "Да, нужны", False),
    ("нет", "Нет, не нужны", True),
)

_CONSTRAINTS_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("локальный запуск", "только локальный запуск", True),
    ("ограниченный бюджет", "ограниченный бюджет", False),
    ("срок", "жёсткий срок", False),
    ("без внешних сервисов", "без внешних сервисов", False),
)

_ACCEPTANCE_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("приложение запускается", "приложение запускается", True),
    ("данные сохраняются", "данные сохраняются", False),
    ("отчёт формируется", "отчёт формируется", False),
    ("тесты проходят", "тесты проходят", False),
)

_PRIORITIES_OPTIONS: Tuple[Tuple[str, str, bool], ...] = (
    ("быстрее получить рабочий результат", "быстрее получить рабочий результат", True),
    ("минимальная стоимость", "минимальная стоимость", False),
    ("качество и надёжность", "качество и надёжность", False),
    ("красивый интерфейс", "красивый интерфейс", False),
    ("пока не знаю", "пока не знаю", False),
)

#: ``area -> (control, options)``. A missing area falls back to free text.
_CONTROLS: Mapping[str, Tuple[str, Tuple[Tuple[str, str, bool], ...]]] = {
    "goal": ("text", ()),
    "project_type": ("radio", _PROJECT_TYPE_OPTIONS),
    "users": ("radio", _USERS_OPTIONS),
    "core_features": ("checkbox", _GENERIC_FEATURE_OPTIONS),
    "platform": ("radio", _PLATFORM_OPTIONS),
    "integrations": ("yes_no", _INTEGRATIONS_OPTIONS),
    "data_storage": ("select", _STORAGE_OPTIONS),
    "auth": ("yes_no", _AUTH_OPTIONS),
    "notifications": ("yes_no", _NOTIFICATIONS_OPTIONS),
    "constraints": ("checkbox", _CONSTRAINTS_OPTIONS),
    "acceptance_criteria": ("checkbox", _ACCEPTANCE_OPTIONS),
    "priorities": ("radio", _PRIORITIES_OPTIONS),
}

#: A checkbox renders as one row of chips; the count is bounded by the option set.
_EXPENSE_NEEDLES: Tuple[str, ...] = (
    "расход",
    "трат",
    "затрат",
    "бюджет",
    "expense",
)

_QUESTION_ID_PREFIX = "q-"


def _is_expense_goal(goal: object) -> bool:
    """Whether the idea is about money going out, so the expense options apply."""

    text = str(goal or "").casefold()
    return any(needle in text for needle in _EXPENSE_NEEDLES)


def options_for(
    area: str, goal: object = None
) -> Tuple[ClarificationOption, ...]:
    """The clickable options for one area, in the product's own words."""

    if area == "core_features":
        entries = (
            _EXPENSE_FEATURE_OPTIONS
            if _is_expense_goal(goal)
            else _GENERIC_FEATURE_OPTIONS
        )
    else:
        control = _CONTROLS.get(area)
        entries = control[1] if control is not None else ()
    return tuple(
        ClarificationOption(value=value, label=label, recommended=recommended)
        for value, label, recommended in entries
    )


def question_for_area(
    area: str, goal: object = None, *, required: Optional[bool] = None
) -> Optional[ClarificationQuestion]:
    """The single question for one area, or ``None`` when the area is unknown."""

    entry = QUESTION_TEXTS.get(area)
    if entry is None:
        return None
    text, why = entry
    control = _CONTROLS.get(area, ("text", ()))[0]
    options = options_for(area, goal)
    return ClarificationQuestion(
        id=f"{_QUESTION_ID_PREFIX}{area}",
        text=text,
        field=area,
        why=why,
        required=(area in ESSENTIAL_AREAS) if required is None else bool(required),
        control=control,
        options=options,
        # Free text is always allowed: a click is the fast path, not a cage. A yes/no
        # question needs no custom field, because it has exactly two honest answers.
        allow_custom=control != "yes_no",
        allow_unknown=True,
    )


def question_for(area: str, goal: object = None) -> Optional[ClarificationQuestion]:
    """The question for one area with its default ``required`` rule."""

    return question_for_area(area, goal)


def clarification_questions(
    goal: object,
    answers: object = None,
    *,
    limit: Optional[int] = None,
) -> Tuple[ClarificationQuestion, ...]:
    """The questions still worth asking, bounded by the idea's own length.

    ``answers`` is either a mapping of ``field -> text`` or an iterable of
    :class:`app.discovery.models.ClarificationAnswer`. Both forms are accepted so
    a stored session and a caller-supplied mapping cannot diverge.
    """

    analysis: GoalAnalysis = analyze_goal(goal)
    unknown = set(missing_areas(analysis, answers))
    already_covered = set(answered_fields(answers))
    effective_limit = (
        (SHORT_IDEA_MAX_QUESTIONS if is_short_idea(goal) else MAX_QUESTIONS)
        if limit is None
        else max(0, int(limit))
    )
    open_areas = [
        area
        for area in AREA_ORDER
        if area in unknown and area not in already_covered
    ]
    # Essential areas first, in the canonical order, then the rest. The cap may not drop
    # a required decision: if more essential areas are open than the cap allows, the cap
    # rises to them, so a session is never left unready by its own question limit.
    ordered = [
        area for area in open_areas if area in ESSENTIAL_AREAS
    ] + [area for area in open_areas if area not in ESSENTIAL_AREAS]
    allowed = max(effective_limit, len([a for a in ordered if a in ESSENTIAL_AREAS]))
    asked: list[ClarificationQuestion] = []
    for area in ordered:
        question = question_for_area(area, goal)
        if question is None:
            continue
        asked.append(question)
        if len(asked) >= allowed:
            break
    return tuple(asked)
