"""Deterministic derivation of project facts from the goal text.

Rules only. There is no provider call here, no model, no I/O, and no network:
the same goal text always yields the same derivation, so the clarification layer
is testable offline and a run is never blocked on an LLM being available.

The derivation covers what a short goal usually states outright (project type,
platform, users, core features, the services it must talk to, storage, auth,
notifications). Everything a goal does not state stays **empty** -- it is
reported as a missing area and asked about, never guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from typing import Any, Mapping, Tuple

__all__ = [
    "AREA_ORDER",
    "ESSENTIAL_AREAS",
    "LIST_AREAS",
    "GoalAnalysis",
    "analyze_goal",
    "answered_fields",
    "missing_areas",
]


#: The fixed set of areas a project specification covers, in the order they are
#: derived and asked about. The order is part of the contract: the essential
#: areas come first, so capping the question list can never drop a required one.
AREA_ORDER: Tuple[str, ...] = (
    "goal",
    "project_type",
    "users",
    "core_features",
    "platform",
    "integrations",
    "data_storage",
    "auth",
    "notifications",
    "constraints",
    "acceptance_criteria",
    "priorities",
)

#: Areas without which no useful plan can be produced. The session stays in
#: ``NEEDS_CLARIFICATION`` until each of these is answered or derived.
ESSENTIAL_AREAS = frozenset(
    {"goal", "project_type", "users", "core_features", "platform"}
)

#: Areas whose value is a list rather than a single line of text.
LIST_AREAS = frozenset(
    {"core_features", "integrations", "constraints", "acceptance_criteria", "priorities"}
)


def _is_latin_token(needle: str) -> bool:
    return bool(needle) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in needle
    )


def _has(text: str, *needles: str) -> bool:
    """Whether the case-folded ``text`` mentions any of ``needles``.

    A short Latin token is matched on word boundaries (so ``api`` does not match
    "rapid"), while a Russian stem is matched as a substring (``бронир`` covers
    "бронирование" and "бронировать"). Both are deterministic and locale-free.
    """

    for needle in needles:
        if _is_latin_token(needle):
            if re.search(
                rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", text
            ):
                return True
        elif needle in text:
            return True
    return False


@dataclass(frozen=True)
class GoalAnalysis:
    """What the goal text itself already answers. Empty means "not stated"."""

    goal: str = ""
    project_type: str = ""
    users: str = ""
    core_features: Tuple[str, ...] = ()
    platform: str = ""
    integrations: Tuple[str, ...] = ()
    data_storage: str = ""
    auth: str = ""
    notifications: str = ""
    constraints: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[str, ...] = ()
    priorities: Tuple[str, ...] = ()

    def value_for(self, area: str) -> Any:
        """The derived value for one area (``""`` or ``()`` when not derived)."""

        return getattr(self, area, "")

    def has(self, area: str) -> bool:
        return bool(self.value_for(area))

    def known_areas(self) -> Tuple[str, ...]:
        return tuple(area for area in AREA_ORDER if self.has(area))

    def to_dict(self) -> dict[str, Any]:
        return {field_.name: self.value_for(field_.name) for field_ in fields(self)}


def _list_from(text: str, *patterns: Tuple[str, ...]) -> Tuple[str, ...]:
    """Collect the labels whose patterns match, in the caller's declared order.

    Each pattern is ``(label, *needles)``; the label is itself one of the needles,
    so ``("email", "e-mail", "почт")`` matches the label spelled exactly as well as
    its variants. The resulting list is deterministic in both content and order for
    one goal text.
    """

    found = []
    for group in patterns:
        if _has(text, *group):
            found.append(group[0])
    return tuple(found)


def analyze_goal(goal: object) -> GoalAnalysis:
    """Derive every area the goal text states, deterministically.

    A non-string or empty goal derives nothing; it is reported as missing rather
    than raising, because a session is created before the user has said anything
    useful.
    """

    if not isinstance(goal, str) or not goal.strip():
        return GoalAnalysis()
    text = " ".join(goal.casefold().split())

    project_type = ""
    if _has(text, "telegram") and _has(text, "бот", "bot"):
        project_type = "Telegram-бот"
    elif _has(text, "бронир", "booking", "отель", "hotel"):
        project_type = "система бронирования"
    elif _has(
        text, "интернет-магазин", "e-commerce", "ecommerce", "магазин", "shop", "store"
    ):
        project_type = "интернет-магазин"
    elif _has(text, "дашборд", "dashboard", "админ-панель", "панель управления"):
        project_type = "дашборд"
    elif _has(text, "api", "апи", "микросервис", "rest"):
        project_type = "API-сервис"
    elif _has(text, "cli", "консольн", "командная строка"):
        project_type = "CLI-инструмент"
    elif _has(text, "сайт", "лендинг", "web", "веб", "приложени"):
        project_type = "веб-приложение"

    platform = ""
    if _has(text, "telegram"):
        platform = "Telegram"
    elif _has(text, "сайт", "лендинг", "web", "веб"):
        platform = "веб-сайт"
    elif _has(text, "мобильн", "mobile", "android", "ios"):
        platform = "мобильное приложение"
    elif _has(text, "десктоп", "desktop"):
        platform = "десктоп-приложение"
    elif _has(text, "cli", "консольн", "командная строка"):
        platform = "CLI"
    elif _has(text, "бот", "bot"):
        platform = "чат-бот"

    roles = _list_from(
        text,
        ("администраторы", "админ", "admin"),
        ("сотрудники", "сотрудник", "менеджер", "manager"),
        ("гости", "гост", "guest"),
        ("клиенты", "клиент", "покупател", "customer"),
        ("пользователи", "пользовател", "user"),
        ("студенты", "студент", "ученик", "student"),
    )

    features = _list_from(
        text,
        ("бронирование", "бронир"),
        ("поиск", "search", "фильтр"),
        ("оплата", "оплат", "платеж", "платёж", "payment"),
        ("каталог", "catalog"),
        ("отчёты", "отчет", "report", "аналитик"),
        ("управление задачами", "задач", "task", "todo"),
        ("регистрация", "регистрац"),
        ("уведомления", "уведомл"),
        ("экспорт", "export"),
    )

    integrations = _list_from(
        text,
        ("платежи", "оплат", "платеж", "платёж", "payment", "stripe", "юкасс"),
        ("Telegram", "telegram"),
        ("email", "e-mail", "почт"),
        ("SMS", "sms", "смс"),
        ("CRM/1C", "crm", "1с", "1c"),
        ("календарь", "calendar"),
    )

    data_storage = ""
    if _has(text, "postgres", "postgresql"):
        data_storage = "PostgreSQL"
    elif _has(text, "mysql", "mariadb"):
        data_storage = "MySQL"
    elif _has(text, "sqlite"):
        data_storage = "SQLite"
    elif _has(text, "база данных", "бд", "database", "sql"):
        data_storage = "реляционная база данных"
    elif _has(text, "файл", "json", "csv", "excel"):
        data_storage = "файловое хранилище"

    auth = ""
    if _has(text, "без авторизац", "no auth", "публичн", "анонимн"):
        auth = "без авторизации"
    elif _has(
        text, "авторизац", "аутентификац", "логин", "login", "auth", "пароль", "регистрац"
    ):
        auth = "требуется авторизация"

    notifications = ""
    if _has(text, "уведомл", "notif", "напомин", "рассылк"):
        notifications = "уведомления"
    elif _has(text, "подтвержден") and _has(text, "email", "почт"):
        notifications = "подтверждения по email"

    constraints = _list_from(
        text,
        ("ограниченный бюджет", "бюджет", "budget"),
        ("срок", "дедлайн", "deadline"),
        ("локальный запуск", "on-prem", "офлайн", "offline"),
        ("без внешних сервисов", "no external services"),
    )

    acceptance_criteria = _list_from(
        text,
        ("критерии приёмки", "критерий приёмки", "критерии приемки", "acceptance criteria"),
        ("тесты проходят", "tests pass"),
    )

    priorities = _list_from(
        text,
        ("приоритет", "priority"),
        ("сначала скорость", "быстрее"),
        ("сначала качество", "качество"),
    )

    return GoalAnalysis(
        goal=goal.strip(),
        project_type=project_type,
        users=", ".join(roles),
        core_features=features,
        platform=platform,
        integrations=integrations,
        data_storage=data_storage,
        auth=auth,
        notifications=notifications,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        priorities=priorities,
    )


def answered_fields(
    answers: object = None,
) -> Tuple[str, ...]:
    """The specification fields an answer covers, including explicitly skipped ones.

    ``answers`` may be a mapping of ``field -> text`` or an iterable of
    :class:`app.discovery.models.ClarificationAnswer`. A skipped answer counts as
    answered: the user declined, so the question must not be asked again.
    """

    fields_answered: list[str] = []
    if answers is None:
        return ()
    if isinstance(answers, Mapping):
        for key, value in answers.items():
            if str(value).strip():
                fields_answered.append(str(key))
        return tuple(fields_answered)

    for answer in answers:
        question_id = str(getattr(answer, "question_id", "") or "")
        if not question_id:
            continue
        if bool(getattr(answer, "redacted", False)):
            # A redacted answer carries no usable information, so the area it
            # would have covered is still askable: the user is asked again rather
            # than silently left with an area nobody can fill.
            continue
        field_name = question_id[2:] if question_id.startswith("q-") else question_id
        if field_name in AREA_ORDER:
            fields_answered.append(field_name)
    return tuple(fields_answered)


def missing_areas(
    analysis: GoalAnalysis,
    answers: object = None,
) -> Tuple[str, ...]:
    """The fixed-set areas that are still unknown, in :data:`AREA_ORDER`.

    An area is known when the goal text states it or the user answered it with
    real text. An area the user explicitly skipped stays unknown for the
    specification; :func:`app.discovery.questions.clarification_questions`
    excludes it from the question list, which is what lets a skip finish the
    conversation instead of looping on the same question.
    """

    answered_with_text: set[str] = set()
    if isinstance(answers, Mapping):
        answered_with_text = {
            str(key) for key, value in answers.items() if str(value).strip()
        }
    elif answers is not None:
        for answer in answers:
            question_id = str(getattr(answer, "question_id", "") or "")
            field_name = (
                question_id[2:] if question_id.startswith("q-") else question_id
            )
            text = str(getattr(answer, "text", "") or "")
            if (
                field_name
                and text.strip()
                and not bool(getattr(answer, "skipped", False))
                and not bool(getattr(answer, "redacted", False))
            ):
                answered_with_text.add(field_name)
    return tuple(
        area
        for area in AREA_ORDER
        if not analysis.has(area) and area not in answered_with_text
    )
