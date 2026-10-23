"""Where a delivered project's files really live: one visible folder per project.

A lifecycle project owns a working tree. Until this module existed that tree was a
hidden subdirectory of the project store (``.forge/projects/<id>/workspace``), which
is correct for the record and useless for the owner: he could not open it in Explorer,
could not open it in VS Code, and had no folder he could keep.

So a project's **files** now live in a normal folder under a root the owner chose
(``%USERPROFILE%\\Desktop\\Forge Projects`` by default), named

```
<root>/<project name> (proj-<id>)
```

and the *record* -- versions, manifest, run links -- stays where it always was, in
``.forge/projects/<id>/``. Only the files the owner cares about are visible.

This module is the single place that decides three things, and it decides them
without touching a record:

* how a project folder is named (:func:`visible_folder`);
* how it is rendered for a payload when it lives outside the repository
  (:func:`published_folder_path`);
* whether a folder handed back to the store is really inside a root the owner
  configured (:func:`prove_folder`).

``prove_folder`` is the boundary re-proof. It refuses a relative path, a ``..``
segment, a device path, a path whose real (link-resolved) location leaves every
allowed root, and a path inside a ``.git`` directory. That is what keeps a symlink or
junction planted inside the project root from putting project files outside it.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional, Tuple

from app.projects.models import ProjectError

__all__ = [
    "FALLBACK_PROJECT_NAME",
    "MAX_DERIVED_NAME_CHARS",
    "MAX_FOLDER_NAME_CHARS",
    "UnsafeProjectFolder",
    "canonical",
    "count_files",
    "derive_project_name",
    "is_within",
    "is_fallback_project_name",
    "prove_folder",
    "published_folder_path",
    "safe_project_folder_name",
    "visible_folder",
]

#: How much of the owner's project name becomes a folder name. Long enough to read,
#: short enough to stay well inside every Windows path limit once the root and the
#: `` (proj-<12 hex>)`` suffix are added.
MAX_FOLDER_NAME_CHARS = 64

#: How long a *derived* project name may be. Measured on the owner's machine: a folder
#: called «Создай локальное приложение «Почтовый центр» — только Python (ст» is not a
#: name, it is the first 60 characters of a paragraph. 40 is a full sentence of Russian
#: («Сайт для бронирования отелей в Сочи») and still leaves the path far from ``MAX_PATH``.
MAX_DERIVED_NAME_CHARS = 40

#: How many words of the idea become the name when no quoted phrase names the product.
MAX_DERIVED_NAME_WORDS = 5

#: What a project is called when the idea is empty, punctuation, or a single symbol.
#: ``<N>`` is the ordinal the caller supplies -- a folder that exists is numbered by
#: :func:`visible_folder`, so two nameless projects never share a folder.
FALLBACK_PROJECT_NAME = "Проект"

#: Windows device names, which cannot be used as a directory name whatever the suffix.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

#: Everything Windows refuses inside a single path component, plus control characters.
_UNSAFE_CHARACTERS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')

#: A quoted phrase, in every quote pair the owner may plausibly type. The product name is
#: what he put in quotes: «Создай приложение «Почтовый центр»» is a mail client, not a
#: request to create an application called «Создай приложение «Почтовый центр»».
_QUOTED_PHRASE = re.compile(
    r"«([^«»]{2,160})»"
    r"|„([^„“]{2,160})“"
    r"|“([^“”]{2,160})”"
    r'|"([^"]{2,160})"'
    r"|'([^']{2,160})'"
)

#: A leading word that says *what to do* or *how easy it is*, not *what the thing is*.
#: Only stripped from the front of the idea, and only from the front -- repeatedly, so
#: «Хочу простую локальную страницу» loses both «хочу» and «простую» -- because
#: «Приложение для рассылки» must keep its noun. Articles and possessives ("a", "the",
#: "my") are deliberately **not** here: they are the start of a noun phrase
#: ("A trashed project …"), and dropping them mangles the name.
_LEADING_FILLERS = frozenset(
    """
    создай создайте сделай сделайте сделать напиши напишите разработай разработайте
    построй постройте собери соберите реализуй реализуйте
    создать разработать построить собрать реализовать написать
    нужен нужна нужно нужны хочу хотел хотела хотим надо мне нам пожалуйста
    сделаю сделаем создам создадим создаю напишу напишем требуется хотелось
    простой простая простое простую простые простым простого прост
    create make build write develop generate implement realise realize please
    to me can could would
    i i'd i’d want need like
    """.split()
)

#: What a sentence ends with that says nothing about the product. Stripped from the end of
#: the idea *before* the words are chosen, so «…, пожалуйста» cannot eat a word slot.
_TRAILING_INTENT = (
    "если можно",
    "если не сложно",
    "пожалуйста",
    "срочно",
    "if possible",
    "please",
    "asap",
)

#: Words that open the *next* part of a sentence rather than continue the noun phrase.
#: Everything before the first of them is the head ("Телеграм-бот | для отслеживания
#: чатов") and is the only part rewritten into the nominative; the rest of the idea keeps
#: the grammar the owner already used, which is right after a preposition. "a"/"the" are
#: here for English, where the article *starts* the head rather than following it.
_PHRASE_BREAKS = frozenset(
    """
    в во с со к ко о об от до по из за на при для про через без
    у между около после вместо кроме среди вокруг
    the a an of for with to in on at by from
    """.split()
)

#: A clause that describes the thing instead of naming it. The name stops in front of it,
#: and in front of a comma for the same reason: «Лендинг для корпоратива, с регистрацией
#: гостей» is named by its first two words, not by the whole sentence.
_RELATIVE_CLAUSE = re.compile(
    r"(?:\s*,\s*|\s+)(?:"
    r"котор\w*|где|чтобы|чтоб|что|"
    r"which|where|whose|who|whom|that"
    r")\b",
    re.IGNORECASE,
)

#: The colon rule's threshold: «CDP evidence: …» is named by what stands before the colon
#: only while that head is already a phrase (2-5 words) rather than one bare word.
_MIN_COLON_HEAD_WORDS = 2

#: A word that is glued to the next one with a hyphen, as Russian and English already write
#: «Телеграм-бот», «Веб-сайт», «e-mail». Only applied when a *next* word exists.
_COMPOUND_PREFIXES = frozenset(
    """
    веб telegram телеграм чат sms смс email e-mail web
    """.split()
)

#: Loanwords that already end in -у/-ю in every case. Rewriting them would invent a word
#: («Меню» -> «Меня»), so the accusative rule leaves them exactly as typed.
_INDECLINABLE = frozenset(
    """
    меню интервью шоу табу рагу пальто кино фото метро такси
    """.split()
)

#: A word that cannot end a name: it joins the name to something the cap cut off. «Сайт
#: для бронирования отелей в» is not a name; «Сайт для бронирования отелей» is.
_TRAILING_FILLERS = frozenset(
    """
    в во с со к ко о об от до по из за на при для и или а но
    the a an of for with and or to in on at by from
    """.split()
)

#: At least one letter or digit, in any script: "———" and "..." are not a name.
_HAS_WORD_CHARACTER = re.compile(r"[^\W_]", re.UNICODE)

#: A word the accusative rule may touch: Cyrillic letters and inner hyphens only.
_CYRILLIC_WORD = re.compile(r"^[а-яёА-ЯЁ\-]+$")


class UnsafeProjectFolder(ProjectError):
    """A project folder path is not one this layer will resolve or create."""

    code = "unsafe_project_folder"


def canonical(path: object) -> Path:
    """The real location of ``path``, links and junctions resolved.

    ``os.path.realpath`` is used rather than ``Path.resolve`` alone because on Windows
    it also resolves **junctions** and substituted drives, which ``Path.resolve`` does
    not always follow -- and a junction is exactly how a folder that looks contained
    points somewhere else.
    """

    try:
        return Path(os.path.realpath(str(path))).resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover - defensive
        raise UnsafeProjectFolder(
            f"папку проекта не удалось разрешить: {type(exc).__name__}"
        ) from exc


def is_within(candidate: Path, root: Path) -> bool:
    """True when ``candidate`` is ``root`` or lives beneath it. Both must be canonical."""

    if candidate == root:
        return True
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def safe_project_folder_name(name: object) -> str:
    """The owner-readable part of a project folder name.

    The project name is user text, so it is bounded and stripped of the characters a
    file name cannot carry; it is never used alone -- the ``(proj-<id>)`` suffix is
    what makes the folder unique -- so a name that collides with a reserved device
    name only needs a harmless prefix rather than a rename of the project.
    """

    text = " ".join(str(name or "").split())
    text = _UNSAFE_CHARACTERS.sub(" ", text)
    text = " ".join(text.split()).strip(" .")
    if not text:
        text = "Untitled project"
    if text.upper() in _RESERVED_NAMES:
        text = f"Project {text}"
    if len(text) > MAX_FOLDER_NAME_CHARS:
        text = text[:MAX_FOLDER_NAME_CHARS].rstrip(" .")
    return text or "Untitled project"


def _clean_component(text: object) -> str:
    """One path component's worth of text: no separators, no control characters."""

    cleaned = _UNSAFE_CHARACTERS.sub(" ", str(text or ""))
    return " ".join(cleaned.split()).strip()


def _quoted_phrase(text: str) -> str:
    """The first quoted phrase in the idea, or ``""``.

    The owner's own words for the thing are the best name there is, and quotes are how
    he marks them: «Создай локальное приложение «Почтовый центр» — только Python»
    contains exactly one product name, and it is not the paragraph around it.
    """

    match = _QUOTED_PHRASE.search(text)
    if match is None:
        return ""
    for group in match.groups():
        if group:
            return " ".join(group.split()).strip(" .")
    return ""


def _strip_leading_intent(text: str) -> str:
    """Drop leading "what to do" words, keep the noun phrase that follows.

    Repeatedly: «Мне нужен простой сайт» is led by three such words, and stopping after
    the first one would leave «нужен простой сайт» as the product's name.
    """

    words = text.split()
    while words and words[0].lower().strip(".,:;!?—-–()") in _LEADING_FILLERS:
        words.pop(0)
    return " ".join(words)


def _strip_trailing_intent(text: str) -> str:
    """Drop the sentence's tail courtesies (``пожалуйста``, ``если можно``, ``срочно``)."""

    changed = True
    while changed:
        changed = False
        lowered = text.lower().rstrip(" .,:;!?—-–)")
        for filler in _TRAILING_INTENT:
            if not lowered.endswith(filler):
                continue
            head = text[: len(text) - len(filler)].rstrip(" .,:;!?—-–(")
            if head.strip():
                text = head
                changed = True
                break
    return text


def _cut_at_relative_clause(text: str) -> str:
    """Keep the noun phrase that stands before a comma or a relative clause."""

    match = _RELATIVE_CLAUSE.search(text)
    if match is None:
        return text
    head = text[: match.start()].rstrip(" .,:;!?—-–")
    return head or text


def _cut_after_colon(text: str) -> str:
    """Drop what follows the first colon when the head is already a phrase.

    «Сайт для бронирования отелей: список, форма, подтверждение» is named «Сайт для
    бронирования отелей»; a head of one word («Отчёт: …») is too thin to stand alone, so
    the whole idea is left to the other rules.
    """

    head, separator, _ = text.partition(":")
    if not separator:
        return text
    words = head.split()
    if _MIN_COLON_HEAD_WORDS <= len(words) <= MAX_DERIVED_NAME_WORDS:
        return head
    return text


def _nominative_part(part: str) -> str:
    """One hyphen-free word rewritten from the accusative into the nominative.

    Russian ideas are written as a request ("сделай страницу"), so the head noun arrives
    in the accusative while a product's name is the nominative ("страница"). The rule is
    deliberately suffix-only and narrow -- ``-ую``/``-юю`` for a feminine adjective,
    ``-у``/``-ю`` for a feminine noun -- because those four endings have no nominative
    reading in the language and everything else is left untouched. It is applied to
    Cyrillic words only: an English "you" ends in "-u" and must never become "yoa".
    """

    if not part or not _CYRILLIC_WORD.match(part) or part.lower() in _INDECLINABLE:
        return part
    lowered = part.lower()
    if lowered.endswith("ую") and len(lowered) > 4:
        return part[:-2] + "ая"
    if lowered.endswith("юю") and len(lowered) > 4:
        return part[:-2] + "яя"
    if lowered.endswith("у") and len(lowered) > 3:
        return part[:-1] + "а"
    if lowered.endswith("ю") and len(lowered) > 3:
        return part[:-1] + "я"
    return part


def _nominative(word: str) -> str:
    """A hyphenated word rewritten part by part («страницу-визитку» -> «страница-визитка»)."""

    if "-" not in word:
        return _nominative_part(word)
    return "-".join(_nominative_part(part) for part in word.split("-"))


def _head_end(words: list) -> int:
    """Where the head noun phrase ends: the index of the first word that opens a new part."""

    for index, word in enumerate(words):
        if word.lower().strip(".,:;!?—-–()") in _PHRASE_BREAKS:
            return index
    return len(words)


def _join_compound_prefixes(words: list) -> list:
    """Glue a prefix to the word it belongs to: «телеграм бот» -> «телеграм-бот»."""

    joined: list = []
    index = 0
    while index < len(words):
        word = words[index]
        if (
            word.lower() in _COMPOUND_PREFIXES
            and index + 1 < len(words)
            and "-" not in words[index + 1]
        ):
            joined.append(f"{word}-{words[index + 1]}")
            index += 2
            continue
        joined.append(word)
        index += 1
    return joined


def _capitalise(text: str) -> str:
    """A product name starts with a capital; the rest keeps the owner's own case."""

    return text[:1].upper() + text[1:] if text else text


def _product_phrase(text: str) -> str:
    """Turn the head of the idea into a noun phrase a product could be called."""

    words = text.split()
    end = _head_end(words)
    words = [
        _nominative(word) if index < end else word
        for index, word in enumerate(words)
    ]
    return _capitalise(" ".join(_join_compound_prefixes(words)))


def _head_phrase(text: str) -> str:
    """The short noun phrase the idea contains, in the order the rules apply.

    1. a comma or a relative clause ends the name («…, чтобы я мог читать почту»);
    2. a colon with a phrase in front of it ends it too;
    3. closing courtesies at the tail are dropped;
    4. leading "create/make/I want/simple" words are dropped, repeatedly;
    5. the first :data:`MAX_DERIVED_NAME_WORDS` words survive;
    6. the head is rewritten into a product-like noun phrase
       (:func:`_product_phrase`) -- nominative, compound prefixes hyphenated, a capital.
    """

    text = _cut_at_relative_clause(text)
    text = _cut_after_colon(text)
    text = _strip_trailing_intent(text)
    text = _strip_leading_intent(text)
    words = text.split()[:MAX_DERIVED_NAME_WORDS]
    return _product_phrase(" ".join(words))


def _cap_at_word_boundary(text: str, limit: int) -> str:
    """``text`` shortened to at most ``limit`` characters, never mid-word.

    A word that alone exceeds the limit is the one exception: a name may be shortened
    inside it rather than become empty, because an empty folder name is worse than a
    clipped one.
    """

    if len(text) <= limit:
        return text
    cut = text[:limit]
    head = cut.rsplit(" ", 1)[0].rstrip(" .,;:!?—-–(")
    if not head:
        head = cut.strip()
    return head


def _trim_trailing_filler(text: str) -> str:
    """Drop a conjunction or preposition the cap left dangling at the end."""

    words = text.split()
    while len(words) > 1 and words[-1].lower().strip(".,:;!?") in _TRAILING_FILLERS:
        words.pop()
    return " ".join(words)


def derive_project_name(
    idea: object, *, ordinal: int = 1, limit: int = MAX_DERIVED_NAME_CHARS
) -> str:
    """A short, human name for a project, derived deterministically from the idea.

    The rules, in order:

    1. **A quoted phrase wins.** If the idea contains «…», „…“, “…”, "…" or '…', the
       first one is the name -- that is the owner naming his own product -- and nothing
       else is applied to it.
    2. Otherwise the **noun phrase** the idea contains (:func:`_head_phrase`): the part
       before a comma, a relative clause or a phrase-length colon head; leading
       "create/make/I want/simple" words and trailing courtesies dropped; the first
       :data:`MAX_DERIVED_NAME_WORDS` words kept, with the head rewritten from the
       owner's request into a product's name («Хочу простую локальную страницу-визитку» ->
       «Локальная страница-визитка», «телеграм бот для …» -> «Телеграм-бот для …»).
    3. Otherwise, and when nothing usable survives (empty idea, punctuation only),
       `Проект <N>` -- ``ordinal``, so consecutive nameless projects differ.

    Whatever the source, the result is one Windows-safe path component: the characters
    Windows forbids are replaced by spaces, spaces are collapsed, trailing dots and
    spaces are stripped, Cyrillic and every other script are preserved verbatim, and the
    text is capped at ``limit`` characters **at a word boundary** so it never ends with
    a partial word.
    """

    text = " ".join(str(idea or "").split())
    quoted = _quoted_phrase(text)
    if quoted:
        text = quoted
    else:
        text = _head_phrase(text)
    text = _clean_component(text)
    text = _cap_at_word_boundary(text, max(1, int(limit)))
    text = _trim_trailing_filler(text)
    text = text.strip(" .")
    if not text or not _HAS_WORD_CHARACTER.search(text):
        number = int(ordinal) if isinstance(ordinal, int) and ordinal > 0 else 1
        return f"{FALLBACK_PROJECT_NAME} {number}"
    return text


def is_fallback_project_name(name: object) -> bool:
    """True when ``name`` is the nameless-project fallback (``Проект <N>``)."""

    text = " ".join(str(name or "").split())
    if text == FALLBACK_PROJECT_NAME:
        return True
    head, _, tail = text.partition(" ")
    return head == FALLBACK_PROJECT_NAME and tail.isdigit()


def _is_empty_directory(path: Path) -> bool:
    """True when ``path`` is a directory with nothing in it (and readable)."""

    try:
        if not path.is_dir():
            return False
        return next(iter(path.iterdir()), None) is None
    except OSError:
        return False


def visible_folder(
    root: Path, project_id: str, name: object, *, allow_existing: bool = False
) -> Path:
    """``<root>/<name> (<project_id>)``, proved to sit inside ``root``.

    The suffix carries the identifier, so two projects with the same name never share
    a folder. When that exact folder **already exists** and it is not the folder of the
    project being created (``allow_existing`` is how a retry says "it is mine"), the
    name is numbered -- ``(2)``, ``(3)``, ... -- instead of failing or being shared:
    creating a project must never die because a folder of that name is already there.
    An *empty* leftover folder is taken over rather than orphaned.
    """

    if not str(project_id or "").strip():
        raise UnsafeProjectFolder("у проекта нет идентификатора: папка не может быть создана")
    identifier = str(project_id).strip()
    stem = safe_project_folder_name(name)
    root_path = canonical(root)
    candidate = root_path / f"{stem} ({identifier})"
    if not allow_existing and candidate.exists():
        emptied = _is_empty_directory(candidate)
        if not emptied:
            for index in range(2, 1000):
                numbered = root_path / f"{stem} ({index}) ({identifier})"
                if not numbered.exists():
                    candidate = numbered
                    break
            else:  # pragma: no cover - a thousand same-named folders is not a case
                raise UnsafeProjectFolder(
                    "свободное имя для папки проекта не нашлось"
                )
    if not is_within(canonical(candidate), root_path):
        raise UnsafeProjectFolder("папка проекта оказалась вне выбранного корня")
    return candidate


def published_folder_path(root: Path, folder: Path) -> str:
    """How a visible folder is published: relative to the owner's desktop, not absolute.

    The root's own name is included (``Forge Projects/My Project (proj-…)``) so the
    owner can see *where* the folder is without the payload carrying a host path. The
    absolute form exists, and lives in the card's diagnostics block only.
    """

    root_path = Path(root)
    folder_name = Path(folder).name
    parent_name = root_path.name
    return f"{parent_name}/{folder_name}" if parent_name else folder_name


def prove_folder(
    value: object,
    *,
    allowed_roots: Iterable[object],
    must_exist: bool = False,
) -> Path:
    """Resolve ``value`` and prove it is inside one of ``allowed_roots``.

    Every refusal is :class:`UnsafeProjectFolder`, which the HTTP layer answers as a
    typed conflict, never as a server fault. ``allowed_roots`` must come from the
    deployment's own configuration (the owner's settings and the store root); it is
    never derived from a request or from the record being checked.
    """

    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise UnsafeProjectFolder("папка проекта не указана")
    text = str(value).strip()
    if "\x00" in text:
        raise UnsafeProjectFolder("путь к папке проекта содержит недопустимый символ")
    path = Path(text)
    if not path.is_absolute():
        raise UnsafeProjectFolder("путь к папке проекта должен быть абсолютным")
    if any(part == ".." for part in path.parts):
        raise UnsafeProjectFolder("путь к папке проекта не может содержать «..»")
    if path.name == "" or str(path) == path.drive:
        raise UnsafeProjectFolder("корень диска не может быть папкой проекта")

    real = canonical(path)
    roots: Tuple[Path, ...] = tuple(canonical(root) for root in allowed_roots if root)
    if not roots:
        raise UnsafeProjectFolder("не задан корень, внутри которого разрешены проекты")
    if not any(is_within(real, root) for root in roots):
        raise UnsafeProjectFolder(
            "папка проекта находится вне разрешённого корня проектов"
        )
    if any(part.lower() == ".git" for part in real.parts):
        raise UnsafeProjectFolder("папка проекта не может находиться внутри .git")
    if must_exist and not real.is_dir():
        raise UnsafeProjectFolder("папка проекта не найдена")
    return real


def count_files(folder: Optional[Path], *, limit: int = 5000) -> int:
    """How many regular files the folder holds (bounded walk, links not followed).

    Used to answer "is there anything to open?" without trusting the record: a version
    can name files the owner has since deleted.
    """

    if folder is None:
        return 0
    root = Path(folder)
    if not root.is_dir():
        return 0
    found = 0
    try:
        for entry in root.rglob("*"):
            if entry.is_symlink():
                continue
            if entry.is_file():
                found += 1
                if found >= limit:
                    break
    except OSError:
        return found
    return found
