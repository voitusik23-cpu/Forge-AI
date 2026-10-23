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

#: A leading verb that says *what to do*, not *what the thing is*. Only stripped from the
#: front of the idea, and only from the front: «Приложение для рассылки» keeps its noun.
#: Articles and possessives ("a", "the", "my") are deliberately **not** here: they are the
#: start of a noun phrase ("A trashed project …"), and dropping them mangles the name.
_LEADING_FILLERS = frozenset(
    """
    создай создайте сделай сделайте напиши напишите разработай разработайте
    построй постройте собери соберите реализуй реализуйте
    нужен нужна нужно нужны хочу хотел хотела хотим надо мне нам пожалуйста
    create make build write develop generate implement realise realize please
    i i'd i’d want need like
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
    """Drop leading "what to do" words, keep the noun phrase that follows."""

    words = text.split()
    while words and words[0].lower().strip(".,:;!?—-–()") in _LEADING_FILLERS:
        words.pop(0)
    return " ".join(words)


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
       first one is the name -- that is the owner naming his own product.
    2. Otherwise the **first 3-5 meaningful words** of the idea: leading "create/make I
       want" words are dropped because they say what to do, not what the thing is.
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
        text = " ".join(
            _strip_leading_intent(text).split()[:MAX_DERIVED_NAME_WORDS]
        )
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
