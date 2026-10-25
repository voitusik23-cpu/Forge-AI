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
import locale
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence, Tuple

from app.projects.models import ProjectError

__all__ = [
    "FALLBACK_ROOT_NAME",
    "LOW_INTEGRITY_LABEL",
    "ProjectRootDecision",
    "ProjectRootPolicy",
    "ProjectRootUnavailable",
    "RootAttempt",
    "candidate_roots",
    "grant_low_integrity_label",
    "label_grant_command",
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

#: The mandatory label a selected folder needs before a **low-integrity** Forge can write
#: into it: ``L`` = Low, ``(OI)(CI)`` = inherited by files and folders created inside.
#:
#: MEASURED on this host: the interpreters Forge runs on carry a low mandatory label
#: (``S-1-16-4096``, "Низкий обязательный уровень", no-write-up), so the whole application
#: runs at low integrity. A low-integrity process may not create or write anything in a
#: **medium** folder such as ``C:\Users\<owner>\Desktop``, and the owner's project root
#: lived exactly there: ``PermissionError (WinError 5)`` on ``Forge Projects`` and every
#: project silently delivered to ``.forge/projects-visible`` instead of his desktop.
LOW_INTEGRITY_LABEL = "(OI)(CI)L"

#: How long the one-time label grant may take. ``icacls`` answers in milliseconds; a bound
#: keeps a settings screen from waiting on a shell that never returns.
_GRANT_TIMEOUT_SECONDS = 20.0

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
    """One probed candidate: the path, whether it can hold a project, and the OS reason.

    ``denied`` is the one distinction the caller needs beyond "it failed": a folder the OS
    refused with an *access denied* is the shape the label grant can address, while a
    folder whose parent is a file (or whose name is invalid) is not. It is published so a
    reader sees the classification the code acted on, not a guess about the text.
    """

    path: Path
    writable: bool
    reason: str = ""
    denied: bool = False

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "writable": bool(self.writable),
            "reason": self.reason,
            "denied": bool(self.denied),
        }


@dataclass(frozen=True)
class ProjectRootDecision:
    """Which folder a project is delivered into, and why it is that one.

    ``fallback`` is the fact the owner is told about: it is ``True`` exactly when the
    configured root could not be used. ``notice`` is the plain-language sentence built
    from that fact -- never a stack trace, never a bare path without a reason.

    ``label_granted`` is the one-time mandatory-label grant: ``label_grant_attempted`` says
    ``icacls`` really ran, ``label_granted`` says it worked and the folder is now usable by
    a low-integrity process, ``label_grant_reason`` carries the OS's own refusal when it
    did not. A granted label means the *preferred* folder is used and there is no fallback.
    """

    root: Path
    preferred: Path
    attempts: Tuple[RootAttempt, ...] = field(default_factory=tuple)
    fallback: bool = False
    notice: str = ""
    label_grant_attempted: bool = False
    label_granted: bool = False
    label_grant_reason: str = ""

    def to_dict(self) -> dict:
        """The additive ``project_root`` block of a confirm/settings payload."""

        return {
            "root": str(self.root),
            "preferred": str(self.preferred),
            "fallback": bool(self.fallback),
            "notice": self.notice,
            "label_grant_attempted": bool(self.label_grant_attempted),
            "label_granted": bool(self.label_granted),
            "label_grant_reason": self.label_grant_reason,
            "label_grant_command": (
                label_grant_command(self.preferred)
                if self.label_grant_attempted or self.fallback
                else ""
            ),
            "attempted": [attempt.to_dict() for attempt in self.attempts],
        }


def _reason_of(exc: BaseException) -> str:
    """The OS's own words for a refused folder, never a traceback."""

    name = type(exc).__name__
    text = str(getattr(exc, "strerror", "") or "").strip() or str(exc).strip()
    win = getattr(exc, "winerror", None)
    prefix = f"{name} (WinError {win})" if win else name
    return f"{prefix}: {text}" if text else prefix


def _is_access_denied(exc: BaseException) -> bool:
    """Whether the OS refused for lack of permission (the label-grant case).

    ``WinError 5`` is ``ERROR_ACCESS_DENIED`` and Python raises it as
    :class:`PermissionError`. Nothing else is treated as a label problem: a path whose
    parent is a file, an invalid name or a missing drive are different facts and must keep
    the generic sentence.
    """

    if isinstance(exc, PermissionError):
        return True
    return getattr(exc, "winerror", None) == 5


def _decode_console(data: bytes) -> str:
    """Decode ``icacls`` output without assuming this console's code page."""

    for encoding in ("utf-8", "cp866", locale.getpreferredencoding(False) or "utf-8"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def label_grant_command(path: object) -> str:
    """The exact ``icacls`` command that lowers one folder's mandatory label to Low.

    Published verbatim in the settings notice when Forge cannot run it itself, so the
    owner can fix the folder with one line from his own (medium-integrity) shell.
    """

    return f'icacls "{path}" /setintegritylevel {LOW_INTEGRITY_LABEL}'


def grant_low_integrity_label(path: object) -> Tuple[bool, str]:
    """Run the one-time label grant on ``path``. Returns ``(granted, reason)``; never raises.

    The command is ``icacls "<path>" /setintegritylevel (OI)(CI)L``: it lowers the folder's
    mandatory label to Low (inheritable), which is exactly what a low-integrity Forge needs
    in order to create projects inside it. It needs **no administrator rights** -- it only
    needs the right to write that folder's security descriptor, which the owner has on his
    own desktop.

    It is run with ``shell=False`` (no shell, no command string to quote) and with
    ``CREATE_NO_WINDOW`` (no console flashes on the owner's screen). A refusal comes back
    as a *reason sentence*, never as an exception: the caller keeps its honest fallback and
    turns the reason into the notice the owner reads.
    """

    command = ["icacls", str(path), "/setintegritylevel", LOW_INTEGRITY_LABEL]
    try:
        completed = subprocess.run(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=_GRANT_TIMEOUT_SECONDS,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, _reason_of(exc)
    if completed.returncode == 0:
        return True, ""
    output = _decode_console(completed.stderr or b"") or _decode_console(
        completed.stdout or b""
    )
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    detail = lines[0] if lines else f"icacls вернул код {completed.returncode}"
    return False, detail


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
        return RootAttempt(
            path=root,
            writable=False,
            reason=_reason_of(exc),
            denied=_is_access_denied(exc),
        )
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
            return RootAttempt(
                path=root,
                writable=False,
                reason=_reason_of(exc),
                denied=_is_access_denied(exc),
            )
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
        return RootAttempt(
            path=root,
            writable=False,
            reason=_reason_of(exc),
            denied=_is_access_denied(exc),
        )
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


def _notice(
    root: Path,
    preferred: Path,
    attempts: Sequence[RootAttempt],
    *,
    label_grant_attempted: bool = False,
    label_granted: bool = False,
    label_grant_reason: str = "",
) -> str:
    """The sentence the owner reads: which folder is used, and why not the other one.

    Two shapes, and the difference matters:

    * the OS refused for **lack of permission** -- the low-integrity case. The sentence
      says what is really wrong (a low-integrity process cannot write a medium folder),
      whether the mandatory label was lowered, and names the *one command* that fixes it
      plus the «Сменить папку» button. An owner must be able to act, not just read;
    * anything else (a path whose parent is a file, an invalid name): the generic sentence
      naming the OS's own reason, unchanged.
    """

    from app.config.app_settings import default_project_root

    try:
        is_desktop_default = Path(os.path.normcase(str(preferred))) == Path(
            os.path.normcase(str(default_project_root()))
        )
    except (OSError, ValueError):  # pragma: no cover - defensive
        is_desktop_default = False
    label = "Папка на Рабочем столе" if is_desktop_default else f"Папка {preferred}"
    failed = next(
        (
            attempt
            for attempt in attempts
            if Path(os.path.normcase(str(attempt.path)))
            == Path(os.path.normcase(str(preferred)))
            and not attempt.writable
        ),
        None,
    )
    reason = failed.reason if failed is not None else ""
    if failed is not None and failed.denied:
        if label_granted:
            # The label was lowered, and the folder still refuses: say both facts.
            state = "метка понижена, но запись всё равно отклоняется"
            detail = f" ({reason})" if reason else ""
        elif label_grant_attempted:
            state = "метку понизить не удалось"
            if label_grant_reason:
                state = f"{state} ({label_grant_reason})"
            # The probe's own OS reason is already published in `project_root_attempted`;
            # the grant's refusal is the one that explains this sentence.
            detail = ""
        else:  # pragma: no cover - a denied preferred root always gets its one attempt
            state = "метка не понижена"
            detail = f" ({reason})" if reason else ""
        return (
            f"{label} не принимает запись от процесса с пониженной целостностью: "
            f"{state}{detail}. Проекты сохраняются в {root}. "
            f"Вернуть Рабочий стол можно одной командой — "
            f"{label_grant_command(preferred)} — или кнопкой «Сменить папку»."
        )
    if reason:
        return (
            f"{label} недоступна для записи ({reason}) — "
            f"проекты сохраняются в {root}."
        )
    return f"{label} недоступна для записи — проекты сохраняются в {root}."


def _same_path(left: object, right: object) -> bool:
    """Whether two folder values name the same folder, case-insensitively."""

    return Path(os.path.normcase(str(left))) == Path(os.path.normcase(str(right)))


def _probe_with(probe: Callable[[object], RootAttempt], path: object) -> RootAttempt:
    """Probe one folder, turning an escaping OS error into the attempt it describes.

    :func:`probe_writable` already reports a refusal as a value, but a caller may supply
    its own probe (a test, a deployment), and a probe that raises must not turn a folder
    decision into a crash.
    """

    try:
        attempt = probe(path)
    except OSError as exc:
        return RootAttempt(
            path=Path(str(path)),
            writable=False,
            reason=_reason_of(exc),
            denied=_is_access_denied(exc),
        )
    if not isinstance(attempt, RootAttempt):  # pragma: no cover - a broken custom probe
        return RootAttempt(
            path=Path(str(path)),
            writable=False,
            reason="проба вернула не результат проверки",
        )
    return attempt


def _grant_with(
    grant: Callable[[object], Tuple[bool, str]], path: object
) -> Tuple[bool, str]:
    """Run the label grant, turning a broken granter into a refusal, never a crash."""

    try:
        granted, reason = grant(path)
    except Exception as exc:  # noqa: BLE001 - a failed grant is a reported fact
        return False, _reason_of(exc)
    return bool(granted), str(reason or "")


def resolve_writable_root(
    preferred: object,
    *,
    remembered: object = None,
    candidates: Optional[Iterable[object]] = None,
    probe: Optional[Callable[[object], RootAttempt]] = None,
    grant: Optional[Callable[[object], Tuple[bool, str]]] = None,
) -> ProjectRootDecision:
    """The first candidate that can really hold a project, or a typed refusal.

    ``candidates`` overrides the ordered list (a test, or a deployment with its own
    list); when it is omitted :func:`candidate_roots` builds the documented order.
    ``probe`` and ``grant`` override the two side effects, so a test can describe a refused
    folder and a label grant without touching the host's real ACLs.

    **The one-time label grant.** When the owner's own folder is refused with *access
    denied* -- the measured case: a low-integrity Forge against a medium-integrity desktop
    -- the folder's mandatory label is lowered once and the folder is probed again. A
    folder that accepts writes after the grant is the root, with ``fallback=False``: the
    owner gets his desktop. A grant that fails (a folder protected by policy, or a process
    that may not rewrite that folder's label) leaves the honest fallback in place and turns
    the failure into an actionable notice. The grant is attempted **at most once per
    resolve**, only for the preferred folder, and never for a remembered fallback.
    """

    preferred_path = Path(str(preferred))
    ordered: Tuple[Path, ...] = (
        tuple(Path(str(item)) for item in candidates)
        if candidates is not None
        else candidate_roots(preferred_path, remembered=remembered)
    )
    probe_one = probe if probe is not None else probe_writable
    grant_one = grant if grant is not None else grant_low_integrity_label
    attempts: list[RootAttempt] = []
    grant_attempted = False
    granted = False
    grant_reason = ""

    def decided(root: Path, *, fallback: bool, notice: str) -> ProjectRootDecision:
        return ProjectRootDecision(
            root=root,
            preferred=preferred_path,
            attempts=tuple(attempts),
            fallback=fallback,
            notice=notice,
            label_grant_attempted=grant_attempted,
            label_granted=granted,
            label_grant_reason=grant_reason,
        )

    def notice_for(root: Path) -> str:
        return _notice(
            root,
            preferred_path,
            attempts,
            label_grant_attempted=grant_attempted,
            label_granted=granted,
            label_grant_reason=grant_reason,
        )

    for candidate in ordered:
        attempt = _probe_with(probe_one, candidate)
        attempts.append(attempt)
        if attempt.writable:
            fallback = not _same_path(attempt.path, preferred_path)
            return decided(
                attempt.path,
                fallback=fallback,
                notice=notice_for(attempt.path) if fallback else "",
            )
        if grant_attempted or not attempt.denied:
            continue
        if not _same_path(attempt.path, preferred_path):
            continue
        # THE ONE ATTEMPT: the owner's folder, an access denial, once per resolve.
        grant_attempted = True
        granted, grant_reason = _grant_with(grant_one, attempt.path)
        if not granted:
            continue
        retry = _probe_with(probe_one, attempt.path)
        attempts[-1] = retry
        if retry.writable:
            # The grant worked: his folder is the root, and there is no fallback at all.
            return decided(retry.path, fallback=False, notice="")
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
