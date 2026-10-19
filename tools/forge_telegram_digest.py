#!/usr/bin/env python3
"""Short, fluff-free digest of one Telegram chat -- from a local export.

Two independent halves, so the tool is useful without any provider configured:

* **Offline (default)** -- parses a Telegram Desktop "Export chat history" the
  owner already has: a directory with ``result.json``, a single ``result.json``,
  ``messages.html``, or a plain ``.txt`` transcript.  It drops service messages,
  stickers, GIFs, voice-only posts, empty and pure-emoji bodies, and renders a
  compact digest.  This path imports nothing outside the standard library and
  opens no socket.
* **Provider summary (optional)** -- when a real provider is already enabled in
  the project settings and has its credential configured, the same digest is
  produced through the existing ``app.agents.providers`` interface.  The tool
  never enables a provider, never widens the paid-provider policy, and never
  reads ``.env`` itself: the project's ``load_settings`` and ``SecretStore`` do
  that.  If no provider is usable it falls back to the local extractive engine
  (``--engine auto``) or exits with code 3 (``--engine provider``).

Usage
-----
    python tools/forge_telegram_digest.py --export path/to/Telegram\\ Desktop\\ Chat
    python tools/forge_telegram_digest.py --export result.json --since 2026-10-01 \\
        --limit 2000 --format md --out digest.md --lang ru --engine auto

Exit codes: 0 success, 2 bad input, 3 provider requested but unavailable.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROGRAM = "forge_telegram_digest"

#: The tool lives in ``tools/``; the provider surface lives in ``app/``.  Adding
#: the repository root keeps the offline path independent of the current
#: directory and lets the optional provider path import the project normally.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Plain-text normalisation, noise filtering and labels
# ---------------------------------------------------------------------------

#: Emoji, pictographs, dingbats, variation selectors and joiners.  Stripped from
#: retained bodies so a message like "Готово 🚀" becomes "Готово"; a body made
#: only of these characters is dropped as noise.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF"
    "\uFE0F\u200D\u20E3\u2705\u274C\u2757\u2753"
    "]"
)

_URL_RE = re.compile(r"https?://[^\s<>()]+|www\.[^\s<>()]+", re.IGNORECASE)

#: Telegram Desktop media kinds that carry no text worth summarising.
_NOISE_MEDIA = {
    "sticker",
    "animation",
    "gif",
    "voice_message",
    "video_message",
    "round_video",
    "video_note",
}

#: Russian and English function words.  Keeping this list in the tool (instead
#: of a dependency) is deliberate: the extractive fallback must stay
#: dependency-free and deterministic.
_STOPWORDS = frozenset(
    """
    и в во не что он на я с со как а то все всё она так его но да ты к у же вы
    за бы по только ее её мне было вот от меня еще ещё нет о из ему теперь когда
    даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам
    ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их
    чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот
    того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее
    сейчас были куда зачем всех никогда можно при наконец два об другой хоть после
    над больше тот через эти нас про всего них какая много разве три эту моя
    впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им более
    всегда конечно всю между это очень также просто наш кстати поэтому однако
    давай давайте пожалуйста привет спасибо ок нужно нужен нужна нужны будет
    будут могут может должны должен должна думаю кажется вообще типа вроде
    конечно сегодня вчера завтра сейчас тогда почему как-то что-то кто-то
    где-то когда-то куда-то откуда-то
    the a an and or but if then else of to in on at for with from by as is are was
    were be been being it its this that these those i you he she we they me him
    her us them my your his our their not no yes so just very can could should
    would will shall may might must do does did done have has had having about
    into over under again further once here there when where why how all any both
    each few more most other some such only own same too s t don now
    """.split()
)

#: Word forms are grouped by their first five letters when long enough.  This
#: is not a morphological analyser; it only merges obvious inflectional variants
#: ("интеграция"/"интеграцией", "release"/"releases") so one topic is not split
#: across several keyword rows.  The most frequent original surface is shown.
_GROUP_PREFIX = 5

#: Decision / agreement markers.  Word-bounded so "перерешили" does not count.
_DECISION_RE = re.compile(
    r"(?<![А-Яа-яЁёA-Za-z])(?:"
    r"реш(?:ил[а-я]*|ени[а-я]*|ено|им|аем)"
    r"|договорил[а-я]*|договоримся|договор[её]нност[а-я]*"
    r"|итог[а-я]*"
    r"|сдела[ею]м"
    r"|зафиксир[а-я]*"
    r"|утвердил[а-я]*"
    r"|согласовал[а-я]*"
    r"|\b(?:decided|agreed|resolution|conclusion|action\s+item|final\s+decision)\b"
    r")(?![А-Яа-яЁёA-Za-z])",
    re.IGNORECASE | re.UNICODE,
)

_LABELS: Dict[str, Dict[str, str]] = {
    "ru": {
        "digest": "дайджест чата",
        "engine_local": "Локальная выжимка (без модели): эвристики по тексту, без внешнего API.",
        "engine_provider": "Выжимка модели: {provider} / {model}.",
        "engine_fallback": (
            "Провайдер не использован ({reason}); показана локальная выжимка (без модели)."
        ),
        "summary": "Сводка",
        "decisions": "Решения",
        "speakers": "Кто что говорил",
        "questions": "Открытые вопросы",
        "stats": "Статистика",
        "topics": "Обсуждали: {topics}.",
        "topics_none": "Содержательных тем выделить не удалось.",
        "activity": (
            "{messages} в чате, участников: {participants}; "
            "активнее всех — {leader} ({leader_count})."
        ),
        "decisions_summary": "Решений зафиксировано: {count}. Первое: «{first}».",
        "decisions_none_summary": "Явных решений в переписке не зафиксировано.",
        "questions_summary": "Открытых вопросов: {count}. Первый: «{first}».",
        "questions_none_summary": "Открытых вопросов не осталось.",
        "context": "Ссылок — {links}, вложений — {attachments}.",
        "decisions_none": "Явных решений в переписке не найдено.",
        "questions_none": "Открытых вопросов не найдено.",
        "speakers_none": "Содержательных сообщений нет.",
        "unknown_author": "Неизвестный",
        "no_messages": "В экспорте нет содержательных сообщений для выжимки.",
        "stats_messages": "Сообщений: {count}",
        "stats_period": "Период: {period}",
        "stats_participants": "Участников: {count}",
        "stats_links": "Ссылок: {count}",
        "stats_attachments": "Вложений: {count}",
        "stats_skipped": "Пропущено (служебные и шум): {count}",
        "period_none": "даты не указаны",
        "written": "Дайджест записан: {path}",
        "missing": "Файл или каталог не найден: {path}",
        "unknown_shape": (
            "Неизвестный формат экспорта: {path}. Ожидался result.json с массивом "
            "messages, messages.html или текстовый транскрипт."
        ),
        "bad_json": "Не удалось прочитать JSON {path}: {detail}",
        "provider_unavailable": "Провайдер недоступен: {reason}",
        "provider_none": (
            "ни один реальный провайдер не включён в FORGE_ENABLED_PROVIDERS и не "
            "настроен; используйте --engine local"
        ),
        "bad_output_dir": "Каталог для --out не существует: {path}",
        "messages_one": "сообщение",
        "messages_few": "сообщения",
        "messages_many": "сообщений",
        "participants_one": "участник",
        "participants_few": "участника",
        "participants_many": "участников",
    },
    "en": {
        "digest": "chat digest",
        "engine_local": (
            "Local extractive digest (no model): text heuristics, no external API."
        ),
        "engine_provider": "Model digest: {provider} / {model}.",
        "engine_fallback": (
            "Provider was not used ({reason}); showing the local extractive digest (no model)."
        ),
        "summary": "Summary",
        "decisions": "Decisions",
        "speakers": "Who said what",
        "questions": "Open questions",
        "stats": "Statistics",
        "topics": "Discussed: {topics}.",
        "topics_none": "No substantive topics could be extracted.",
        "activity": (
            "{messages} in the chat, participants: {participants}; "
            "most active — {leader} ({leader_count})."
        ),
        "decisions_summary": "Decisions found: {count}. First: “{first}”.",
        "decisions_none_summary": "No explicit decisions were found in the chat.",
        "questions_summary": "Open questions: {count}. First: “{first}”.",
        "questions_none_summary": "No open questions remain.",
        "context": "Links — {links}, attachments — {attachments}.",
        "decisions_none": "No explicit decisions were found in the chat.",
        "questions_none": "No open questions were found.",
        "speakers_none": "No substantive messages.",
        "unknown_author": "Unknown",
        "no_messages": "The export contains no substantive messages to summarise.",
        "stats_messages": "Messages: {count}",
        "stats_period": "Period: {period}",
        "stats_participants": "Participants: {count}",
        "stats_links": "Links: {count}",
        "stats_attachments": "Attachments: {count}",
        "stats_skipped": "Skipped (service/noise): {count}",
        "period_none": "dates not provided",
        "written": "Digest written: {path}",
        "missing": "File or directory not found: {path}",
        "unknown_shape": (
            "Unknown export shape: {path}. Expected result.json with a messages "
            "array, messages.html, or a plain-text transcript."
        ),
        "bad_json": "Could not read JSON {path}: {detail}",
        "provider_unavailable": "Provider unavailable: {reason}",
        "provider_none": (
            "no real provider is enabled in FORGE_ENABLED_PROVIDERS and configured; "
            "use --engine local"
        ),
        "bad_output_dir": "Output directory does not exist: {path}",
        "messages_one": "message",
        "messages_few": "messages",
        "messages_many": "messages",
        "participants_one": "participant",
        "participants_few": "participants",
        "participants_many": "participants",
    },
}


class InputError(Exception):
    """Bad or missing input; the CLI maps it to exit code 2."""


class ProviderUnavailable(Exception):
    """A provider was requested or selected but cannot produce the digest."""


def _labels(lang: str) -> Dict[str, str]:
    return _LABELS.get(lang, _LABELS["ru"])


def _clean_text(value: str) -> str:
    """Strip emoji and control noise, collapse all whitespace to one space."""

    text = _EMOJI_RE.sub("", value)
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return " ".join(text.split())


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _trim_period(text: str) -> str:
    """Drop a trailing full stop so quotes do not end with ".»"."""

    return text[:-1].rstrip() if text.endswith(".") else text


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    last_two = number % 100
    last = number % 10
    if last == 1 and last_two != 11:
        return one
    if 2 <= last <= 4 and not 12 <= last_two <= 14:
        return few
    return many


def _messages_phrase(lang: str, count: int) -> str:
    labels = _labels(lang)
    if lang == "en":
        word = labels["messages_one"] if count == 1 else labels["messages_many"]
    else:
        word = _plural_ru(
            count, labels["messages_one"], labels["messages_few"], labels["messages_many"]
        )
    return f"{count} {word}"


# ---------------------------------------------------------------------------
# Source model and parsing
# ---------------------------------------------------------------------------


@dataclass
class ChatMessage:
    """One chat entry, already reduced to the fields the digest needs."""

    id: str
    author: str
    text: str
    date: Optional[datetime] = None
    is_service: bool = False
    media_type: str = ""
    has_attachment: bool = False


@dataclass
class ExportData:
    title: str
    messages: List[ChatMessage] = field(default_factory=list)
    service_count: int = 0
    source_kind: str = ""


def _parse_date(value: Any) -> Optional[datetime]:
    """Parse the date shapes Telegram exports use; never raise."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    iso = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(iso)
    except ValueError:
        parsed = None
    if parsed is not None:
        # Telegram writes local wall-clock times; keeping the wall-clock value is
        # what the digest should display, whatever offset the export recorded.
        return parsed.replace(tzinfo=None)
    for pattern in (
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        for candidate in (raw, raw[:19].strip()):
            try:
                return datetime.strptime(candidate, pattern)
            except ValueError:
                continue
    return None


def _join_telegram_text(value: Any) -> str:
    """Flatten the string-or-segments text field of Telegram Desktop JSON."""

    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: List[str] = []
        for segment in value:
            if isinstance(segment, str):
                parts.append(segment)
            elif isinstance(segment, dict):
                text = segment.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _message_from_json(item: Dict[str, Any]) -> ChatMessage:
    author = item.get("from")
    if not isinstance(author, str) or not author.strip():
        actor = item.get("actor") or item.get("from_id")
        author = actor if isinstance(actor, str) and actor.strip() else ""
    media_type = item.get("media_type")
    if not isinstance(media_type, str):
        media_type = ""
    has_attachment = bool(media_type) or any(
        item.get(key) for key in ("photo", "file", "sticker_emoji", "voice_message", "video_file")
    )
    raw_id = item.get("id")
    return ChatMessage(
        id=str(raw_id) if raw_id is not None else "",
        author=author.strip(),
        text=_join_telegram_text(item.get("text")),
        date=_parse_date(item.get("date")),
        is_service=False,
        media_type=media_type.strip().lower(),
        has_attachment=has_attachment,
    )


def _parse_result_json(payload: Any, path: Path) -> ExportData:
    if not isinstance(payload, dict):
        raise InputError(
            _labels("ru")["unknown_shape"].format(path=path)
        )
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list):
        raise InputError(_labels("ru")["unknown_shape"].format(path=path))
    title = payload.get("name")
    if not isinstance(title, str) or not title.strip():
        title = path.parent.name if path.name == "result.json" else path.stem
    export = ExportData(title=title.strip(), source_kind="json")
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        kind = item.get("type", "message")
        if kind != "message":
            export.service_count += 1
            continue
        export.messages.append(_message_from_json(item))
    return export


class _TelegramHTMLParser(HTMLParser):
    """Small, forgiving reader for Telegram Desktop's ``messages.html``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.messages: List[ChatMessage] = []
        self.service_count = 0
        self._div_depth = 0
        self._message_depth = 0
        self._in_message = False
        self._service = False
        self._capture: Optional[str] = None
        self._capture_depth = 0
        self._from_parts: List[str] = []
        self._text_parts: List[str] = []
        self._date_title = ""
        self._message_id = ""

    def handle_starttag(self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        if tag == "br":
            if self._capture == "text":
                self._text_parts.append("\n")
            return
        if tag != "div":
            return
        self._div_depth += 1
        classes = attributes.get("class", "").split()
        if not self._in_message:
            if "message" in classes:
                self._in_message = True
                self._message_depth = self._div_depth
                self._service = "service" in classes
                self._from_parts = []
                self._text_parts = []
                self._date_title = ""
                self._message_id = attributes.get("id", "")
            return
        if self._capture is not None:
            return
        if "from_name" in classes:
            self._capture = "from"
            self._capture_depth = self._div_depth
        elif "text" in classes and "message" not in classes:
            self._capture = "text"
            self._capture_depth = self._div_depth
        elif "date" in classes and attributes.get("title"):
            self._date_title = attributes["title"]

    def handle_startendtag(
        self, tag: str, attrs: Sequence[Tuple[str, Optional[str]]]
    ) -> None:
        if tag == "br":
            if self._capture == "text":
                self._text_parts.append("\n")
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._capture == "from":
            self._from_parts.append(data)
        elif self._capture == "text":
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._capture is not None and self._div_depth == self._capture_depth:
            self._capture = None
        if self._in_message and self._div_depth == self._message_depth:
            author = " ".join("".join(self._from_parts).split())
            text = "".join(self._text_parts).strip()
            if self._service:
                self.service_count += 1
            else:
                self.messages.append(
                    ChatMessage(
                        id=self._message_id,
                        author=author,
                        text=html.unescape(text),
                        date=_parse_date(self._date_title),
                        is_service=False,
                    )
                )
            self._in_message = False
        self._div_depth -= 1


def _parse_html(document: str, path: Path) -> ExportData:
    parser = _TelegramHTMLParser()
    try:
        parser.feed(document)
        parser.close()
    except Exception:
        # A malformed HTML tree must not abort the digest: whatever was parsed
        # before the break is still useful.
        pass
    title = path.stem if path.stem.lower() not in {"messages", "index"} else path.parent.name
    return ExportData(
        title=title or path.stem or "Telegram",
        messages=parser.messages,
        service_count=parser.service_count,
        source_kind="html",
    )


_TXT_BRACKET_RE = re.compile(r"^\[(?P<date>[^\]]{4,40})\]\s*(?P<rest>.+)$")
_TXT_DASH_RE = re.compile(
    r"^(?P<date>\d{2}[./]\d{2}[./]\d{4}\s+\d{1,2}:\d{2}(?::\d{2})?)"
    r"\s*[-–—]\s*(?P<rest>.+)$"
)


def _split_txt_line(line: str) -> Optional[Tuple[str, str, Optional[datetime]]]:
    rest = line.strip()
    when: Optional[datetime] = None
    bracket = _TXT_BRACKET_RE.match(rest)
    if bracket:
        when = _parse_date(bracket.group("date"))
        rest = bracket.group("rest").strip()
    else:
        dashed = _TXT_DASH_RE.match(rest)
        if dashed:
            when = _parse_date(dashed.group("date"))
            rest = dashed.group("rest").strip()
    if not rest or ":" not in rest:
        return None
    author, _, body = rest.partition(":")
    author = author.strip()
    if not author or len(author) > 80 or "://" in author:
        return None
    if not any(character.isalpha() for character in author):
        return None
    return author, body.strip(), when


def _parse_plain_text(document: str, path: Path) -> ExportData:
    export = ExportData(title=path.stem or "Telegram", source_kind="text")
    current: Optional[ChatMessage] = None
    for raw_line in document.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        header = _split_txt_line(line)
        if header is not None:
            author, body, when = header
            current = ChatMessage(id=str(len(export.messages) + 1), author=author, text=body, date=when)
            export.messages.append(current)
        elif current is not None:
            current.text = (current.text + "\n" + line.strip()).strip()
    return export


def resolve_export(path: Path) -> Tuple[Path, str]:
    """Return ``(file, kind)`` for any supported export location."""

    if not path.exists():
        raise InputError(_labels("ru")["missing"].format(path=path))
    if path.is_dir():
        for name, kind in (
            ("result.json", "json"),
            ("messages.html", "html"),
            ("messages.htm", "html"),
            ("messages.txt", "text"),
        ):
            candidate = path / name
            if candidate.is_file():
                return candidate, kind
        raise InputError(_labels("ru")["unknown_shape"].format(path=path))
    suffix = path.suffix.lower()
    if suffix == ".json":
        return path, "json"
    if suffix in {".html", ".htm"}:
        return path, "html"
    if suffix in {".txt", ".log"}:
        return path, "text"
    raise InputError(_labels("ru")["unknown_shape"].format(path=path))


def load_export(path: Path) -> ExportData:
    source, kind = resolve_export(path)
    try:
        document = source.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        document = source.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise InputError(f"{source}: {exc.strerror or exc}") from None
    if kind == "json":
        try:
            payload = json.loads(document)
        except json.JSONDecodeError as exc:
            raise InputError(
                _labels("ru")["bad_json"].format(
                    path=source, detail=f"{exc.msg} (строка {exc.lineno})"
                )
            ) from None
        return _parse_result_json(payload, source)
    if kind == "html":
        return _parse_html(document, source)
    return _parse_plain_text(document, source)


# ---------------------------------------------------------------------------
# Selection and extraction
# ---------------------------------------------------------------------------


def prepare_messages(
    messages: Iterable[ChatMessage], since: Optional[date], limit: Optional[int]
) -> List[ChatMessage]:
    """Apply ``--since``/``--limit`` to the non-service messages, in export order.

    ``--limit`` keeps the *most recent* N candidates: a chat digest is about the
    latest part of the conversation, not its first lines.
    """

    candidates = [message for message in messages if not message.is_service]
    if since is not None:
        candidates = [
            message for message in candidates if message.date is not None and message.date.date() >= since
        ]
    if limit is not None and limit > 0:
        candidates = candidates[-limit:]
    return candidates


def _normalise_media(media_type: str) -> str:
    return media_type.strip().lower().replace("-", "_")


def is_noise(message: ChatMessage) -> bool:
    """True for sticker/GIF/voice-only, empty, pure-emoji and <3-char bodies."""

    if _normalise_media(message.media_type) in _NOISE_MEDIA:
        return True
    text = _clean_text(message.text)
    if len(text) < 3:
        return True
    if not any(character.isalnum() for character in text):
        return True
    return False


def _decision_messages(messages: Sequence[ChatMessage], limit: int = 6) -> List[ChatMessage]:
    found: List[ChatMessage] = []
    seen = set()
    for message in messages:
        text = _clean_text(message.text)
        if not text or not _DECISION_RE.search(text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(message)
        if len(found) >= limit:
            break
    return found


def _question_messages(messages: Sequence[ChatMessage], limit: int = 5) -> List[ChatMessage]:
    found: List[ChatMessage] = []
    seen = set()
    for message in messages:
        text = _clean_text(message.text)
        if not text or ("?" not in text and "？" not in text):
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(message)
        if len(found) >= limit:
            break
    return found


def _tokens(text: str) -> List[str]:
    # URLs are counted separately; tokenising them would make "https", "com"
    # and the host name look like discussion topics.
    without_urls = _URL_RE.sub(" ", _clean_text(text))
    return re.findall(r"[A-Za-zА-Яа-яЁё0-9][A-Za-zА-Яа-яЁё0-9-]*", without_urls.lower())


def _group_key(token: str) -> str:
    """Group inflected word forms without a morphology dependency."""

    return token[:_GROUP_PREFIX] if len(token) > _GROUP_PREFIX else token


def _top_keywords(messages: Sequence[ChatMessage], limit: int = 8) -> List[str]:
    buckets: Dict[str, Dict[str, Any]] = {}
    for index, message in enumerate(messages):
        for token in _tokens(message.text):
            if len(token) < 3 or token.isdigit() or token in _STOPWORDS:
                continue
            key = _group_key(token)
            bucket = buckets.setdefault(key, {"count": 0, "first": index, "surfaces": {}})
            bucket["count"] += 1
            surfaces = bucket["surfaces"]
            surfaces[token] = surfaces.get(token, 0) + 1
    ranked = sorted(
        buckets.items(),
        key=lambda entry: (-entry[1]["count"], entry[1]["first"], entry[0]),
    )
    keywords: List[str] = []
    for _key, bucket in ranked[:limit]:
        surfaces = bucket["surfaces"]
        # Most frequent surface wins; on a tie the shorter form is the less
        # inflected one ("интеграция" over "интеграцией").
        surface = sorted(surfaces.items(), key=lambda entry: (-entry[1], len(entry[0]), entry[0]))[0][0]
        keywords.append(surface)
    return keywords


def _keyword_score(text: str) -> int:
    return sum(
        1
        for token in _tokens(text)
        if len(token) >= 3 and not token.isdigit() and token not in _STOPWORDS
    )


@dataclass
class Participant:
    name: str
    count: int
    representative: str


@dataclass
class DigestData:
    title: str
    kept: List[ChatMessage]
    candidates: List[ChatMessage]
    service_count: int
    keywords: List[str]
    participants: List[Participant]
    decisions: List[ChatMessage]
    questions: List[ChatMessage]
    links: int
    attachments: int
    skipped: int
    lang: str


def analyse(
    export: ExportData, candidates: Sequence[ChatMessage], kept: Sequence[ChatMessage], lang: str
) -> DigestData:
    labels = _labels(lang)
    grouped: Dict[str, List[ChatMessage]] = {}
    for message in kept:
        author = message.author.strip() or labels["unknown_author"]
        grouped.setdefault(author, []).append(message)

    participants: List[Participant] = []
    for author, items in grouped.items():
        best = items[0]
        best_score = _keyword_score(best.text)
        for item in items[1:]:
            score = _keyword_score(item.text)
            if score > best_score:  # first message wins ties: export order decides.
                best, best_score = item, score
        representative = _trim_period(_shorten(_clean_text(best.text), 150))
        participants.append(
            Participant(name=author, count=len(items), representative=representative)
        )
    participants.sort(key=lambda item: (-item.count, item.name.casefold(), item.name))

    links = sum(len(_URL_RE.findall(message.text)) for message in kept)
    attachments = sum(1 for message in candidates if message.has_attachment)
    skipped = export.service_count + (len(candidates) - len(kept))
    return DigestData(
        title=export.title,
        kept=list(kept),
        candidates=list(candidates),
        service_count=export.service_count,
        keywords=_top_keywords(kept),
        participants=participants,
        decisions=_decision_messages(kept),
        questions=_question_messages(kept),
        links=links,
        attachments=attachments,
        skipped=max(0, skipped),
        lang=lang,
    )


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _period(data: DigestData) -> str:
    dates = [message.date.date() for message in data.kept if message.date is not None]
    if not dates:
        return _labels(data.lang)["period_none"]
    first, last = min(dates), max(dates)
    if first == last:
        return first.strftime("%d.%m.%Y")
    return f"{first.strftime('%d.%m.%Y')} – {last.strftime('%d.%m.%Y')}"


def _summary_bullets(data: DigestData) -> List[str]:
    labels = _labels(data.lang)
    if not data.kept:
        return [labels["no_messages"]]
    bullets: List[str] = []
    if data.keywords:
        bullets.append(labels["topics"].format(topics=", ".join(data.keywords[:6])))
    else:
        bullets.append(labels["topics_none"])
    leader = data.participants[0] if data.participants else None
    bullets.append(
        labels["activity"].format(
            messages=_messages_phrase(data.lang, len(data.kept)),
            participants=len(data.participants),
            leader=leader.name if leader else labels["unknown_author"],
            leader_count=_messages_phrase(data.lang, leader.count) if leader else "0",
        )
    )
    if data.decisions:
        bullets.append(
            labels["decisions_summary"].format(
                count=len(data.decisions),
                first=_trim_period(_shorten(_clean_text(data.decisions[0].text), 110)),
            )
        )
    else:
        bullets.append(labels["decisions_none_summary"])
    if data.questions:
        bullets.append(
            labels["questions_summary"].format(
                count=len(data.questions),
                first=_shorten(_clean_text(data.questions[0].text), 90),
            )
        )
    else:
        bullets.append(labels["questions_none_summary"])
    if data.links or data.attachments:
        bullets.append(
            labels["context"].format(links=data.links, attachments=data.attachments)
        )
    return bullets


def _render_message_line(message: ChatMessage, data: DigestData, fmt: str, limit: int) -> str:
    labels = _labels(data.lang)
    author = message.author.strip() or labels["unknown_author"]
    text = _trim_period(_shorten(_clean_text(message.text), limit))
    stamp = f" ({message.date.strftime('%d.%m.%Y')})" if message.date else ""
    if fmt == "md":
        return f"- **{author}**{stamp}: «{text}»"
    return f"- {author}{stamp}: «{text}»"


def _local_body(data: DigestData, fmt: str) -> str:
    labels = _labels(data.lang)
    lines: List[str] = []

    def section(key: str) -> None:
        lines.append(f"## {labels[key]}" if fmt == "md" else f"{labels[key].upper()}:")

    section("summary")
    for bullet in _summary_bullets(data):
        lines.append(f"- {bullet}")
    lines.append("")

    section("decisions")
    if data.decisions:
        for message in data.decisions:
            lines.append(_render_message_line(message, data, fmt, 220))
    else:
        lines.append(f"- {labels['decisions_none']}")
    lines.append("")

    section("speakers")
    if data.participants:
        for participant in data.participants[:5]:
            count = _messages_phrase(data.lang, participant.count)
            if fmt == "md":
                lines.append(
                    f"- **{participant.name}** — {count}: «{participant.representative}»"
                )
            else:
                lines.append(f"- {participant.name} — {count}: «{participant.representative}»")
    else:
        lines.append(f"- {labels['speakers_none']}")
    lines.append("")

    section("questions")
    if data.questions:
        for message in data.questions[:5]:
            lines.append(_render_message_line(message, data, fmt, 180))
    else:
        lines.append(f"- {labels['questions_none']}")
    lines.append("")

    section("stats")
    lines.append(f"- {labels['stats_messages'].format(count=len(data.kept))}")
    lines.append(f"- {labels['stats_period'].format(period=_period(data))}")
    lines.append(f"- {labels['stats_participants'].format(count=len(data.participants))}")
    lines.append(f"- {labels['stats_links'].format(count=data.links)}")
    lines.append(f"- {labels['stats_attachments'].format(count=data.attachments)}")
    if data.skipped:
        lines.append(f"- {labels['stats_skipped'].format(count=data.skipped)}")
    return "\n".join(lines).rstrip() + "\n"


_MD_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)


def _markdown_to_plain(text: str) -> str:
    plain = text.replace("**", "").replace("__", "").replace("`", "")
    plain = _MD_HEADING_RE.sub("", plain)
    lines = []
    for line in plain.splitlines():
        stripped = line.strip()
        if stripped:
            lines.append(line.rstrip())
        elif lines and lines[-1] != "":
            lines.append("")
    return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# Optional provider engine (only through the existing project interfaces)
# ---------------------------------------------------------------------------


@dataclass
class ProviderChoice:
    provider: Any
    provider_name: str
    model_name: str


def _load_runtime_settings() -> Any:
    """Load the project's own settings; never parse ``.env`` here."""

    from app.config.settings import load_settings

    return load_settings()


def _key_configured(environment_variable: str) -> bool:
    """Ask the project's existing secret store whether a key is present."""

    from app.config.secrets import SecretStore

    try:
        return bool(SecretStore().get_secret(environment_variable))
    except Exception:
        return False


def _model_for(provider_name: str, settings: Any) -> str:
    if provider_name == "openrouter":
        return str(settings.openrouter_model or "")
    if provider_name == "google":
        return str(settings.gemini_model or "")
    if provider_name == settings.default_provider:
        return str(settings.default_model or "")
    return ""


def _choose_provider(settings: Any) -> Tuple[Optional[ProviderChoice], List[str]]:
    """Pick an already-enabled, already-configured provider or explain why not.

    Nothing is enabled here: the candidate set is exactly
    ``settings.enabled_providers`` minus the offline provider.  A paid provider
    stays unreachable unless ``allow_paid_providers`` is already true, mirroring
    the dispatcher's cost policy.  No credential value is read into a variable
    that outlives the presence check.
    """

    from app.agents.providers.capabilities import CostTier, capabilities_for
    from app.agents.providers.config import ProviderConfig
    from app.agents.providers.factory import ProviderFactory
    from app.config.settings import OFFLINE_PROVIDER_NAME

    reasons: List[str] = []
    names = [name for name in settings.enabled_providers if name != OFFLINE_PROVIDER_NAME]
    default_name = str(settings.default_provider)
    if default_name in names:
        names.remove(default_name)
        names.insert(0, default_name)
    if not names:
        return None, [_labels("ru")["provider_none"]]

    factory = ProviderFactory()
    for name in names:
        try:
            base = factory.create(name)
            model = _model_for(name, settings)
            config = ProviderConfig(
                provider_name=name,
                model_name=model or "unconfigured",
                enabled=True,
                api_key_env_var=base.config.api_key_env_var,
            )
            provider = factory.create(name, config=config)
            capabilities = capabilities_for(provider)
        except Exception as exc:
            reasons.append(f"{name}: {type(exc).__name__}")
            continue
        if capabilities.cost_tier is not CostTier.FREE and not settings.allow_paid_providers:
            reasons.append(f"{name}: платный провайдер запрещён (FORGE_ALLOW_PAID_PROVIDERS=false)")
            continue
        if not model or model == "unconfigured":
            reasons.append(f"{name}: не задана модель (FORGE_DEFAULT_MODEL)")
            continue
        if not capabilities.api_key_env:
            reasons.append(f"{name}: не указана переменная ключа")
            continue
        if not _key_configured(capabilities.api_key_env):
            reasons.append(f"{name}: ключ {capabilities.api_key_env} не найден")
            continue
        return ProviderChoice(provider=provider, provider_name=name, model_name=model), reasons
    return None, reasons


def _provider_prompt(data: DigestData, lang: str) -> str:
    if lang == "en":
        instructions = (
            "You are the editor of very short Telegram chat digests.\n"
            "Below are the messages of the chat {title!r}, in chronological order.\n"
            "Write a digest in English: only the essence, no fluff, no retelling of every "
            "message, no emoji.\n"
            "Answer in Markdown with exactly these sections:\n"
            "## Summary — 3–6 bullets about what the chat was about.\n"
            "## Decisions — what was decided or agreed; say so honestly if nothing was.\n"
            "## Who said what — up to 5 participants: name, message count, one key point.\n"
            "## Open questions — up to 5 questions with authors.\n"
            "## Statistics — messages, participants, period, links, attachments.\n"
            "Do not invent facts. Do not add an introduction or conclusion.\n"
            "If there are no messages, say that there is no data.\n\nMessages:\n"
        )
    else:
        instructions = (
            "Ты — редактор очень коротких дайджестов Telegram-чата.\n"
            "Ниже — сообщения чата «{title}» в хронологическом порядке.\n"
            "Сделай выжимку на русском языке: только суть, без воды, без пересказа "
            "каждого сообщения, без эмодзи.\n"
            "Ответ — Markdown ровно с этими разделами:\n"
            "## Сводка — 3–6 пунктов о том, что обсуждали.\n"
            "## Решения — что решили или о чём договорились; если решений не было, "
            "так и напиши.\n"
            "## Кто что говорил — до 5 участников: имя, число сообщений, одна ключевая мысль.\n"
            "## Открытые вопросы — до 5 вопросов с авторами.\n"
            "## Статистика — сообщений, участники, период, ссылки, вложения.\n"
            "Не выдумывай факты. Не добавляй вступление и заключение.\n"
            "Если сообщений нет — напиши, что данных нет.\n\nСообщения:\n"
        )
    header = instructions.format(title=data.title or "Telegram")
    lines: List[str] = []
    for message in data.kept:
        stamp = message.date.strftime("%d.%m.%Y %H:%M") if message.date else "—"
        text = _clean_text(message.text)
        lines.append(f"[{stamp}] {message.author or _labels(lang)['unknown_author']}: {text}")
    payload = "\n".join(lines)
    # Keep the prompt bounded: the freshest messages matter most.
    limit = 14000
    if len(payload) > limit:
        payload = payload[-limit:]
        payload = payload[payload.find("\n") + 1 :] if "\n" in payload else payload
    return header + payload + "\n"


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _provider_summary(choice: ProviderChoice, data: DigestData, lang: str) -> Tuple[str, str, str]:
    """Call the chosen provider through its existing ``generate`` interface."""

    from app.agents.providers.base import ProviderError, ProviderRequest

    prompt = _provider_prompt(data, lang)
    try:
        response = choice.provider.generate(ProviderRequest(prompt=prompt))
    except ProviderError as exc:
        raise ProviderUnavailable(str(exc)) from None
    except Exception as exc:  # no SDK detail is echoed; only its type name.
        raise ProviderUnavailable(type(exc).__name__) from None
    output = _strip_code_fence(str(getattr(response, "output", "") or ""))
    if not output:
        raise ProviderUnavailable("провайдер вернул пустой ответ")
    provider_name = str(getattr(response, "provider_name", "") or choice.provider_name)
    model_name = str(getattr(response, "model_name", "") or choice.model_name)
    return output, provider_name, model_name


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("ожидалось целое число") from None
    if value < 1:
        raise argparse.ArgumentTypeError("значение должно быть больше 0")
    return value


def _iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("ожидался формат YYYY-MM-DD") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM,
        description=(
            "Короткий дайджест одного Telegram-чата из локального экспорта. "
            "По умолчанию работает офлайн, без ключей и сети."
        ),
    )
    parser.add_argument(
        "--export",
        required=True,
        help="каталог с result.json, файл result.json, messages.html или .txt",
    )
    parser.add_argument("--since", type=_iso_date, default=None, help="только сообщения с YYYY-MM-DD")
    parser.add_argument("--limit", type=_positive_int, default=None, help="не более N последних сообщений")
    parser.add_argument("--format", choices=("md", "txt"), default="md", help="формат вывода")
    parser.add_argument("--out", default=None, help="записать результат в файл")
    parser.add_argument("--lang", choices=("ru", "en"), default="ru", help="язык разделов дайджеста")
    parser.add_argument(
        "--engine",
        choices=("auto", "local", "provider"),
        default="auto",
        help="auto: провайдер, если уже включён, иначе локально; local: только эвристики",
    )
    return parser


def _build_document(data: DigestData, body: str, engine_label: str, fmt: str) -> str:
    labels = _labels(data.lang)
    title = data.title.strip() or "Telegram"
    if fmt == "md":
        head = f"# {title} — {labels['digest']}\n\n_{engine_label}_\n\n"
    else:
        head = f"{title} — {labels['digest']}\n\n{engine_label}\n\n"
    return head + body.rstrip() + "\n"


def _local_fallback(data: DigestData, fmt: str, reason: str) -> Tuple[str, str]:
    labels = _labels(data.lang)
    body = _local_body(data, fmt)
    if reason:
        label = labels["engine_fallback"].format(reason=reason)
    else:
        label = labels["engine_local"]
    return body, label


def _write_output(document: str, out_path: Optional[str], lang: str) -> None:
    text = document if document.endswith("\n") else document + "\n"
    if out_path:
        target = Path(out_path)
        if not target.parent.exists():
            raise InputError(_labels(lang)["bad_output_dir"].format(path=target.parent))
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        print(_labels(lang)["written"].format(path=target))
        return
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        buffer = getattr(sys.stdout, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode("utf-8", "replace"))
        else:
            sys.stdout.write(text.encode("ascii", "replace").decode("ascii"))


def run(args: argparse.Namespace) -> int:
    export = load_export(Path(args.export))
    candidates = prepare_messages(export.messages, since=args.since, limit=args.limit)
    kept = [message for message in candidates if not is_noise(message)]
    data = analyse(export, candidates, kept, args.lang)
    labels = _labels(args.lang)

    if args.engine == "local" or not data.kept:
        body = _local_body(data, args.format)
        engine_label = labels["engine_local"]
    else:
        try:
            settings = _load_runtime_settings()
        except Exception as exc:
            if args.engine == "provider":
                raise ProviderUnavailable(type(exc).__name__) from None
            body, engine_label = _local_fallback(data, args.format, type(exc).__name__)
        else:
            choice, reasons = _choose_provider(settings)
            if choice is None:
                reason = "; ".join(reasons[:2]) or labels["provider_none"]
                if args.engine == "provider":
                    raise ProviderUnavailable(reason)
                body, engine_label = _local_fallback(data, args.format, reason)
            else:
                try:
                    output, provider_name, model_name = _provider_summary(
                        choice, data, args.lang
                    )
                except ProviderUnavailable as exc:
                    if args.engine == "provider":
                        raise
                    body, engine_label = _local_fallback(data, args.format, str(exc))
                else:
                    body = output if args.format == "md" else _markdown_to_plain(output)
                    engine_label = labels["engine_provider"].format(
                        provider=provider_name, model=model_name
                    )

    document = _build_document(data, body, engine_label, args.format)
    _write_output(document, args.out, args.lang)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except InputError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return 2
    except ProviderUnavailable as exc:
        print(f"{PROGRAM}: {_labels(args.lang)['provider_unavailable'].format(reason=exc)}", file=sys.stderr)
        return 3
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # Odd, unexpected input must produce a clear message, never a traceback.
        print(
            f"{PROGRAM}: не удалось обработать экспорт: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
