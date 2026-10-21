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
    "SHORT_IDEA_MAX_QUESTIONS",
    "GoalAnalysis",
    "analyze_goal",
    "answered_fields",
    "is_short_idea",
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

#: A short idea is a sentence or two. It gets at most three questions: the owner's rule
#: is that a short idea must not turn into a questionnaire. A longer brief may have more
#: open decisions, and :data:`app.discovery.questions.MAX_QUESTIONS` is its hard bound.
SHORT_IDEA_MAX_QUESTIONS = 3
SHORT_IDEA_MAX_CHARACTERS = 240
SHORT_IDEA_MAX_LINES = 4


def is_short_idea(text: object) -> bool:
    """Whether ``text`` is a short idea rather than a detailed brief.

    Deterministic and length-only: no provider, no model, no guess about meaning. A
    multi-line brief or a long paragraph is "detailed"; everything else is "short".
    """

    value = str(text or "").strip()
    if not value:
        return True
    return (
        len(value) <= SHORT_IDEA_MAX_CHARACTERS
        and len(value.splitlines()) <= SHORT_IDEA_MAX_LINES
    )


#: Surface words that name a *kind* of program, most specific first. Each entry is
#: ``(label, *needles)``; the label is what the specification records. Order matters,
#: so «приложение для рабочего стола» is a desktop app and never a "сайт", while
#: «сайт с оплатой» is still a web app. The two bot kinds are handled by
#: :func:`_bot_mentioned`, because "бот" as a bare stem also matches «работать».
PROJECT_TYPE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("система бронирования", ("бронир", "booking", "отель", "hotel")),
    (
        "интернет-магазин",
        ("интернет-магазин", "e-commerce", "ecommerce", "магазин", "shop", "store"),
    ),
    ("дашборд", ("дашборд", "dashboard", "админ-панель", "панель управления")),
    (
        "десктоп-приложение",
        ("рабочий стол", "рабочем столе", "рабочего стола", "десктоп", "desktop", "windows"),
    ),
    ("мобильное приложение", ("мобильн", "mobile", "android", "ios", "смартфон")),
    # "апи" is deliberately absent: as a bare Cyrillic stem it also occurs inside
    # «записи», which turned a normal expense brief into an API service. The Latin
    # "api" is matched on word boundaries and is the form a brief actually uses.
    ("API-сервис", ("api", "микросервис", "rest", "сервис")),
    ("CLI-инструмент", ("cli", "консольн", "командная строка", "терминал")),
    ("веб-приложение", ("сайт", "лендинг", "web", "веб")),
)

#: Surface and deployment words for the ``platform`` area, most specific first. The
#: first group names where it runs, the second says how it is deployed -- the owner's
#: «локально» / «на этом компьютере» / «в интернете» / «в сети» all answer it.
PLATFORM_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Telegram", ("telegram",)),
    ("веб-сайт", ("сайт", "лендинг", "web", "веб")),
    (
        "десктоп-приложение",
        ("рабочий стол", "рабочем столе", "рабочего стола", "десктоп", "desktop", "windows"),
    ),
    ("мобильное приложение", ("мобильн", "mobile", "android", "ios", "смартфон")),
    ("CLI", ("cli", "консольн", "командная строка", "терминал")),
    (
        "локальный запуск",
        ("локальн", "на этом компьютер", "на моем компьютер", "на моём компьютер", "офлайн", "offline", "local"),
    ),
    (
        "публикация в интернет",
        ("в интернете", "в сети", "в облаке", "online", "deploy", "задепло", "опубликова"),
    ),
)

#: A bot is mentioned by the word "бот" as a word, not by "работать" or "обработка".
#: Latin "bot"/"bots" keeps a word boundary, which ``_has`` already enforces.
_BOT_MENTION = re.compile(r"(?<![а-яёa-z])(?:бот|бота|боты|боту|ботом|ботов|боте)(?![а-яёa-z])")

#: User groups, most specific first. «для себя» and «личн» are the owner's own words for
#: a single-user tool, so they are recognised like any other role.
USER_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("администраторы", ("админ", "admin")),
    ("сотрудники", ("сотрудник", "менеджер", "manager")),
    ("гости", ("гост", "guest")),
    ("клиенты", ("клиент", "покупател", "customer")),
    ("семья", ("семь", "семейн")),
    ("один пользователь", ("только я", "для себя", "личн", "себя")),
    ("пользователи", ("пользовател", "user")),
    ("студенты", ("студент", "ученик", "student")),
)

#: Domain features the idea can state as a word. The first group is generic, the rest
#: are recognised business areas; the labels are what the specification records.
FEATURE_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("бронирование", ("бронир",)),
    ("учёт расходов", ("учёт расходов", "учет расходов", "расход", "трат", "затрат", "бюджет")),
    ("категории", ("категор",)),
    ("поиск", ("поиск", "search")),
    ("фильтрация", ("фильтр", "filter")),
    ("оплата", ("оплат", "платеж", "платёж", "payment")),
    ("каталог", ("каталог", "catalog")),
    ("отчёты", ("отчет", "отчёт", "report", "аналитик")),
    ("управление задачами", ("задач", "task", "todo")),
    ("регистрация", ("регистрац",)),
    ("уведомления", ("уведомл",)),
    ("импорт", ("импорт", "import")),
    ("экспорт", ("экспорт", "export")),
)

#: Verbs a short idea uses to list what the program does. Two of them are an
#: enumeration of features even without a colon -- «добавлять и видеть итог». Each entry
#: is ``(label, *needles)``; the label is what the specification records, never the stem.
FEATURE_VERB_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("добавление записей", ("добавл", "вносить")),
    ("редактирование", ("редактир",)),
    ("удаление", ("удал",)),
    ("просмотр", ("видеть", "смотр", "показ")),
    ("расчёты", ("считать",)),
    ("хранение", ("хранить",)),
    ("учёт", ("вести",)),
)

#: How many enumerated items are read from one idea, and how long one may be. A bound
#: keeps an enumeration from turning into a pasted document.
MAX_ENUMERATED_FEATURES = 6
MAX_ENUMERATED_ITEM_CHARACTERS = 60

#: A bulleted line is an enumeration the idea itself wrote down.
_BULLET = re.compile(r"^[ \t]*[-*•—][ \t]+(?P<item>.+?)[ \t]*$")



def _is_latin_token(needle: str) -> bool:
    return bool(needle) and all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in needle
    )


#: Words that turn a mention into a refusal rather than a requirement. A needle
#: matched right after one of them -- "не использовать онлайн-платежи", "без
#: оплаты" -- is something the project must NOT have, so it must never become a
#: specification value. The list is deliberately tiny and literal: this module
#: derives only what the text states, and a guard against reading a refusal as a
#: requirement is the same rule read in the other direction.
#:
#: The word must start a word of its own: "мне нужен сайт" and "интернет-магазин"
#: contain the letters of "не"/"нет" but state no refusal, so a mention after them
#: is derived exactly as before.
_NEGATION = re.compile(r"(?:^|[^a-zа-яё0-9])(?:нет|не|без|no|not|never|without)\s")

#: How far back a negation still governs the mention. Long enough for "не
#: использовать онлайн-" before "платежи", short enough not to reach across a
#: sentence. A comma ends the window: "это не бот, а сайт с оплатой" states a
#: payment integration, and the comma is what keeps it.
_NEGATION_WINDOW = 24


def _negated_before(text: str, start: int) -> bool:
    """Whether the mention at ``start`` is refused by a negation just before it."""

    window = text[max(0, start - _NEGATION_WINDOW):start]
    # Only the clause the mention belongs to decides; a comma starts a new one.
    clause = window.rsplit(",", 1)[-1]
    return _NEGATION.search(clause) is not None


def _has(text: str, *needles: str) -> bool:
    """Whether the case-folded ``text`` *states* any of ``needles``.

    A short Latin token is matched on word boundaries (so ``api`` does not match
    "rapid"), while a Russian stem is matched as a substring (``бронир`` covers
    "бронирование" and "бронировать"). Both are deterministic and locale-free.

    A mention that a negation governs is not a statement: the next occurrence is
    tried instead, so "не использовать онлайн-платежи" states no payment
    integration while "с оплатой" still does.
    """

    for needle in needles:
        if _is_latin_token(needle):
            pattern = rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])"
            for match in re.finditer(pattern, text):
                if not _negated_before(text, match.start()):
                    return True
        else:
            start = text.find(needle)
            while start >= 0:
                if not _negated_before(text, start):
                    return True
                start = text.find(needle, start + 1)
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


def _labels_from(
    text: str, rules: Tuple[Tuple[str, Tuple[str, ...]], ...]
) -> Tuple[str, ...]:
    """The labels of ``rules`` the text states, in the rules' own order."""

    return tuple(label for label, needles in rules if _has(text, *needles))


def _rule_label(text: str, rules: Tuple[Tuple[str, Tuple[str, ...]], ...]) -> str:
    """The first label whose needles the text states, or ``""``."""

    for label, needles in rules:
        if _has(text, *needles):
            return label
    return ""


def _bot_mentioned(text: str) -> bool:
    """Whether the text says "бот"/"bot" as a word. «Работать» is not a bot."""

    return _BOT_MENTION.search(text) is not None or _has(text, "bot")


def _project_type(text: str) -> str:
    """The kind of program the idea states, or ``""`` when it states none."""

    if _bot_mentioned(text):
        return "Telegram-бот" if _has(text, "telegram") else "чат-бот"
    return _rule_label(text, PROJECT_TYPE_RULES)


def _platform(text: str) -> str:
    """Where the program runs or is deployed, as the idea states it."""

    if _has(text, "telegram"):
        return "Telegram"
    if _bot_mentioned(text):
        return "чат-бот"
    return _rule_label(text, PLATFORM_RULES)


def _enumerated_items(text: str) -> Tuple[str, ...]:
    """The items of the first real enumeration the idea writes down.

    Three bounded forms are recognised, and nothing else:

    * a **bulleted** list ("- добавлять расходы", ...) -- the way a brief writes features;
    * a **colon-introduced** comma list («…расходов: список трат, категории, месячный
      отчёт»);
    * a run of feature **verbs** («добавлять и видеть итог»), which is an enumeration
      without either marker.

    A comma alone is never an enumeration, and an item that carries a clause, a
    negation («не использовать …») or a sentence is not a feature.
    """

    bullets = [
        " ".join(match.group("item").split()).strip(" .;")
        for match in (_BULLET.match(line) for line in str(text).splitlines())
        if match is not None
    ]
    bullets = [item for item in bullets if _is_enumerated_feature(item)]
    if len(bullets) >= 2:
        return tuple(bullets[:MAX_ENUMERATED_FEATURES])

    for line in str(text).splitlines() or [str(text)]:
        if ":" not in line:
            continue
        chunk = line.split(":", 1)[1]
        items = [" ".join(item.split()).strip(" .;") for item in chunk.split(",")]
        if len(items) < 2:
            continue
        if any(not _is_enumerated_feature(item) for item in items):
            continue
        return tuple(items[:MAX_ENUMERATED_FEATURES])
    return ()


def _is_enumerated_feature(item: str) -> bool:
    """Whether one enumerated item reads as a feature rather than a clause."""

    if not item or len(item) > MAX_ENUMERATED_ITEM_CHARACTERS:
        return False
    if any(character in item for character in ";:!?"):
        return False
    if len(item.split()) > 5:
        return False
    return not _NEGATION.search(f" {item} ")


def _verb_features(text: str) -> Tuple[str, ...]:
    """The feature verbs the idea states -- an enumeration without a colon or a bullet."""

    stated = _labels_from(text, FEATURE_VERB_RULES)
    return stated if len(stated) >= 2 else ()


def _features(text: str) -> Tuple[str, ...]:
    """Everything the idea says the program must do, in a stable order, deduplicated."""

    ordered: list[str] = []
    for label in _labels_from(text, FEATURE_RULES):
        ordered.append(label)
    for item in _enumerated_items(text):
        ordered.append(item)
    for verb in _verb_features(text):
        ordered.append(verb)
    seen: set[str] = set()
    unique: list[str] = []
    for item in ordered:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return tuple(unique)


def analyze_goal(goal: object) -> GoalAnalysis:
    """Derive every area the goal text states, deterministically.

    A non-string or empty goal derives nothing; it is reported as missing rather
    than raising, because a session is created before the user has said anything
    useful.
    """

    if not isinstance(goal, str) or not goal.strip():
        return GoalAnalysis()
    text = " ".join(goal.casefold().split())

    # The kind of program and where it runs are read from the same surface words, most
    # specific first, so «приложение для рабочего стола» is a desktop app rather than a
    # web app with a stray mention of the desktop.
    project_type = _project_type(text)
    platform = _platform(text)
    roles = _labels_from(text, USER_RULES)
    features = _features(text)

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
    elif _has(text, "база", "бд", "database", "sql"):
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
