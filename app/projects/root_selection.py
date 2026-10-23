"""Choosing the folder a project's files are delivered into, and never dying over it.

Measured on the owner's own machine: `POST /api/discovery/sessions/{id}/confirm`
answered **409 unsafe_project_folder** -- *«папку проекта не удалось создать:
C:\\Users\\<owner>\\Desktop\\Forge Projects\\… (PermissionError)»* -- because the
preferred root lives on a protected Desktop and the process cannot create a folder
there. The owner could not create a project at all: one unwritable folder turned the
whole product into a refusal.

That is the wrong failure. A project creation must never die because the **preferred**
root is unwritable; it must land somewhere real and say so. This module is the single
place that decides *where*, and it decides it by **probing**, not by assuming:

1. the candidate list is ordered and finite (:func:`candidate_roots`):
   the configured root -> `%USERPROFILE%\\Forge Projects` ->
   `%USERPROFILE%\\Documents\\Forge Projects` -> `%LOCALAPPDATA%\\Forge Projects` ->
   `<repository>\\.forge\\projects-visible`;
2. every candidate is *probed* by actually creating a temporary directory, writing a
   temporary file into it and deleting both (:func:`probe_writable`). A folder that
   exists is not proof that it is writable, and an ACL that refuses `mkdir` is exactly
   the case this module exists for;
3. the **first** writable candidate wins; when it is not the configured one, the
   decision carries a plain-language sentence saying which folder is used and why
   (:attr:`ProjectRootDecision.notice`);
4. only when **every** candidate fails does :class:`ProjectRootUnavailable` travel to the
   HTTP layer, carrying the full list of attempted paths and the OS reason for each.

The decision is a value object, so the root it names flows unchanged into the record's
`project_path`, the open-folder/editor/terminal actions, deletion and restore: nothing
downstream re-decides where a project lives.
"""

from __future__ import annotations

import itertools
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple

from app.projects.models import ProjectError

__all__ = [
    "FALLBACK_ROOT_NAME",
    "ProjectRootDecision",
    "ProjectRootPolicy",
    "ProjectRootUnavailable",
    "RootAttempt",
    "candidate_roots",
    "probe_writable",
    "resolve_writable_root",
]

#: The folder name every fallback candidate uses, so the published path keeps the shape
#: the owner already knows (``Forge Projects/<name> (proj-…)``) wherever it really is.
FALLBACK_ROOT_NAME = "Forge Projects"

#: The last resort: inside the repository, next to the records. It is always writable by
#: the process that is running the application -- if it is not, nothing is, and the typed
#: refusal is the only honest answer.
VISIBLE_PROJECTS_DIRECTORY = ("projects-visible",)

#: The probe's own name counter. Plain ``mkdir`` with a unique name, because
#: ``tempfile.mkdtemp`` retries a denied folder ten thousand times on Windows (measured);
#: see :func:`probe_writable`.
_PROBE_NAMES = itertools.count(1)


class ProjectRootUnavailable(ProjectError):
    """No candidate folder can hold a project. A refusal with the whole list, never a crash.

    ``attempts`` is the ordered list of what was tried and why each one failed, so the
    HTTP layer can publish it verbatim instead of a stack trace.
    """

    code = "project_root_unavailable"

    def __init__(
        self,
        message: str,
        *,
        attempts: Sequence["RootAttempt"] = (),
        preferred: Optional[Path] = None,
    ) -> None:
        super().__init__(message)
        self.attempts: Tuple[RootAttempt, ...] = tuple(attempts)
        self.preferred: Optional[Path] = Path(preferred) if preferred else None


@dataclass(frozen=True)
class RootAttempt:
    """One probed candidate: the path, whether it can hold a project, and the OS reason."""

    path: Path
    writable: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "writable": bool(self.writable),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProjectRootDecision:
    """Which folder a project is delivered into, and why it is that one.

    ``fallback`` is the fact the owner is told about: it is ``True`` exactly when the
    configured root could not be used. ``notice`` is the plain-language sentence built
    from that fact -- never a stack trace, never a bare path without a reason.
    """

    root: Path
    preferred: Path
    attempts: Tuple[RootAttempt, ...] = field(default_factory=tuple)
    fallback: bool = False
    notice: str = ""

    def to_dict(self) -> dict:
        """The additive ``project_root`` block of a confirm/settings payload."""

        return {
            "root": str(self.root),
            "preferred": str(self.preferred),
            "fallback": bool(self.fallback),
            "notice": self.notice,
            "attempted": [attempt.to_dict() for attempt in self.attempts],
        }


def _reason_of(exc: BaseException) -> str:
    """The OS's own words for a refused folder, never a traceback."""

    name = type(exc).__name__
    text = str(getattr(exc, "strerror", "") or "").strip() or str(exc).strip()
    win = getattr(exc, "winerror", None)
    prefix = f"{name} (WinError {win})" if win else name
    return f"{prefix}: {text}" if text else prefix


def probe_writable(path: object) -> RootAttempt:
    """Can a project folder really be created under ``path``? Measured, not assumed.

    The probe creates the folder when it does not exist, creates a **temporary
    directory** inside it, writes a **temporary file** into that directory and deletes
    both. That is the smallest sequence that proves the four permissions a project
    delivery needs (create a folder, create a file, write bytes, remove what it made),
    and it leaves nothing behind when it succeeds.

    **The temporary name is built here, never with ``tempfile.mkdtemp``.** MEASURED on
    this very host: on Windows ``mkdtemp`` treats ``PermissionError`` as "the name is
    taken", decides whether to retry from ``os.access(dir, W_OK)`` -- which consults the
    read-only attribute, not the ACL -- and therefore retries a denied folder
    **ten thousand times** before giving up. On the owner's protected desktop that turns
    "can I write here?" into a request that never answers. One ``mkdir`` at a time is
    both honest and bounded: a denied folder refuses in the same millisecond.

    A refusal is not swallowed: it comes back as the attempt's ``reason``.
    """

    try:
        root = Path(str(path))
    except (TypeError, ValueError) as exc:
        return RootAttempt(path=Path("."), writable=False, reason=_reason_of(exc))
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return RootAttempt(path=root, writable=False, reason=_reason_of(exc))
    if not root.is_dir():
        return RootAttempt(path=root, writable=False, reason="NotADirectoryError: это не папка")
    probe: Optional[Path] = None
    for _ in range(8):
        candidate = root / f".forge-probe-{os.getpid()}-{next(_PROBE_NAMES)}"
        try:
            candidate.mkdir()
        except FileExistsError:  # pragma: no cover - a fresh random name is never taken
            continue
        except OSError as exc:
            return RootAttempt(path=root, writable=False, reason=_reason_of(exc))
        probe = candidate
        break
    if probe is None:  # pragma: no cover - eight collisions in a row is not a case
        return RootAttempt(
            path=root, writable=False, reason="временную папку для пробы создать не удалось"
        )
    try:
        marker = probe / "probe.txt"
        marker.write_bytes(b"forge")
        if marker.read_bytes() != b"forge":  # pragma: no cover - defensive
            return RootAttempt(
                path=root, writable=False, reason="запись вернула другие байты"
            )
        marker.unlink()
    except OSError as exc:
        return RootAttempt(path=root, writable=False, reason=_reason_of(exc))
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    return RootAttempt(path=root, writable=True)


def _home(home: Optional[object]) -> Path:
    if home is not None:
        return Path(str(home))
    profile = os.environ.get("USERPROFILE")
    if profile and profile.strip():
        return Path(profile)
    return Path.home()


def _local_appdata() -> Optional[Path]:
    value = os.environ.get("LOCALAPPDATA")
    if value and value.strip():
        return Path(value)
    profile = os.environ.get("USERPROFILE")
    return Path(profile) / "AppData" / "Local" if profile and profile.strip() else None


#: Distinguishes "the caller did not pass local_appdata" from "pass none at all".
_UNSET: object = object()


def _repository_visible_root() -> Path:
    """``<repository>/.forge/projects-visible`` -- the fallback that is always ours."""

    from app.config.app_settings import repository_root

    return repository_root().joinpath(".forge", *VISIBLE_PROJECTS_DIRECTORY)


def candidate_roots(
    preferred: object,
    *,
    remembered: object = None,
    home: object = None,
    local_appdata: object = _UNSET,
    repository: object = None,
) -> Tuple[Path, ...]:
    """The ordered list of folders a project may be delivered into, first choice first.

    ``preferred`` is the owner's setting. ``remembered`` is the folder this deployment
    used last time because the preferred one was unusable: it is kept **right after**
    the preference so a working fallback stays stable across calls instead of jumping
    to a different folder whenever the first candidate's luck changes.
    """

    roots: list[Path] = []

    def add(value: object) -> None:
        if value is None:
            return
        try:
            path = Path(str(value))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        if path not in roots:
            roots.append(path)

    add(preferred)
    add(remembered)
    user = _home(home)
    add(user / FALLBACK_ROOT_NAME)
    add(user / "Documents" / FALLBACK_ROOT_NAME)
    appdata = (
        _local_appdata()
        if local_appdata is _UNSET
        else (Path(str(local_appdata)) if local_appdata else None)
    )
    add(appdata / FALLBACK_ROOT_NAME if appdata is not None else None)
    add(
        Path(str(repository)) / ".forge" / VISIBLE_PROJECTS_DIRECTORY[0]
        if repository is not None
        else _repository_visible_root()
    )
    return tuple(roots)


def _notice(root: Path, preferred: Path, attempts: Sequence[RootAttempt]) -> str:
    """The sentence the owner reads: which folder is used, and why not the other one."""

    from app.config.app_settings import default_project_root

    try:
        is_desktop_default = Path(os.path.normcase(str(preferred))) == Path(
            os.path.normcase(str(default_project_root()))
        )
    except (OSError, ValueError):  # pragma: no cover - defensive
        is_desktop_default = False
    label = "Папка на Рабочем столе" if is_desktop_default else f"Папка {preferred}"
    reason = next(
        (
            attempt.reason
            for attempt in attempts
            if Path(os.path.normcase(str(attempt.path)))
            == Path(os.path.normcase(str(preferred)))
            and attempt.reason
        ),
        "",
    )
    if reason:
        return (
            f"{label} недоступна для записи ({reason}) — "
            f"проекты сохраняются в {root}."
        )
    return f"{label} недоступна для записи — проекты сохраняются в {root}."


def resolve_writable_root(
    preferred: object,
    *,
    remembered: object = None,
    candidates: Optional[Iterable[object]] = None,
) -> ProjectRootDecision:
    """The first candidate that can really hold a project, or a typed refusal.

    ``candidates`` overrides the ordered list (a test, or a deployment with its own
    list); when it is omitted :func:`candidate_roots` builds the documented order.
    """

    preferred_path = Path(str(preferred))
    ordered: Tuple[Path, ...] = (
        tuple(Path(str(item)) for item in candidates)
        if candidates is not None
        else candidate_roots(preferred_path, remembered=remembered)
    )
    attempts: list[RootAttempt] = []
    for candidate in ordered:
        attempt = probe_writable(candidate)
        attempts.append(attempt)
        if attempt.writable:
            fallback = Path(os.path.normcase(str(attempt.path))) != Path(
                os.path.normcase(str(preferred_path))
            )
            return ProjectRootDecision(
                root=attempt.path,
                preferred=preferred_path,
                attempts=tuple(attempts),
                fallback=fallback,
                notice=(
                    _notice(attempt.path, preferred_path, attempts) if fallback else ""
                ),
            )
    listed = "; ".join(
        f"{attempt.path} — {attempt.reason or 'недоступна'}" for attempt in attempts
    )
    raise ProjectRootUnavailable(
        "не удалось найти папку для проектов: ни один из вариантов не доступен для "
        f"записи ({listed})",
        attempts=tuple(attempts),
        preferred=preferred_path,
    )


class ProjectRootPolicy:
    """Resolve, remember and report the project root for one deployment.

    ``preferred`` is a path or a zero-argument callable (the owner's setting read from
    his own file, so editing it takes effect without a restart). ``remember`` is called
    once per newly chosen fallback -- it is how the chosen root reaches
    ``.forge/settings.json``. ``known`` lists every root this deployment has used, so a
    project delivered to a previous root stays reachable after the setting changes.

    Nothing is cached: the probe is a few file operations, and a cached "unwritable"
    would be a stale answer to the one question the owner is trying to fix.
    """

    def __init__(
        self,
        preferred: object,
        *,
        remember: Optional[Callable[[Path], object]] = None,
        known: Optional[Callable[[], Iterable[object]]] = None,
        candidates: Optional[Callable[[Path], Iterable[object]]] = None,
    ) -> None:
        self._preferred_source = preferred
        self._remember = remember
        self._known = known
        self._candidates = candidates
        self._remembered_in_this_process: Optional[Path] = None

    # ------------------------------------------------------------------ values
    def preferred_root(self) -> Optional[Path]:
        """The configured root, or ``None`` when the configured value is unusable.

        An unusable **value** (empty, relative, containing ``..``) is "not configured"
        rather than an exception -- a broken setting must not make the project list
        unreadable. An unusable **folder** is a different fact and is handled by
        :meth:`resolve`, which falls back and then refuses.
        """

        source = self._preferred_source
        if callable(source):
            try:
                source = source()
            except Exception:  # noqa: BLE001 - an unreadable setting is "not configured"
                return None
        if source is None:
            return None
        try:
            path = Path(str(source)).expanduser()
        except (TypeError, ValueError):
            return None
        if not path.is_absolute() or any(part == ".." for part in path.parts):
            return None
        return path

    def remembered_root(self) -> Optional[Path]:
        """The fallback this process already chose, kept for the next resolve."""

        return self._remembered_in_this_process

    def known_roots(self) -> Tuple[Path, ...]:
        """Every root this deployment may have delivered a project into."""

        found: list[Path] = []
        for item in (self._known() if self._known is not None else ()):
            try:
                path = Path(str(item)).expanduser()
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
            if path.is_absolute() and path not in found:
                found.append(path)
        return tuple(found)

    # ------------------------------------------------------------------ resolve
    def resolve(self) -> Optional[ProjectRootDecision]:
        """The writable root for the next project, or ``None`` when none is configured.

        Raises :class:`ProjectRootUnavailable` only when a root **is** configured and not
        one candidate can hold a project.
        """

        preferred = self.preferred_root()
        if preferred is None:
            return None
        decision = resolve_writable_root(
            preferred,
            remembered=self._remembered_in_this_process,
            candidates=(
                self._candidates(preferred) if self._candidates is not None else None
            ),
        )
        if decision.fallback and decision.root != self._remembered_in_this_process:
            self._remembered_in_this_process = decision.root
            if self._remember is not None:
                try:
                    self._remember(decision.root)
                except Exception:  # noqa: BLE001 - a failed remember must not lose the root
                    pass
        return decision
