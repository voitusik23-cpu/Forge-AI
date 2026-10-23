"""The owner's own settings, kept in a file that is **not** ``.env``.

The product needs one setting the application itself owns: *where a delivered
project lives*. It is deliberately not a ``FORGE_*`` environment variable and not a
line in the repository's ``.env``, for two reasons:

* the owner edits it in «Настройки» and expects the change to survive a restart --
  a text field is not a reason to ask him to edit a dotfile by hand;
* ``.env`` holds deployment configuration and credentials. A folder the owner picks
  for his own projects is neither, and mixing the two is how a secret ends up in a
  place it does not belong.

The file is JSON under the repository's ``.forge`` directory
(``.forge/settings.json``), written atomically. It holds an absolute folder path and
nothing else: no key, no token, no provider configuration. ``FORGE_SETTINGS_FILE``
exists only so a test can point the store at a throwaway file.

Two values live in it:

``project_root``
    the folder **new** projects are delivered into. Default
    ``%USERPROFILE%\\Desktop\\Forge Projects``.

``project_roots_known``
    every root this deployment has used, most recent first. The boundary check for a
    project's own folder is proved against these roots, so changing the setting does
    not make the projects already delivered to the previous root unreadable. A
    project folder is therefore only ever accepted under a root the **owner**
    configured -- never under a path a request or a record supplied.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

__all__ = [
    "DEFAULT_PROJECT_ROOT_NAME",
    "PROJECT_ROOT_KEY",
    "PROJECT_ROOTS_KNOWN_KEY",
    "SETTINGS_ENV_VAR",
    "SETTINGS_SCHEMA_VERSION",
    "AppSettingsError",
    "AppSettingsStore",
    "default_project_root",
    "default_settings_path",
    "normalise_project_root",
    "repository_root",
]

#: The file a test (or a laboratory run) may redirect the settings to.
SETTINGS_ENV_VAR = "FORGE_SETTINGS_FILE"

#: Written into every settings file so a reader can tell what it is reading.
SETTINGS_SCHEMA_VERSION = "forge.settings.v1"

PROJECT_ROOT_KEY = "project_root"
PROJECT_ROOTS_KNOWN_KEY = "project_roots_known"

#: The folder created on the owner's desktop by default.
DEFAULT_PROJECT_ROOT_NAME = "Forge Projects"

#: How many previous roots are remembered. Bounded on purpose: the list exists so an
#: already-delivered project stays reachable, not as a history of the owner's choices.
MAX_KNOWN_ROOTS = 20

#: Characters Windows forbids in a file name are not the issue here -- the root is a
#: whole path the owner typed. What is refused is anything that could not name one
#: folder: an empty value, a relative value, a device path, or one that cannot be
#: created. The list below is the set of path *prefixes* with no single folder meaning.
_REFUSED_PREFIXES = ("\\\\?\\", "\\\\.\\")


class AppSettingsError(ValueError):
    """The requested setting is not a usable value. A refusal, never a crash."""

    code = "invalid_app_setting"

    def __init__(self, message: str, *, field: str = "") -> None:
        super().__init__(message)
        self.field = str(field)


def repository_root() -> Path:
    """The repository this module belongs to, from its own location."""

    return Path(__file__).resolve().parents[2]


def default_settings_path() -> Path:
    """``<repository>/.forge/settings.json``, unless ``FORGE_SETTINGS_FILE`` names one."""

    configured = os.environ.get(SETTINGS_ENV_VAR)
    if configured and configured.strip():
        return Path(configured).expanduser()
    return repository_root() / ".forge" / "settings.json"


def _user_home() -> Path:
    """``%USERPROFILE%`` (or the platform's home), never the repository."""

    profile = os.environ.get("USERPROFILE")
    if profile and profile.strip():
        return Path(profile)
    return Path.home()


def default_project_root() -> Path:
    """The folder a fresh deployment delivers projects into."""

    return _user_home() / "Desktop" / DEFAULT_PROJECT_ROOT_NAME


def normalise_project_root(value: object) -> Path:
    """Validate one owner-supplied root and return it as an absolute path.

    A relative value is refused instead of being resolved against the process's
    current directory: ``Projects`` typed into the field would otherwise mean a
    different folder depending on how the server happened to be started, and the
    owner would have no way to tell which one he got.
    """

    if not isinstance(value, str) or not value.strip():
        raise AppSettingsError(
            "Укажите папку для проектов: пустой путь не сохраняется.", field="project_root"
        )
    text = value.strip().strip('"').strip("'")
    if not text:
        raise AppSettingsError(
            "Укажите папку для проектов: пустой путь не сохраняется.", field="project_root"
        )
    if "\x00" in text:
        raise AppSettingsError(
            "Путь к папке проектов содержит недопустимый символ.", field="project_root"
        )
    for prefix in _REFUSED_PREFIXES:
        if text.startswith(prefix):
            raise AppSettingsError(
                "Путь вида \\\\?\\ или \\\\.\\ не поддерживается: укажите обычную папку.",
                field="project_root",
            )
    expanded = Path(os.path.expandvars(text)).expanduser()
    if ".." in expanded.parts:
        raise AppSettingsError(
            "Путь к папке проектов не может содержать «..»: укажите полный путь.",
            field="project_root",
        )
    if not expanded.is_absolute():
        raise AppSettingsError(
            "Укажите полный путь к папке проектов (например, "
            f"{default_project_root()}).", field="project_root"
        )
    if expanded.drive and expanded == Path(expanded.drive + "\\"):
        raise AppSettingsError(
            "Корень диска нельзя использовать как папку проектов: выберите папку внутри него.",
            field="project_root",
        )
    if expanded.exists() and not expanded.is_dir():
        raise AppSettingsError(
            f"По этому пути лежит файл, а не папка: {expanded}", field="project_root"
        )
    return expanded


def _as_path(value: object) -> Optional[Path]:
    """A stored value as a path, or ``None`` when it is not a usable absolute one."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalise_project_root(value)
    except AppSettingsError:
        return None


class AppSettingsStore:
    """Read and write the owner's settings file atomically."""

    def __init__(self, path: Optional[object] = None) -> None:
        self._path = Path(path) if path is not None else default_settings_path()

    # ------------------------------------------------------------------ path
    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------- I/O
    def read(self) -> Dict[str, object]:
        """The stored document, or an empty one when it is missing or unreadable.

        A corrupt file is reported as empty rather than partially trusted: half a
        settings document would decide a folder from a fragment.
        """

        try:
            data = json.loads(self._path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        root = data.get(PROJECT_ROOT_KEY)
        known = data.get(PROJECT_ROOTS_KNOWN_KEY)
        return {
            "schema": str(data.get("schema", "") or ""),
            PROJECT_ROOT_KEY: root if isinstance(root, str) else "",
            PROJECT_ROOTS_KNOWN_KEY: [
                str(item) for item in (known or ()) if isinstance(item, str)
            ]
            if isinstance(known, list)
            else [],
            "updated_at": str(data.get("updated_at", "") or ""),
        }

    def _write(self, root: Path, known: Iterable[object]) -> Path:
        """Atomically persist ``root`` and the known roots, newest first."""

        entries: List[str] = []
        for item in (str(root), *[str(value) for value in known]):
            if item and item not in entries:
                entries.append(item)
        payload = {
            "schema": SETTINGS_SCHEMA_VERSION,
            PROJECT_ROOT_KEY: str(root),
            PROJECT_ROOTS_KNOWN_KEY: entries[:MAX_KNOWN_ROOTS],
            "updated_at": _now_iso(),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.replace(temporary, self._path)
        return self._path

    # ---------------------------------------------------------------- values
    def project_root(self) -> Path:
        """The folder new projects are delivered into.

        Never empty and always absolute: a missing or unusable stored value falls back
        to :func:`default_project_root` and **the fallback is not written back**, so a
        broken file never silently rewrites the owner's choice.
        """

        stored = _as_path(self.read().get(PROJECT_ROOT_KEY))
        return stored if stored is not None else default_project_root()

    def default_root(self) -> Path:
        """What «Использовать рабочий стол» restores."""

        return default_project_root()

    def known_project_roots(self) -> Tuple[Path, ...]:
        """Every root this deployment has used, the current one first."""

        current = self.project_root()
        found: List[Path] = [current]
        for item in self.read().get(PROJECT_ROOTS_KNOWN_KEY) or ():
            path = _as_path(item)
            if path is not None and path not in found:
                found.append(path)
        return tuple(found)

    def set_project_root(self, value: object) -> Path:
        """Store ``value`` as the project root and remember the previous ones."""

        root = normalise_project_root(value)
        known = [item for item in self.known_project_roots() if item != root]
        self._write(root, known)
        return root

    def use_desktop(self) -> Path:
        """«Использовать рабочий стол»: restore the documented default root."""

        return self.set_project_root(str(default_project_root()))

    def ensure_root(self) -> Path:
        """Create the configured root when it does not exist yet, and return it."""

        root = self.project_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AppSettingsError(
                f"Папку проектов не удалось создать: {root} ({type(exc).__name__})",
                field="project_root",
            ) from exc
        return root

    def describe(self) -> Dict[str, object]:
        """What «Настройки» shows about the root: the value, the default, and the truth.

        This is the one place an absolute path of the host is published on purpose: the
        owner has to be able to see and paste the folder he chose. It is *his* setting,
        not a project's internal path, and no request value reaches it.
        """

        root = self.project_root()
        return {
            "project_root": str(root),
            "default_project_root": str(default_project_root()),
            "project_root_exists": bool(root.is_dir()),
            "project_roots_known": [str(item) for item in self.known_project_roots()],
            "settings_path": str(self._path),
            "schema": SETTINGS_SCHEMA_VERSION,
        }


def _now_iso() -> str:
    """The timestamp written into the settings file, in UTC ISO form."""

    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
