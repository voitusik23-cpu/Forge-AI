"""Concrete questions for one *kind* of task, instead of one generic questionnaire.

Why this module exists
----------------------

:mod:`app.discovery.questions` used to ask one fixed set of project areas -- «Кто
будет пользоваться», «Нужна ли авторизация», «Импорт/экспорт» -- for every idea, so a
mail client and a hotel booking system were interrogated with the same words. The
owner's complaint: «тупые вопросы, одни и те же, целую кучу, вообще не по теме».

This module is the answer: one bank per domain, written in that domain's own language.
A mail bank asks about mailboxes, spam and a digest; a booking bank about slots,
payment, notifications and who confirms a booking. Every question keeps the shape the
interface already renders (``id, text, field, why, optional, control, options``) and
adds one field the card shows as «Почему этот вопрос»: ``relevance`` -- why *this*
decision matters for *this* kind of task.

Deterministic and offline, exactly like the rest of the layer: no model, no provider,
no I/O. The selection rules are:

* a question whose answer is already in the idea is not asked (:attr:`BankQuestion.
  answered_when`);
* a question whose field is already answered -- by the user, in this session or an
  earlier round -- is not asked;
* a question the idea explicitly refuses is not asked: «без оплаты» drops every
  payment question (:attr:`BankQuestion.refused_when`);
* a question that only makes sense once the idea mentions something (a contact form,
  hosting) is asked only then (:attr:`BankQuestion.asked_when`);
* the list is ordered by importance for the domain, deduplicated by field and by
  normalised text, and bounded -- four questions for a short idea, six for a brief;
* :func:`is_relevant` is the guard the optional model path must pass: a question's
  field, or its words, must belong to the domain's own bank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence, Tuple

from app.discovery.analyzer import is_short_idea, mentions, refutes
from app.discovery.domain import DOMAINS
from app.discovery.models import ClarificationOption, ClarificationQuestion

__all__ = [
    "BANKS",
    "DOMAIN_BANKS",
    "LONG_IDEA_MAX_QUESTIONS",
    "SHORT_IDEA_MAX_QUESTIONS",
    "BankQuestion",
    "bank_question",
    "choose_questions",
    "banks_for",
    "is_relevant",
    "merge_questions",
    "normalise_question_text",
]


#: Bounds for the *domain* selector itself. The generic area questions keep the older
#: bound of :mod:`app.discovery.questions`; these two are the new cap the selection
#: function promises, so a bank of eight questions is still a conversation, not a form.
SHORT_IDEA_MAX_QUESTIONS = 4
LONG_IDEA_MAX_QUESTIONS = 6


def normalise_question_text(text: object) -> str:
    """One comparison key for a question: case-folded, single-spaced, unpunctuated."""

    collapsed = " ".join(str(text or "").casefold().split())
    return collapsed.strip(" \t.,;:!?—–-«»\"'")


@dataclass(frozen=True)
class BankQuestion:
    """One concrete question of one domain, plus the rules that make it askable.

    ``answered_when`` / ``asked_when`` / ``refused_when`` are needle lists in the
    vocabulary of :mod:`app.discovery.analyzer`: a Russian stem matches as a substring,
    a Latin token on word boundaries, and a negation makes a mention a refusal. They
    are the difference between "a question about this domain" and "a question about
    *this* idea".
    """

    field: str
    text: str
    why: str
    relevance: str
    control: str
    options: Tuple[Tuple[str, str, bool], ...]
    required: bool = False
    allow_custom: bool = True
    allow_unknown: bool = True
    #: Needles that mean the idea already answered this question.
    answered_when: Tuple[str, ...] = ()
    #: Needles that must be present for the question to make sense at all.
    asked_when: Tuple[str, ...] = ()
    #: Needles whose *refusal* removes the question («без оплаты»).
    refused_when: Tuple[str, ...] = ()
    #: The words that identify this question's topic for :func:`is_relevant`.
    keywords: Tuple[str, ...] = ()

    def question(self) -> ClarificationQuestion:
        """The wire shape the interface already renders, with its relevance."""

        return ClarificationQuestion(
            id=f"q-{self.field}",
            text=self.text,
            field=self.field,
            why=self.why,
            required=bool(self.required),
            control=self.control,
            options=tuple(
                ClarificationOption(value=value, label=label, recommended=recommended)
                for value, label, recommended in self.options
            ),
            allow_custom=bool(self.allow_custom) and self.control != "yes_no",
            allow_unknown=bool(self.allow_unknown),
            relevance=self.relevance,
        )


def _q(
    field: str,
    text: str,
    why: str,
    relevance: str,
    control: str,
    options: Sequence[Tuple[str, str, bool]],
    *,
    required: bool = False,
    answered_when: Sequence[str] = (),
    asked_when: Sequence[str] = (),
    refused_when: Sequence[str] = (),
    keywords: Sequence[str] = (),
) -> BankQuestion:
    """A terse constructor, so the banks below read as data rather than as code."""

    return BankQuestion(
        field=field,
        text=text,
        why=why,
        relevance=relevance,
        control=control,
        options=tuple(options),
        required=required,
        answered_when=tuple(answered_when),
        asked_when=tuple(asked_when),
        refused_when=tuple(refused_when),
        keywords=tuple(keywords),
    )


# --------------------------------------------------------------------------- #
# The banks, one per domain. Order inside a bank is importance order.
# --------------------------------------------------------------------------- #

_MAIL: Tuple[BankQuestion, ...] = (
    _q(
        "users",
        "Кто будет читать эти письма — только вы или ещё несколько человек?",
        "От числа читателей зависят общий ящик и права доступа.",
        "В почте доступ к ящику — первое, что влияет на устройство приложения.",
        "radio",
        (
            ("только я", "только я", True),
            ("я и семья", "я и семья", False),
            ("несколько сотрудников", "несколько сотрудников", False),
            ("клиенты", "клиенты", False),
        ),
        required=True,
        answered_when=("только я", "для себя", "семья", "сотрудник", "клиент", "команд"),
        keywords=("письм", "ящик", "читат"),
    ),
    _q(
        "integrations",
        "Какие ящики подключаем — Gmail, Яндекс, Mail.ru или другой IMAP?",
        "Список ящиков задаёт параметры подключения и хранения.",
        "Без списка ящиков невозможно настроить подключение к почте.",
        "checkbox",
        (
            ("gmail", "Gmail", True),
            ("yandex", "Яндекс", False),
            ("mailru", "Mail.ru", False),
            ("imap", "другой IMAP", False),
        ),
        answered_when=("gmail", "яндекс.почт", "яндекс почт", "mail.ru", "imap", "outlook"),
        keywords=("ящик", "imap", "gmail", "яндекс", "mail.ru", "подключ"),
    ),
    _q(
        "spam_action",
        "Что делать со спамом — помечать и складывать в папку или сразу удалять?",
        "Удаление безвозвратно, поэтому правило нужно решить заранее.",
        "Спам — самое частое, что приходится разбирать в почте каждый день.",
        "radio",
        (
            ("mark", "помечать и складывать в папку", True),
            ("delete", "удалять в корзину", False),
            ("keep", "оставлять как есть", False),
        ),
        answered_when=("спам",),
        keywords=("спам", "корзин", "папк"),
    ),
    _q(
        "digest",
        "Нужна ли короткая выжимка по важным письмам или достаточно списка?",
        "Выжимка — отдельная работа и отдельный расход токенов.",
        "Именно выжимка отличает полезный почтовый клиент от простого списка писем.",
        "radio",
        (
            ("list", "только список", True),
            ("short", "короткая выжимка", False),
            ("full", "полная выжимка", False),
        ),
        answered_when=("выжимк", "дайджест", "сводк", "кратко"),
        keywords=("выжимк", "дайджест", "список", "важн"),
    ),
    _q(
        "notifications",
        "Как напоминать о важном письме — всплывающее окно, звук или ничего?",
        "Уведомление — отдельная подсистема, её легко забыть.",
        "Пропущенное важное письмо — то, ради чего такую программу и ставят.",
        "checkbox",
        (
            ("popup", "всплывающее окно", True),
            ("sound", "звук", False),
            ("tray", "значок в трее", False),
        ),
        answered_when=("уведомл", "напомин", "оповещ", "звук"),
        keywords=("уведомл", "напомин", "окно", "звук"),
    ),
    _q(
        "auth",
        "Нужен ли вход в приложение — пароль при запуске или сразу открывать?",
        "Вход защищает локальные письма от чужого взгляда.",
        "Локальная копия переписки — самое чувствительное, что есть в почтовом клиенте.",
        "yes_no",
        (
            ("да", "Да, нужен вход", False),
            ("нет", "Нет, не нужен", True),
        ),
        answered_when=("пароль при запуске", "вход", "авторизац", "логин"),
        keywords=("вход", "пароль", "авторизац"),
    ),
    _q(
        "credentials_storage",
        "Пароль приложения — где хранить: Windows Credential Manager подходит?",
        "Пароль нельзя писать в файл настроек открытым текстом.",
        "Пароль от почтового ящика — первое, что нужно хранить по-настоящему безопасно.",
        "yes_no",
        (
            ("vault", "Да, Windows Credential Manager", True),
            ("other", "Нет, другой способ", False),
        ),
        answered_when=("credential manager", "хранилище учётных данных", "keyring"),
        keywords=("пароль", "хранилищ", "credential"),
    ),
    _q(
        "mail_storage",
        "Хранить письма локально или только показывать то, что на сервере?",
        "Локальная копия ускоряет поиск, но занимает место.",
        "От этого зависит, работает ли поиск по письмам без сети.",
        "radio",
        (
            ("headers", "только заголовки", True),
            ("full", "полная локальная копия", False),
            ("none", "без хранения", False),
        ),
        answered_when=("локальн", "кэш", "копи"),
        keywords=("храни", "локальн", "копи"),
    ),
    _q(
        "search",
        "Нужен ли поиск по письмам — по отправителю, теме и дате?",
        "Поиск определяет индексы и объём хранимых данных.",
        "Найти письмо месячной давности — обычная задача почтового клиента.",
        "yes_no",
        (
            ("да", "Да, нужен", True),
            ("нет", "Нет, хватит списка", False),
        ),
        answered_when=("поиск", "фильтр"),
        keywords=("поиск", "фильтр", "отправител"),
    ),
)


_BOOKING: Tuple[BankQuestion, ...] = (
    _q(
        "users",
        "Кто будет пользоваться бронированием — только вы, гости или администратор стойки?",
        "От ролей зависят экраны, права и уведомления.",
        "У бронирования всегда есть две стороны: тот, кто бронирует, и тот, кто подтверждает.",
        "radio",
        (
            ("только я", "только я", False),
            ("гости", "гости", False),
            ("гости и администратор", "гости и администратор", True),
            ("сотрудники", "сотрудники", False),
        ),
        required=True,
        answered_when=("гост", "администратор", "клиент", "сотрудник", "только я", "для себя"),
        keywords=("гост", "администратор", "пользовател", "бронир"),
    ),
    _q(
        "auth",
        "Как администратор входит в систему — по email и паролю или без входа?",
        "Вход отделяет гостя от того, кто управляет бронями.",
        "Управление бронями без входа означает, что управлять может любой, кто открыл программу.",
        "radio",
        (
            ("email", "email и пароль", True),
            ("none", "без входа, только локально", False),
            ("other", "другое", False),
        ),
        answered_when=("авторизац", "пароль", "логин", "без входа", "вход"),
        keywords=("вход", "пароль", "администратор", "авторизац"),
    ),
    _q(
        "integrations",
        "Как принимать оплату — на месте, онлайн или вообще без оплаты?",
        "Оплата — отдельная интеграция со своим провайдером.",
        "Способ оплаты определяет, нужен ли платёжный сервис вообще.",
        "radio",
        (
            ("online", "онлайн-оплата картой", False),
            ("onsite", "на месте", False),
            ("none", "без оплаты", True),
            ("other", "другое", False),
        ),
        answered_when=("оплат", "платеж", "платёж", "payment"),
        refused_when=("оплат", "платеж", "платёж", "payment"),
        keywords=("оплат", "платеж", "платёж", "карт"),
    ),
    _q(
        "booking_slots",
        "Какие слоты бронировать — часы, дни или ночи?",
        "Единица слота задаёт календарь и проверку пересечений.",
        "От единицы брони зависит вся модель занятости.",
        "radio",
        (
            ("hours", "на часы", False),
            ("days", "на дни", True),
            ("nights", "на ночи", False),
            ("any", "произвольно", False),
        ),
        answered_when=("слот", "ноч", "по часам", "на час", "даты", "дата", "период"),
        keywords=("слот", "дат", "час", "ноч", "календар"),
    ),
    _q(
        "booking_rules",
        "Кто подтверждает бронь — автоматически или вручную?",
        "Ручное подтверждение — отдельный экран и отдельная ответственность.",
        "Правило подтверждения решает, увидит ли гость «забронировано» сразу.",
        "radio",
        (
            ("auto", "автоматически", True),
            ("manual", "вручную администратором", False),
            ("schedule", "по расписанию", False),
        ),
        answered_when=("подтвержд", "вручную", "автоматическ"),
        keywords=("подтвержд", "вручную", "автоматическ"),
    ),
    _q(
        "notifications",
        "Куда приходит подтверждение брони — email, SMS или Telegram?",
        "Уведомление — отдельная подсистема, её легко забыть.",
        "Без подтверждения гость не знает, что бронь принята.",
        "checkbox",
        (
            ("email", "email", True),
            ("sms", "SMS", False),
            ("telegram", "Telegram", False),
        ),
        answered_when=("уведомл", "подтвержд", "напомин", "sms", "смс", "telegram"),
        keywords=("уведомл", "подтвержд", "email", "sms", "telegram"),
    ),
    _q(
        "data_storage",
        "Где хранить брони — SQLite на этом компьютере или PostgreSQL на сервере?",
        "Хранение определяет модель данных и резервную копию.",
        "Брони — это записи, которые нельзя потерять при перезапуске.",
        "select",
        (
            ("sqlite", "SQLite", True),
            ("postgres", "PostgreSQL", False),
            ("file", "файл", False),
            ("unknown", "пока не знаю", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "файл", "хран"),
        keywords=("храни", "баз", "sqlite", "postgres"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — срок, бюджет или только локальный запуск?",
        "Ограничения влияют на выбор решения.",
        "Срок и бюджет решают, что вообще успеет войти в первую версию.",
        "checkbox",
        (
            ("deadline", "жёсткий срок", False),
            ("budget", "ограниченный бюджет", False),
            ("local", "только локальный запуск", True),
            ("none", "без ограничений", False),
        ),
        answered_when=("срок", "бюджет", "дедлайн", "ограничен", "локальн"),
        keywords=("срок", "бюджет", "ограничен", "локальн"),
    ),
)


_SHOP: Tuple[BankQuestion, ...] = (
    _q(
        "catalog",
        "Что в каталоге — товары с фото и ценами или услуги?",
        "Каталог задаёт модель данных и экран списка.",
        "Каталог — то, с чего начинается любой магазин.",
        "radio",
        (
            ("goods", "товары с фото и ценами", True),
            ("services", "услуги", False),
            ("both", "и то и другое", False),
        ),
        answered_when=("каталог", "товар", "услуг"),
        keywords=("каталог", "товар", "услуг", "фото", "цена"),
    ),
    _q(
        "cart",
        "Как оформляется покупка — корзина или сразу «Купить»?",
        "Корзина — отдельный экран и отдельная модель заказа.",
        "От корзины зависит, сколько шагов проходит покупатель.",
        "radio",
        (
            ("cart", "корзина", True),
            ("buy", "сразу «Купить»", False),
            ("both", "и то и другое", False),
        ),
        answered_when=("корзин", "оформлен", "покупк"),
        keywords=("корзин", "покупк", "оформлен", "купить"),
    ),
    _q(
        "payment",
        "Как принимать оплату — онлайн-картой, при получении или без оплаты?",
        "Оплата — отдельная интеграция со своим провайдером.",
        "Способ оплаты определяет, нужен ли платёжный сервис вообще.",
        "radio",
        (
            ("online", "онлайн-картой", False),
            ("cod", "при получении", False),
            ("none", "без оплаты", True),
        ),
        answered_when=("оплат", "платеж", "платёж", "payment"),
        refused_when=("оплат", "платеж", "платёж", "payment"),
        keywords=("оплат", "платеж", "карт"),
    ),
    _q(
        "delivery",
        "Как доставлять — курьер, почта или самовывоз?",
        "Доставка добавляет адрес, стоимость и статусы заказа.",
        "Без решения о доставке заказ нельзя считать завершённым.",
        "checkbox",
        (
            ("courier", "курьер", True),
            ("post", "почта", False),
            ("pickup", "самовывоз", False),
            ("none", "без доставки", False),
        ),
        answered_when=("доставк", "курьер", "самовывоз", "пункт выдачи"),
        keywords=("доставк", "курьер", "самовывоз", "адрес"),
    ),
    _q(
        "notifications",
        "Куда присылать подтверждение заказа — email, SMS или Telegram?",
        "Уведомление — отдельная подсистема, её легко забыть.",
        "Подтверждение заказа — первое, что ждёт покупатель после оплаты.",
        "checkbox",
        (
            ("email", "email", True),
            ("sms", "SMS", False),
            ("telegram", "Telegram", False),
        ),
        answered_when=("уведомл", "подтвержд", "sms", "смс", "telegram"),
        keywords=("уведомл", "подтвержд", "email", "sms"),
    ),
    _q(
        "auth",
        "Нужна ли регистрация покупателя или покупать можно без аккаунта?",
        "Аккаунт добавляет вход, пароли и восстановление доступа.",
        "Регистрация — самая частая причина, по которой покупку не доводят до конца.",
        "yes_no",
        (
            ("да", "Да, нужна регистрация", False),
            ("нет", "Нет, без аккаунта", True),
        ),
        answered_when=("регистрац", "аккаунт", "логин", "авторизац"),
        keywords=("регистрац", "аккаунт", "покупк", "вход"),
    ),
    _q(
        "data_storage",
        "Где хранить товары и заказы — SQLite или сервер?",
        "Хранение определяет модель данных и резервную копию.",
        "Заказы нельзя потерять при перезапуске.",
        "select",
        (
            ("sqlite", "SQLite", True),
            ("postgres", "PostgreSQL", False),
            ("file", "файл", False),
            ("unknown", "пока не знаю", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "файл", "хран"),
        keywords=("храни", "баз", "товар", "заказ"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — срок, бюджет или только локальный запуск?",
        "Ограничения влияют на выбор решения.",
        "Срок и бюджет решают, что успеет войти в первую версию.",
        "checkbox",
        (
            ("deadline", "жёсткий срок", False),
            ("budget", "ограниченный бюджет", False),
            ("local", "только локальный запуск", True),
        ),
        answered_when=("срок", "бюджет", "дедлайн", "ограничен", "локальн"),
        keywords=("срок", "бюджет", "ограничен"),
    ),
)


_BOT: Tuple[BankQuestion, ...] = (
    _q(
        "platform",
        "Где живёт бот — Telegram, VK, Discord или сразу несколько?",
        "Платформа задаёт API, лимиты и способ запуска.",
        "У каждой платформы свои ограничения, и менять её потом дорого.",
        "radio",
        (
            ("telegram", "Telegram", True),
            ("vk", "VK", False),
            ("discord", "Discord", False),
            ("multiple", "сразу несколько", False),
        ),
        answered_when=("telegram", "телеграм", "vk", "вконтакте", "discord", "whatsapp"),
        keywords=("платформ", "telegram", "vk", "discord"),
    ),
    _q(
        "commands",
        "Какие команды должны работать в первую очередь?",
        "Список команд — это и есть объём первой версии.",
        "Бот без команд — это чат, который молчит.",
        "checkbox",
        (
            ("start", "/start и приветствие", True),
            ("order", "оформление заказа", False),
            ("help", "/help и подсказки", False),
            ("settings", "настройки", False),
        ),
        answered_when=("команд", "/start", "/help"),
        keywords=("команд", "/start", "бот"),
    ),
    _q(
        "admin",
        "Кто управляет ботом — только вы или несколько администраторов?",
        "От этого зависят права и панель управления.",
        "Админ-доступ к боту — это доступ к его данным и рассылкам.",
        "radio",
        (
            ("only_me", "только я", True),
            ("admins", "несколько администраторов", False),
            ("staff", "сотрудники", False),
        ),
        answered_when=("админ", "только я", "сотрудник"),
        keywords=("админ", "управля", "прав"),
    ),
    _q(
        "auth",
        "Нужно ли привязывать аккаунт пользователя к его данным?",
        "Привязка задаёт модель пользователя и хранение истории.",
        "Без привязки бот не помнит, кто с ним говорил.",
        "yes_no",
        (
            ("да", "Да, нужно", False),
            ("нет", "Нет, без привязки", True),
        ),
        answered_when=("привяз", "аккаунт", "пользовател"),
        keywords=("аккаунт", "пользовател", "привяз"),
    ),
    _q(
        "data_storage",
        "Где хранить заявки и пользователей — файл или база?",
        "Хранение определяет модель данных и резервную копию.",
        "Заявки, потерянные при перезапуске, — потерянные клиенты.",
        "select",
        (
            ("file", "файл", False),
            ("sqlite", "SQLite", True),
            ("postgres", "PostgreSQL", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "файл", "хран"),
        keywords=("храни", "баз", "заявк"),
    ),
    _q(
        "notifications",
        "Бот только отвечает или ещё и пишет сам — напоминает, присылает отчёты?",
        "Исходящие сообщения — отдельная логика и отдельные разрешения.",
        "Рассылка без согласия — быстрый способ получить блокировку бота.",
        "radio",
        (
            ("replies", "только отвечает", True),
            ("reminders", "напоминает", False),
            ("broadcast", "рассылает", False),
        ),
        answered_when=("уведомл", "напомин", "рассылк"),
        keywords=("напомин", "рассылк", "пишет"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — срок или бюджет?",
        "Ограничения влияют на выбор решения.",
        "Срок и бюджет решают, что успеет войти в первую версию.",
        "checkbox",
        (
            ("deadline", "жёсткий срок", False),
            ("budget", "ограниченный бюджет", True),
        ),
        answered_when=("срок", "бюджет", "дедлайн", "ограничен"),
        keywords=("срок", "бюджет", "ограничен"),
    ),
)


_DASHBOARD: Tuple[BankQuestion, ...] = (
    _q(
        "metrics",
        "Какие метрики на первом экране — выручка, заказы, конверсия?",
        "Список метрик — это и есть содержание дашборда.",
        "Дашборд без выбранных метрик — просто красивые графики.",
        "checkbox",
        (
            ("revenue", "выручка", True),
            ("orders", "заказы", False),
            ("conversion", "конверсия", False),
            ("users", "пользователи", False),
        ),
        answered_when=("метрик", "выручк", "конверси", "kpi"),
        keywords=("метрик", "выручк", "заказ", "конверси"),
    ),
    _q(
        "source",
        "Откуда брать данные — файл, API или база?",
        "Источник определяет способ загрузки и обновления.",
        "Без источника данных дашборд нечего показывать.",
        "radio",
        (
            ("file", "файл (CSV/Excel)", False),
            ("api", "API", False),
            ("database", "база", True),
        ),
        answered_when=("csv", "excel", "api", "баз", "бд", "файл"),
        keywords=("источник", "данн", "api", "файл", "баз"),
    ),
    _q(
        "period",
        "За какой период показывать по умолчанию — день, неделя или месяц?",
        "Период задаёт агрегаты и объём запроса.",
        "«За месяц» и «за день» — разные запросы к данным.",
        "radio",
        (
            ("day", "день", False),
            ("week", "неделя", False),
            ("month", "месяц", True),
            ("custom", "произвольный", False),
        ),
        answered_when=("период", "месяц", "недел", "за день"),
        keywords=("период", "месяц", "недел", "день"),
    ),
    _q(
        "refresh",
        "Как часто обновлять — по кнопке или автоматически?",
        "Автообновление — отдельный цикл и отдельная нагрузка.",
        "Живой дашборд и снимок раз в день — разные вещи.",
        "radio",
        (
            ("manual", "по кнопке", True),
            ("auto", "автоматически", False),
        ),
        answered_when=("обновл", "автоматическ", "реальном времени"),
        keywords=("обновл", "автоматическ", "част"),
    ),
    _q(
        "users",
        "Кто смотрит дашборд — только вы или руководители?",
        "От зрителей зависят доступ и разграничение данных.",
        "Дашборд для руководителей обычно требует входа и прав.",
        "radio",
        (
            ("only_me", "только я", True),
            ("managers", "руководители", False),
            ("team", "команда", False),
        ),
        answered_when=("только я", "руководител", "команд", "сотрудник"),
        keywords=("смотр", "руководител", "пользовател"),
    ),
    _q(
        "data_storage",
        "Где хранить историю метрик — SQLite или сервер?",
        "Хранение определяет, можно ли будет сравнивать периоды.",
        "Без сохранённой истории дашборд показывает только «сейчас».",
        "select",
        (
            ("sqlite", "SQLite", True),
            ("postgres", "PostgreSQL", False),
            ("none", "без хранения", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "хран"),
        keywords=("храни", "истори", "баз"),
    ),
    _q(
        "notifications",
        "Нужны ли оповещения, когда метрика выходит за порог?",
        "Порог и оповещение — отдельная подсистема.",
        "Именно оповещение превращает отчёт в инструмент.",
        "yes_no",
        (
            ("да", "Да, нужны", False),
            ("нет", "Нет, не нужны", True),
        ),
        answered_when=("уведомл", "оповещ", "порог"),
        keywords=("оповещ", "уведомл", "порог"),
    ),
)


_NOTES: Tuple[BankQuestion, ...] = (
    _q(
        "note_format",
        "Заметки только текстовые или с картинками и файлами?",
        "Вложения задают модель хранения и предпросмотр.",
        "Заметки с файлами — это уже хранилище, а не блокнот.",
        "radio",
        (
            ("text", "только текст", True),
            ("images", "текст и картинки", False),
            ("files", "текст, картинки и файлы", False),
        ),
        answered_when=("картинк", "фото", "файл", "вложен"),
        keywords=("заметк", "картинк", "файл", "текст"),
    ),
    _q(
        "organization",
        "Как раскладывать заметки — теги, папки или поиск?",
        "Способ организации задаёт структуру и экраны.",
        "Найти старую заметку — главная задача после её создания.",
        "radio",
        (
            ("tags", "теги", True),
            ("folders", "папки", False),
            ("search", "только поиск", False),
        ),
        answered_when=("тег", "папк", "поиск", "категор"),
        keywords=("тег", "папк", "поиск", "заметк"),
    ),
    _q(
        "search",
        "Нужен ли поиск по тексту заметок?",
        "Поиск определяет индексы и объём хранимых данных.",
        "Заметки бесполезны, если нужную не найти.",
        "yes_no",
        (
            ("да", "Да, нужен", True),
            ("нет", "Нет, хватит списка", False),
        ),
        answered_when=("поиск", "фильтр"),
        keywords=("поиск", "заметк"),
    ),
    _q(
        "sync",
        "Нужна ли синхронизация между устройствами или только этот компьютер?",
        "Синхронизация — это сервер, конфликты и второй режим хранения.",
        "Локальный блокнот и общий — разные объёмы работы.",
        "radio",
        (
            ("local", "только этот компьютер", True),
            ("sync", "синхронизация нужна", False),
        ),
        answered_when=("синхрониз", "устройств", "облак"),
        keywords=("синхрониз", "устройств", "облак"),
    ),
    _q(
        "users",
        "Кто читает заметки — только вы или ещё кто-то?",
        "От читателей зависят доступ и права.",
        "Общие заметки требуют входа и разграничения.",
        "radio",
        (
            ("only_me", "только я", True),
            ("family", "семья", False),
            ("team", "команда", False),
        ),
        answered_when=("только я", "для себя", "семья", "сотрудник", "команд"),
        keywords=("читат", "заметк", "пользовател"),
    ),
    _q(
        "data_storage",
        "Где хранить заметки — один файл или база?",
        "Хранение определяет резервную копию и поиск.",
        "Потерять заметки из-за одного повреждённого файла — обидно.",
        "select",
        (
            ("file", "файл", True),
            ("sqlite", "SQLite", False),
            ("postgres", "PostgreSQL", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "файл", "json"),
        keywords=("храни", "файл", "баз", "заметк"),
    ),
    _q(
        "notifications",
        "Нужны ли напоминания по заметкам?",
        "Напоминание — отдельная подсистема и отдельное разрешение.",
        "Заметка с датой полезна, только если о ней напомнят.",
        "yes_no",
        (
            ("да", "Да, нужны", False),
            ("нет", "Нет, не нужны", True),
        ),
        answered_when=("напомин", "уведомл"),
        keywords=("напомин", "уведомл", "заметк"),
    ),
)


_FINANCE: Tuple[BankQuestion, ...] = (
    _q(
        "users",
        "Кто ведёт учёт — только вы или деньги общие с семьёй?",
        "От числа людей зависят общий бюджет и права.",
        "Личный и семейный учёт расходов — разные модели доступа.",
        "radio",
        (
            ("only_me", "только я", True),
            ("family", "семья", False),
            ("team", "сотрудники", False),
        ),
        required=True,
        answered_when=("только я", "для себя", "семья", "сотрудник", "клиент"),
        keywords=("ведут", "учёт", "семь", "пользовател"),
    ),
    _q(
        "finance_import",
        "Откуда брать траты — вручную, из банковской выписки или CSV?",
        "Импорт — отдельная работа с форматами и ошибками.",
        "Ручной ввод и импорт выписки — разный объём работы.",
        "radio",
        (
            ("manual", "только вручную", True),
            ("csv", "импорт CSV", False),
            ("bank", "банковская выписка", False),
        ),
        answered_when=("импорт", "выписк", "csv", "bank"),
        keywords=("импорт", "выписк", "csv", "трат"),
    ),
    _q(
        "budget",
        "Нужен ли месячный лимит и предупреждение при превышении?",
        "Лимит — отдельная логика подсчёта и уведомления.",
        "Контроль лимита — то, ради чего чаще всего заводят такой учёт.",
        "yes_no",
        (
            ("да", "Да, нужен лимит", False),
            ("нет", "Нет, достаточно отчёта", True),
        ),
        answered_when=("лимит", "бюджет на месяц"),
        keywords=("лимит", "бюджет", "превыш"),
    ),
    _q(
        "categories",
        "Категории трат задавать заранее или создавать по ходу?",
        "Фиксированные категории дают сравнимые отчёты.",
        "Без категорий отчёт по тратам превращается в список.",
        "radio",
        (
            ("fixed", "заранее, фиксированные", True),
            ("any", "создавать по ходу", False),
        ),
        answered_when=("категор",),
        keywords=("категор", "трат"),
    ),
    _q(
        "report_period",
        "За какой период показывать отчёт — месяц, неделя или произвольно?",
        "Период задаёт агрегаты и объём запроса.",
        "«За месяц» и «за неделю» — разные отчёты.",
        "radio",
        (
            ("month", "месяц", True),
            ("week", "неделя", False),
            ("year", "год", False),
            ("custom", "произвольный", False),
        ),
        answered_when=("месяч", "месяц", "недел", "период", "год"),
        keywords=("период", "отчёт", "месяц", "недел"),
    ),
    _q(
        "data_storage",
        "Где хранить траты — один файл на компьютере или база?",
        "Хранение определяет резервную копию и отчёты.",
        "Траты за год в одном файле легко потерять.",
        "select",
        (
            ("json", "JSON-файл", False),
            ("sqlite", "SQLite", True),
            ("postgres", "PostgreSQL", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "файл", "json"),
        keywords=("храни", "файл", "баз", "трат"),
    ),
    _q(
        "currency",
        "В какой валюте считать — только рубли или несколько?",
        "Несколько валют требуют курса и правил округления.",
        "Курс валют — отдельная работа, её лучше решить сразу.",
        "radio",
        (
            ("rub", "только рубли", True),
            ("multi", "несколько валют", False),
        ),
        answered_when=("валют", "доллар", "евро", "курс"),
        keywords=("валют", "курс", "рубл"),
    ),
    _q(
        "backup",
        "Нужна ли резервная копия и куда её сохранять?",
        "Копия — отдельный сценарий и отдельный формат.",
        "Домашние расходы за год стоят того, чтобы их не потерять.",
        "yes_no",
        (
            ("да", "Да, нужна копия", True),
            ("нет", "Нет, не нужна", False),
        ),
        answered_when=("резервн", "копи", "бэкап", "backup"),
        keywords=("резервн", "копи", "backup"),
    ),
)


_SITE: Tuple[BankQuestion, ...] = (
    _q(
        "pages",
        "Сколько страниц нужно — одна или несколько?",
        "Число страниц задаёт структуру и навигацию.",
        "Для визитки чаще достаточно одной страницы с блоками.",
        "radio",
        (
            ("one", "одна страница", True),
            ("few", "2–5 страниц", False),
            ("many", "больше пяти", False),
        ),
        answered_when=("одностраничн", "страниц", "раздел"),
        keywords=("страниц", "одностраничн", "раздел"),
    ),
    _q(
        "blocks",
        "Какие блоки на странице — услуги, примеры работ, контакты, отзывы?",
        "Блоки — это содержание страницы и её объём.",
        "Визитка без примеров работ не показывает, что вы умеете.",
        "checkbox",
        (
            ("services", "услуги", True),
            ("works", "примеры работ", False),
            ("contacts", "контакты", False),
            ("reviews", "отзывы", False),
        ),
        answered_when=("услуг", "работ", "контакт", "отзыв", "блок"),
        keywords=("блок", "услуг", "работ", "контакт", "отзыв"),
    ),
    _q(
        "contact_form",
        "Куда приходит заявка с формы — на почту или в Telegram?",
        "Форма требует получателя, иначе заявки теряются.",
        "Форма без получателя — это кнопка, которая ничего не делает.",
        "radio",
        (
            ("email", "на почту", True),
            ("telegram", "в Telegram", False),
            ("none", "без отправки", False),
        ),
        asked_when=("форма", "заявк", "связаться", "кнопк"),
        answered_when=("заявка приходит", "на почту", "в telegram"),
        keywords=("форм", "заявк", "почт", "telegram"),
    ),
    _q(
        "style",
        "Какой стиль — строгий, тёплый или как у конкурентов?",
        "Стиль задаёт шрифты, цвета и объём вёрстки.",
        "Визитку оценивают по внешнему виду раньше, чем по тексту.",
        "radio",
        (
            ("strict", "строгий", True),
            ("warm", "тёплый", False),
            ("like_competitors", "как у конкурентов", False),
            ("unknown", "пока не знаю", False),
        ),
        keywords=("стил", "шрифт", "цвет", "вид"),
    ),
    _q(
        "hosting",
        "Уже есть домен и хостинг или их нужно выбрать?",
        "Домен и хостинг — отдельный шаг публикации.",
        "Готовый сайт без домена нельзя показать клиенту.",
        "radio",
        (
            ("exists", "домен и хостинг уже есть", True),
            ("need", "нужно выбрать", False),
        ),
        asked_when=("домен", "хостинг", "разместить", "опубликовать", "адрес сайта"),
        keywords=("домен", "хостинг", "опубликова", "адрес"),
    ),
    _q(
        "seo",
        "Нужно ли, чтобы сайт находили в поиске — заголовки и описания?",
        "Метаданные — небольшая, но отдельная работа.",
        "Без заголовков и описаний страницу хуже находят.",
        "yes_no",
        (
            ("да", "Да, нужно", True),
            ("нет", "Нет, не нужно", False),
        ),
        keywords=("поиск", "заголовк", "описани", "яндекс"),
    ),
)


_API: Tuple[BankQuestion, ...] = (
    _q(
        "endpoints",
        "Какие ресурсы и методы нужны в первой версии?",
        "Список ресурсов — это и есть объём API.",
        "API без согласованных ресурсов — это набор случайных ручек.",
        "checkbox",
        (
            ("list", "список записей", True),
            ("one", "одна запись", False),
            ("create", "создание", False),
            ("update", "изменение", False),
        ),
        answered_when=("endpoint", "ручк", "метод", "ресурс"),
        keywords=("ресурс", "метод", "endpoint", "запрос"),
    ),
    _q(
        "api_format",
        "Формат ответа — JSON, XML или оба?",
        "Формат фиксирует контракт и клиентов.",
        "Смена формата потом ломает всех клиентов.",
        "radio",
        (
            ("json", "JSON", True),
            ("xml", "XML", False),
            ("both", "оба", False),
        ),
        answered_when=("json", "xml"),
        keywords=("json", "xml", "формат"),
    ),
    _q(
        "auth",
        "Нужен ли ключ или токен для доступа к API?",
        "Доступ определяет безопасность и модель клиентов.",
        "Открытый API без ключа может использовать кто угодно.",
        "yes_no",
        (
            ("да", "Да, нужен ключ", True),
            ("нет", "Нет, открытый доступ", False),
        ),
        answered_when=("ключ", "токен", "авторизац", "auth"),
        keywords=("ключ", "токен", "доступ", "авторизац"),
    ),
    _q(
        "data_storage",
        "Откуда данные — база, файл или внешний сервис?",
        "Источник определяет модель данных и кэш.",
        "API — это в первую очередь данные, а не маршруты.",
        "radio",
        (
            ("database", "база", True),
            ("file", "файл", False),
            ("external", "внешний сервис", False),
        ),
        answered_when=("sqlite", "postgres", "баз", "бд", "файл"),
        keywords=("данн", "баз", "файл", "источник"),
    ),
    _q(
        "docs",
        "Нужна ли документация OpenAPI/Swagger?",
        "Документация — отдельная работа, но без неё API не отдать.",
        "Без описания API клиенты не знают, что вызывать.",
        "yes_no",
        (
            ("да", "Да, нужна", True),
            ("нет", "Нет, не нужна", False),
        ),
        answered_when=("openapi", "swagger", "документац"),
        keywords=("документац", "openapi", "swagger"),
    ),
    _q(
        "users",
        "Кто будет вызывать API — свой сервис, клиенты или несколько?",
        "От вызывающих зависят версии и права.",
        "Публичный API и внутренний — разные требования.",
        "radio",
        (
            ("own", "только свой сервис", True),
            ("clients", "клиенты", False),
            ("many", "несколько потребителей", False),
        ),
        answered_when=("только я", "клиент", "сервис", "пользовател"),
        keywords=("вызыва", "клиент", "сервис", "пользовател"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — срок, бюджет или только локальный запуск?",
        "Ограничения влияют на выбор решения.",
        "Срок и бюджет решают, что успеет войти в первую версию.",
        "checkbox",
        (
            ("deadline", "жёсткий срок", False),
            ("budget", "ограниченный бюджет", False),
            ("local", "только локальный запуск", True),
        ),
        answered_when=("срок", "бюджет", "дедлайн", "ограничен", "локальн"),
        keywords=("срок", "бюджет", "ограничен"),
    ),
)


_GAME: Tuple[BankQuestion, ...] = (
    _q(
        "genre",
        "Какой жанр — змейка, аркада, квест или головоломка?",
        "Жанр задаёт механику и объём первой версии.",
        "Механика игры — это и есть игра, остальное оформление.",
        "radio",
        (
            ("snake", "змейка", True),
            ("arcade", "аркада", False),
            ("quest", "квест", False),
            ("puzzle", "головоломка", False),
        ),
        answered_when=("жанр", "змейк", "аркад", "квест", "головоломк"),
        keywords=("жанр", "змейк", "аркад", "квест", "головоломк"),
    ),
    _q(
        "controls",
        "Как управлять — клавиатура, мышь или касания?",
        "Управление задаёт движок ввода и платформу.",
        "Игра с неудобным управлением не играется.",
        "checkbox",
        (
            ("keyboard", "клавиатура", True),
            ("mouse", "мышь", False),
            ("touch", "касания", False),
        ),
        answered_when=("клавиатур", "мышь", "мышью", "касан", "тачскрин"),
        keywords=("управлен", "клавиатур", "мыш", "касан"),
    ),
    _q(
        "levels",
        "Сколько уровней в первой версии и растёт ли сложность?",
        "Уровни — это содержание и проверка баланса.",
        "Один бесконечный уровень и десять уровней — разный объём работы.",
        "radio",
        (
            ("one", "один уровень", True),
            ("few", "3–5 уровней", False),
            ("many", "много, с ростом сложности", False),
        ),
        answered_when=("уровн", "сложност"),
        keywords=("уровн", "сложност"),
    ),
    _q(
        "score",
        "Нужен ли счёт и таблица рекордов?",
        "Рекорды — это хранение и отдельный экран.",
        "Без счёта игру быстро бросают.",
        "yes_no",
        (
            ("да", "Да, нужны", True),
            ("нет", "Нет, не нужны", False),
        ),
        answered_when=("счёт", "счет", "рекорд", "очк"),
        keywords=("счёт", "счет", "рекорд", "очк"),
    ),
    _q(
        "users",
        "Игра для вас одного или для нескольких игроков?",
        "Число игроков задаёт сеть и режимы.",
        "Многопользовательская игра — отдельный сетевой слой.",
        "radio",
        (
            ("one", "для одного", True),
            ("local", "для двоих за одним экраном", False),
            ("online", "игра по сети", False),
        ),
        answered_when=("один игрок", "для одного", "двоих", "по сети", "мультиплеер"),
        keywords=("игрок", "сеть", "мультиплеер"),
    ),
    _q(
        "data_storage",
        "Где хранить рекорды — файл рядом с игрой или база?",
        "Хранение определяет, переживут ли рекорды перезапуск.",
        "Рекорды, которые исчезают, никого не мотивируют.",
        "select",
        (
            ("file", "файл рядом с игрой", True),
            ("sqlite", "SQLite", False),
        ),
        answered_when=("sqlite", "баз", "файл", "храни"),
        keywords=("храни", "рекорд", "файл", "баз"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — срок или только этот компьютер?",
        "Ограничения влияют на выбор технологии.",
        "Срок и платформа решают, что успеет войти в первую версию.",
        "checkbox",
        (
            ("deadline", "жёсткий срок", False),
            ("local", "только этот компьютер", True),
            ("budget", "ограниченный бюджет", False),
        ),
        answered_when=("срок", "дедлайн", "локальн", "бюджет"),
        keywords=("срок", "локальн", "ограничен"),
    ),
)


_FILES: Tuple[BankQuestion, ...] = (
    _q(
        "file_operation",
        "Что делать с файлами — сортировать, переименовывать или искать дубликаты?",
        "Операция — это и есть первая версия.",
        "От операции зависит, можно ли менять файлы автоматически.",
        "checkbox",
        (
            ("sort", "сортировать", True),
            ("rename", "переименовывать", False),
            ("duplicates", "искать дубликаты", False),
        ),
        answered_when=("сортир", "переименов", "дубликат", "упорядоч"),
        keywords=("файл", "сортир", "переименов", "дубликат"),
    ),
    _q(
        "file_source",
        "Где лежат файлы — одна папка, несколько папок или весь диск?",
        "Область поиска задаёт скорость и права.",
        "Проход по всему диску и по одной папке — разный объём работы.",
        "radio",
        (
            ("one", "одна папка", True),
            ("few", "несколько папок", False),
            ("disk", "весь диск", False),
        ),
        answered_when=("папк", "диск", "каталог"),
        keywords=("папк", "диск", "каталог", "файл"),
    ),
    _q(
        "preview",
        "Нужен ли предпросмотр изменений перед применением?",
        "Предпросмотр — это отдельный безопасный шаг.",
        "Массовое переименование без предпросмотра легко испортить.",
        "yes_no",
        (
            ("да", "Да, нужен", True),
            ("нет", "Нет, применять сразу", False),
        ),
        answered_when=("предпросмотр", "подтвержд"),
        keywords=("предпросмотр", "изменен", "примен"),
    ),
    _q(
        "safety",
        "Нужна ли корзина или резервная копия перед изменениями?",
        "Безвозвратное изменение файлов — самая дорогая ошибка.",
        "Отмена — то, что отличает инструмент от опасного скрипта.",
        "yes_no",
        (
            ("да", "Да, нужна", True),
            ("нет", "Нет, не нужна", False),
        ),
        answered_when=("корзин", "резервн", "бэкап", "backup"),
        keywords=("корзин", "резервн", "отмен", "backup"),
    ),
    _q(
        "formats",
        "Какие форматы важны — фото, видео, документы?",
        "Форматы задают правила и объём обработки.",
        "Инструмент для фото и для документов — разные вещи.",
        "checkbox",
        (
            ("images", "фото", True),
            ("video", "видео", False),
            ("documents", "документы", False),
        ),
        answered_when=("фото", "видео", "документ", "формат"),
        keywords=("формат", "фото", "видео", "документ"),
    ),
    _q(
        "users",
        "Кто будет пользоваться — только вы?",
        "От пользователей зависят права и интерфейс.",
        "Инструмент над файлами получает доступ ко всему, что видит пользователь.",
        "radio",
        (
            ("only_me", "только я", True),
            ("family", "семья", False),
            ("team", "сотрудники", False),
        ),
        answered_when=("только я", "для себя", "семья", "сотрудник"),
        keywords=("пользу", "файл", "пользовател"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — только этот компьютер, без интернета?",
        "Ограничения влияют на выбор решения.",
        "Работа с файлами без интернета — более простой и безопасный вариант.",
        "checkbox",
        (
            ("local", "только этот компьютер", True),
            ("offline", "без интернета", False),
            ("deadline", "жёсткий срок", False),
        ),
        answered_when=("локальн", "офлайн", "offline", "без интернета", "срок"),
        keywords=("локальн", "интернет", "ограничен"),
    ),
)


_CHAT: Tuple[BankQuestion, ...] = (
    _q(
        "platform",
        "Где общаться — своё приложение, Telegram или локальная сеть?",
        "Платформа задаёт доставку сообщений и запуск.",
        "Чат в мессенджере и собственный чат — разный объём работы.",
        "radio",
        (
            ("own", "своё приложение", True),
            ("telegram", "Telegram", False),
            ("lan", "локальная сеть", False),
        ),
        answered_when=("telegram", "телеграм", "локальн", "своё приложен"),
        keywords=("платформ", "общать", "telegram", "сеть"),
    ),
    _q(
        "rooms",
        "Нужны ли общие комнаты или только личные диалоги?",
        "Комнаты добавляют модель участников и права.",
        "Групповой чат — это уже другая модель данных.",
        "radio",
        (
            ("private", "только личные диалоги", True),
            ("rooms", "общие комнаты", False),
            ("both", "и то и другое", False),
        ),
        answered_when=("комнат", "групп", "канал"),
        keywords=("комнат", "групп", "диалог", "участник"),
    ),
    _q(
        "data_storage",
        "Хранить историю сообщений или показывать только новые?",
        "История задаёт хранение и поиск.",
        "Чат без истории — это обмен репликами, а не переписка.",
        "radio",
        (
            ("history", "хранить историю", True),
            ("live", "только новые", False),
        ),
        answered_when=("истори", "хран", "сообщени хранить"),
        keywords=("истори", "храни", "сообщени"),
    ),
    _q(
        "users",
        "Кто участвует — только вы с друзьями или много людей?",
        "Число участников задаёт масштаб и права.",
        "Чат на двоих и на сотню — разные требования.",
        "radio",
        (
            ("few", "несколько человек", True),
            ("team", "команда", False),
            ("many", "много людей", False),
        ),
        answered_when=("друз", "команд", "сотрудник", "много люд"),
        keywords=("участник", "пользовател", "чат", "люд"),
    ),
    _q(
        "auth",
        "Нужен ли вход по имени или аккаунту?",
        "Вход определяет, можно ли доверять автору сообщения.",
        "Без входа сообщения может писать кто угодно.",
        "yes_no",
        (
            ("да", "Да, нужен вход", True),
            ("нет", "Нет, без входа", False),
        ),
        answered_when=("вход", "аккаунт", "логин", "авторизац"),
        keywords=("вход", "аккаунт", "авторизац"),
    ),
    _q(
        "attachments",
        "Можно ли прикреплять файлы и фото?",
        "Вложения — отдельная подсистема хранения и передачи.",
        "Отправка скриншота — обычная потребность в чате.",
        "yes_no",
        (
            ("да", "Да, можно", True),
            ("нет", "Нет, только текст", False),
        ),
        answered_when=("файл", "фото", "вложен", "картинк"),
        keywords=("файл", "фото", "вложен"),
    ),
    _q(
        "constraints",
        "Есть ли ограничения — только локальная сеть или срок?",
        "Ограничения влияют на выбор решения.",
        "Локальный чат и чат через интернет — разная инфраструктура.",
        "checkbox",
        (
            ("local", "только локальная сеть", True),
            ("deadline", "жёсткий срок", False),
        ),
        answered_when=("локальн", "срок", "дедлайн"),
        keywords=("локальн", "срок", "ограничен"),
    ),
)


#: The bank of every domain. ``other`` has no bank on purpose: an idea this layer did
#: not understand falls back to the generic area questions rather than to a guess.
BANKS: Mapping[str, Tuple[BankQuestion, ...]] = {
    "mail": _MAIL,
    "booking": _BOOKING,
    "shop": _SHOP,
    "bot": _BOT,
    "dashboard": _DASHBOARD,
    "notes": _NOTES,
    "finance": _FINANCE,
    "site": _SITE,
    "api": _API,
    "game": _GAME,
    "files": _FILES,
    "chat": _CHAT,
    "other": (),
}

#: Public alias with a name that reads well from another module.
DOMAIN_BANKS = BANKS


def banks_for(domain: object) -> Tuple[BankQuestion, ...]:
    """The bank of one domain, or an empty tuple for an unknown one."""

    return BANKS.get(str(domain or "").strip().lower(), ())


def bank_question(domain: object, field: object) -> Optional[ClarificationQuestion]:
    """One bank question by field, so a stored answer can still be labelled."""

    wanted = str(field or "").strip()
    for entry in banks_for(domain):
        if entry.field == wanted:
            return entry.question()
    return None


def is_relevant(question: ClarificationQuestion, domain: object) -> bool:
    """Whether one question belongs to the domain's own bank -- the relevance guard.

    A question passes when its ``field`` is a bank field, or when its words name a
    topic the bank asks about. A question about «погода» for a mail client fails, and
    so does a question about notifications for a static page: neither field nor words
    belong to that bank. This is what the optional model path is checked against, so a
    model can never smuggle a generic question into a domain's card.
    """

    bank = banks_for(domain)
    if not bank:
        return False
    if any(entry.field == question.field for entry in bank):
        return True
    text = f"{question.text} {question.field}".casefold()
    for entry in bank:
        for needle in entry.keywords or entry.answered_when:
            if needle.startswith("re:"):
                continue
            if needle.casefold() in text:
                return True
    return False


def choose_questions(
    idea: object,
    domain: object,
    already_known: Iterable[str] = (),
) -> Tuple[ClarificationQuestion, ...]:
    """The questions worth asking about *this* idea in *this* domain.

    ``already_known`` is the set of fields the user (or an earlier round) answered.
    The result is ordered by importance for the domain, deduplicated by field and by
    normalised text, and bounded: four for a short idea, six for a detailed one.
    """

    key = str(domain or "").strip().lower()
    if key not in DOMAINS or key == "other":
        return ()
    known = {str(field).strip() for field in already_known if str(field).strip()}
    limit = SHORT_IDEA_MAX_QUESTIONS if is_short_idea(idea) else LONG_IDEA_MAX_QUESTIONS
    chosen: list[ClarificationQuestion] = []
    seen_fields: set[str] = set()
    seen_texts: set[str] = set()
    for entry in banks_for(key):
        if entry.field in known or entry.field in seen_fields:
            continue
        if entry.asked_when and not mentions(idea, *entry.asked_when):
            continue
        if entry.answered_when and mentions(idea, *entry.answered_when):
            continue
        if entry.refused_when and refutes(idea, *entry.refused_when):
            continue
        text_key = normalise_question_text(entry.text)
        if text_key in seen_texts:
            continue
        question = entry.question()
        if not is_relevant(question, key):
            # Unreachable for a bank entry by construction; kept so a future entry that
            # gains a foreign field cannot bypass the guard.
            continue
        chosen.append(question)
        seen_fields.add(entry.field)
        seen_texts.add(text_key)
        if len(chosen) >= limit:
            break
    return tuple(chosen)


def merge_questions(
    primary: Sequence[ClarificationQuestion],
    fallback: Sequence[ClarificationQuestion],
    *,
    limit: int,
) -> Tuple[ClarificationQuestion, ...]:
    """Two ordered lists merged into one, without duplicates, bounded by ``limit``.

    ``primary`` (the optional model's questions) comes first: they are the ones written
    for this exact idea. A field or a question text already present wins once, so the
    same decision can never be asked twice.
    """

    merged: list[ClarificationQuestion] = []
    seen_fields: set[str] = set()
    seen_texts: set[str] = set()
    for question in tuple(primary) + tuple(fallback):
        field = str(question.field or "").strip()
        if not field or field in seen_fields:
            continue
        text_key = normalise_question_text(question.text)
        if not text_key or text_key in seen_texts:
            continue
        merged.append(question)
        seen_fields.add(field)
        seen_texts.add(text_key)
        if len(merged) >= max(0, int(limit)):
            break
    return tuple(merged)
