"""What kind of task the idea is, read from the idea's own words.

Why this module exists
----------------------

The questions used to come from one fixed list of project areas, so a mail client and
a hotel booking system were asked the same things -- «Кто будет пользоваться», «Нужна
ли авторизация» -- regardless of what the person had just described. The owner's
complaint was exact: «тупые вопросы, одни и те же, целую кучу, вообще не по теме».
The first fact a useful question needs is therefore *what this is*.

The classification is deterministic and offline: fixed Russian/Latin rules, no model,
no provider, no I/O. The same idea always yields the same domain, the same confidence
and the same evidence, so a session file and a test cannot disagree.

Confidence and evidence are published, not hidden: the interface shows «Задача понята
как: почтовый клиент — 72 %», and a low-confidence or unknown idea falls back to the
generic area questions rather than pretending to have understood it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from app.discovery.analyzer import mentions

__all__ = [
    "DOMAINS",
    "DOMAIN_LABELS",
    "MIN_CONFIDENCE",
    "DomainGuess",
    "detect_domain",
    "domain_label",
]


#: The domains this layer understands, most specific first. The order breaks a tie
#: between two equally strong readings: «сайт для бронирования» is booking, never a
#: generic site, because booking is declared before site.
DOMAINS: Tuple[str, ...] = (
    "mail",
    "booking",
    "shop",
    "bot",
    "dashboard",
    "notes",
    "finance",
    "site",
    "api",
    "game",
    "files",
    "chat",
    "other",
)

#: The one line the interface shows for a domain. «other» is honest, not a guess.
DOMAIN_LABELS: Mapping[str, str] = {
    "mail": "почтовый клиент",
    "booking": "система бронирования",
    "shop": "интернет-магазин",
    "bot": "чат-бот",
    "dashboard": "дашборд",
    "notes": "заметки",
    "finance": "учёт финансов",
    "site": "сайт-визитка",
    "api": "API-сервис",
    "game": "игра",
    "files": "работа с файлами",
    "chat": "чат",
    "other": "другая задача",
}

#: Below this confidence the idea is treated as «other»: a wrong domain would produce
#: confident but irrelevant questions, which is the very defect this module fixes.
MIN_CONFIDENCE = 0.6

#: A needle may be a plain stem, or ``re:<pattern>`` when a plain stem is unsafe. The
#: bot reading is the measured example: «работать» contains «бота», so «бот» as a bare
#: substring classified a normal brief as a chat-bot. The regex keeps the word.
_BOT_PATTERN = re.compile(
    r"(?<![а-яёa-z])(?:бот|бота|боты|боту|ботом|ботов|боте|bots?)(?![а-яёa-z])"
)

#: ``(needle, weight)`` pairs per domain. Weight 3 is a word that names the domain
#: outright, 2 a strong signal, 1 a supporting one. A weight is not a probability; the
#: reported confidence is derived from the total and the margin (see ``detect_domain``).
_DOMAIN_RULES: Tuple[Tuple[str, Tuple[Tuple[str, int], ...]], ...] = (
    (
        "mail",
        (
            ("почта", 3),
            ("почты", 3),
            ("почту", 3),
            ("почте", 3),
            ("почтой", 3),
            ("почтов", 3),
            ("письм", 2),
            ("ящик", 2),
            ("imap", 3),
            ("smtp", 2),
            ("gmail", 2),
            ("outlook", 2),
            ("mail.ru", 2),
            ("спам", 1),
            ("входящ", 1),
            ("email", 1),
            ("e-mail", 1),
        ),
    ),
    (
        "booking",
        (
            ("бронир", 3),
            ("booking", 3),
            ("резервир", 2),
            ("отель", 2),
            ("отел", 2),
            ("гостиниц", 2),
            ("апартамент", 2),
            ("хостел", 2),
            ("слот", 2),
            ("расписани", 1),
            ("туры", 1),
        ),
    ),
    (
        "shop",
        (
            ("магазин", 3),
            ("shop", 3),
            ("маркетплейс", 3),
            ("корзин", 3),
            ("каталог", 2),
            ("товар", 2),
            ("доставк", 2),
            ("продаж", 2),
            ("заказ", 1),
            ("скидк", 1),
            ("store", 1),
        ),
    ),
    (
        "bot",
        (
            (f"re:{_BOT_PATTERN.pattern}", 2),
            ("telegram", 2),
            ("телеграм", 2),
            ("discord", 2),
            ("чат-бот", 3),
            ("вебхук", 1),
            ("webhook", 1),
        ),
    ),
    (
        "dashboard",
        (
            ("дашборд", 3),
            ("dashboard", 3),
            ("метрик", 2),
            ("kpi", 2),
            ("аналитик", 2),
            ("мониторинг", 2),
            ("статистик", 2),
            ("график", 1),
            ("отчёт", 2),
            ("отчет", 2),
            ("панель управления", 2),
            ("админ-панель", 2),
        ),
    ),
    (
        "notes",
        (
            ("заметк", 3),
            ("блокнот", 3),
            ("список дел", 3),
            ("todo", 2),
            ("to-do", 2),
            ("дневник", 2),
            ("markdown", 1),
            ("черновик", 1),
        ),
    ),
    (
        "finance",
        (
            ("расход", 3),
            ("доход", 2),
            ("трат", 2),
            ("бюджет", 2),
            ("финанс", 3),
            ("бухгалт", 2),
            ("expense", 3),
            ("копилк", 2),
            ("деньг", 2),
            ("платеж", 1),
            ("платёж", 1),
        ),
    ),
    (
        "site",
        (
            ("сайт-визитк", 3),
            ("сайт визитк", 3),
            ("лендинг", 3),
            ("landing", 3),
            ("визитк", 2),
            ("одностраничн", 2),
            ("портфолио", 2),
            ("страниц", 1),
            ("сайт", 1),
        ),
    ),
    (
        "api",
        (
            ("api", 3),
            ("микросервис", 3),
            ("openapi", 3),
            ("swagger", 3),
            ("endpoint", 2),
            ("webhook", 2),
            ("rest", 2),
        ),
    ),
    (
        "game",
        (
            ("игр", 2),
            ("game", 3),
            ("шутер", 3),
            ("аркад", 3),
            ("тетрис", 3),
            ("змейк", 2),
            ("квест", 2),
            ("unity", 2),
            ("геймпл", 3),
            ("уровн", 1),
        ),
    ),
    (
        "files",
        (
            ("файл", 2),
            ("папк", 2),
            ("переименов", 3),
            ("дубликат", 2),
            ("резервн", 2),
            ("backup", 2),
            ("архив", 2),
            ("сортировк", 1),
            ("хранилищ", 1),
        ),
    ),
    (
        "chat",
        (
            ("мессенджер", 3),
            ("переписк", 2),
            ("чат", 2),
            ("диалог", 2),
            ("сообщени", 1),
            ("webrtc", 2),
            ("групповой чат", 3),
        ),
    ),
)


def domain_label(domain: object) -> str:
    """The human words for one domain, or the domain id when it is unknown."""

    key = str(domain or "").strip().lower()
    return DOMAIN_LABELS.get(key, key or DOMAIN_LABELS["other"])


@dataclass(frozen=True)
class DomainGuess:
    """One classification: the domain, how sure it is, and what made it sure."""

    domain: str
    confidence: float
    evidence: Tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return domain_label(self.domain)

    @property
    def recognised(self) -> bool:
        """Whether the reading is confident enough to drive the question bank."""

        return self.domain != "other" and self.confidence >= MIN_CONFIDENCE

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "label": self.label,
            "confidence": round(float(self.confidence), 2),
            "evidence": list(self.evidence),
        }


def _other(confidence: float = 0.0, evidence: Tuple[str, ...] = ()) -> DomainGuess:
    return DomainGuess(domain="other", confidence=confidence, evidence=evidence)


def _matches(text: str, needle: str) -> bool:
    """Whether one rule needle is *stated* by ``text`` (a refusal is not a statement)."""

    if needle.startswith("re:"):
        return re.search(needle[3:], text) is not None
    return mentions(text, needle)


def detect_domain(idea: object) -> DomainGuess:
    """Classify one idea, deterministically, with a confidence and its evidence.

    A weighted total decides the domain; a tie is broken by :data:`DOMAINS`' own order
    (most specific first). The confidence grows with the total and with the margin over
    the runner-up, and is capped at 0.95: a rule system never claims certainty. An idea
    that matches nothing -- or matches too weakly -- is «other», which makes the caller
    fall back to the generic questions instead of asking about the wrong task.
    """

    text = " ".join(str(idea or "").casefold().split())
    if not text:
        return _other()

    scored: list[Tuple[int, str, Tuple[str, ...]]] = []
    for domain, patterns in _DOMAIN_RULES:
        matched: list[str] = []
        score = 0
        for needle, weight in patterns:
            if _matches(text, needle):
                # The evidence a person can read: the needle without the ``re:`` marker.
                matched.append(needle[3:] if needle.startswith("re:") else needle)
                score += weight
        if score:
            scored.append((score, domain, tuple(dict.fromkeys(matched))))

    if not scored:
        return _other()

    scored.sort(key=lambda item: (-item[0], DOMAINS.index(item[1])))
    best_score, best_domain, evidence = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    confidence = round(
        min(0.95, 0.55 + 0.04 * min(best_score, 8) + 0.01 * min(best_score - runner_up, 4)),
        2,
    )
    if confidence < MIN_CONFIDENCE:
        # Being unsure is an answer: the generic questions are better than a confident
        # question about the wrong domain.
        return _other(confidence=confidence, evidence=evidence)
    return DomainGuess(domain=best_domain, confidence=confidence, evidence=evidence)
