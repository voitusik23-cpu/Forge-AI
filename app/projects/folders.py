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
    "MAX_FOLDER_NAME_CHARS",
    "UnsafeProjectFolder",
    "canonical",
    "count_files",
    "is_within",
    "prove_folder",
    "published_folder_path",
    "safe_project_folder_name",
    "visible_folder",
]

#: How much of the owner's project name becomes a folder name. Long enough to read,
#: short enough to stay well inside every Windows path limit once the root and the
#: `` (proj-<12 hex>)`` suffix are added.
MAX_FOLDER_NAME_CHARS = 64

#: Windows device names, which cannot be used as a directory name whatever the suffix.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{index}" for index in range(1, 10)}
    | {f"LPT{index}" for index in range(1, 10)}
)

#: Everything Windows refuses inside a single path component, plus control characters.
_UNSAFE_CHARACTERS = re.compile(r'[\x00-\x1f<>:"/\\|?*]+')


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


def visible_folder(root: Path, project_id: str, name: object) -> Path:
    """``<root>/<name> (proj-<id>)``, proved to sit inside ``root``.

    The suffix carries the identifier, so two projects with the same name never share
    a folder and an existing folder is never reused by a different project.
    """

    if not str(project_id or "").strip():
        raise UnsafeProjectFolder("у проекта нет идентификатора: папка не может быть создана")
    folder_name = f"{safe_project_folder_name(name)} ({str(project_id).strip()})"
    root_path = canonical(root)
    candidate = root_path / folder_name
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
