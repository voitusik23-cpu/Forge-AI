"""Build identity of the running backend: version, commit, build timestamps.

What this module reports, and why each value is what it is
---------------------------------------------------------
Six facts answer "which build is this?":

* ``version`` -- the application's own version string, spelled once here so that
  ``/health`` and ``/api/version`` cannot drift apart;
* ``commit`` / ``commit_short`` -- read from ``git rev-parse HEAD`` and
  ``git rev-parse --short HEAD`` **once, at import**, i.e. when the backend
  starts. The SHA is the one thing that identifies the source this process is
  actually running;
* ``built_at`` -- when this process resolved that identity, i.e. when the running
  backend started. The backend is a Python source checkout, not a compiled
  artifact, so the honest "build moment" of the running backend is the moment it
  started; a commit date would describe the *source*, not this build;
* ``backend`` -- ``"connected"`` when this process holds a runtime, otherwise
  ``"disconnected"``. It is derived by the caller, never assumed here;
* ``ui_build`` -- the modification time of ``ui/dist/index.html``: the interface
  bundle the local server serves. ``"missing"`` when nothing has been built.

Nothing here can leak a secret. The payload carries an ISO-8601 timestamp, a
hexadecimal object name, a version string, and two fixed status words; no
environment value and no filesystem path is part of it.

Everything fails soft
---------------------
A machine without ``git``, a checkout without ``.git``, a git that hangs, a
refusal, a malformed answer, and an absent or unreadable ``ui/dist`` all degrade
to ``"unknown"`` / ``"missing"``. None of them raises: a status read must not be
able to take the server down, and "unknown" is a real answer while a fabricated
value is not.
"""

from __future__ import annotations

import datetime
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

__all__ = [
    "FORGE_VERSION",
    "UNKNOWN",
    "UI_BUILD_MISSING",
    "REPO_ROOT",
    "UI_INDEX",
    "BuildIdentity",
    "iso_utc",
    "resolve_build_identity",
    "ui_build_stamp",
    "build_version_payload",
    "BUILD_IDENTITY",
]

#: One spelling of the application version. ``HealthResponse`` defaults to it.
FORGE_VERSION = "0.1.0"

#: The value used whenever a fact could not be established.
UNKNOWN = "unknown"

#: The value used when no interface bundle exists in this checkout.
UI_BUILD_MISSING = "missing"

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_INDEX = REPO_ROOT / "ui" / "dist" / "index.html"

#: A git read must never hold a request open. Five seconds is generous for one
#: ``rev-parse`` on any checkout and short enough that a stuck git cannot look
#: like a hung server.
GIT_TIMEOUT_SECONDS = 5.0

#: A git object name is lowercase hexadecimal. Anything else (an error message on
#: stdout, a localized notice, an empty string) is not a commit and is not
#: reported as one.
_COMMIT_HEX = re.compile(r"[0-9a-f]{4,64}\Z")

_GIT_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class BuildIdentity:
    """The identity of one running backend, as observed at its start."""

    version: str
    commit: str
    commit_short: str
    built_at: str


def iso_utc(moment: datetime.datetime) -> str:
    """A UTC moment as ``YYYY-MM-DDTHH:MM:SSZ``; a naive moment is read as UTC."""

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_git(
    arguments: Sequence[str],
    *,
    root: Path,
    run: Optional[Callable[..., Any]] = None,
) -> str:
    """Run one read-only git command in ``root``; return its first output line, or ``""``.

    ``run`` is injectable only so a test can answer without a git binary and without
    this machine's own checkout; production always uses :func:`subprocess.run`. Every
    failure mode -- no binary, non-zero exit, timeout, unreadable output -- answers
    ``""``, which the caller reports as ``"unknown"``.
    """

    runner = subprocess.run if run is None else run
    try:
        completed = runner(
            ["git", *arguments],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            creationflags=_GIT_CREATION_FLAGS,
        )
    except Exception:  # noqa: BLE001 - a missing or uncooperative git is "unknown", not a crash
        return ""
    try:
        if int(getattr(completed, "returncode", 1) or 0) != 0:
            return ""
        output = getattr(completed, "stdout", "") or ""
    except Exception:  # noqa: BLE001 - a substitute process object answers oddly; treat as unknown
        return ""
    for line in str(output).splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _commit_pair(
    *, root: Path = REPO_ROOT, run: Optional[Callable[..., Any]] = None
) -> Tuple[str, str]:
    """``(commit, commit_short)`` or ``("unknown", "unknown")``.

    The two are read separately so that a ``rev-parse --short`` that fails cannot
    hide an otherwise valid full SHA; when the short form is unusable it is derived
    from the full name instead of being reported as unknown.
    """

    full = _run_git(("rev-parse", "HEAD"), root=root, run=run)
    if not _COMMIT_HEX.match(full):
        return UNKNOWN, UNKNOWN
    short = _run_git(("rev-parse", "--short", "HEAD"), root=root, run=run)
    if not _COMMIT_HEX.match(short):
        short = full[:7]
    return full, short


def resolve_build_identity(
    *,
    root: Path = REPO_ROOT,
    run: Optional[Callable[..., Any]] = None,
    now: Optional[datetime.datetime] = None,
) -> BuildIdentity:
    """Resolve the identity of the backend that is starting now."""

    commit, commit_short = _commit_pair(root=root, run=run)
    moment = now if now is not None else datetime.datetime.now(datetime.timezone.utc)
    return BuildIdentity(
        version=FORGE_VERSION,
        commit=commit,
        commit_short=commit_short,
        built_at=iso_utc(moment),
    )


def ui_build_stamp(index: Optional[Path] = None) -> str:
    """The interface bundle's build time, or ``"missing"`` when there is no bundle."""

    target = Path(index) if index is not None else UI_INDEX
    try:
        modified = target.stat().st_mtime
    except OSError:
        return UI_BUILD_MISSING
    try:
        return iso_utc(datetime.datetime.fromtimestamp(modified, tz=datetime.timezone.utc))
    except (OverflowError, OSError, ValueError):  # pragma: no cover - a hostile mtime
        return UNKNOWN


#: Resolved once, at import -- when the backend starts.
BUILD_IDENTITY = resolve_build_identity()


def build_version_payload(
    *,
    backend: str = "connected",
    identity: Optional[BuildIdentity] = None,
    index: Optional[Path] = None,
) -> Dict[str, Any]:
    """The read-only version payload: six fields, no secret and no path."""

    resolved = BUILD_IDENTITY if identity is None else identity
    return {
        "version": resolved.version,
        "commit": resolved.commit,
        "commit_short": resolved.commit_short,
        "built_at": resolved.built_at,
        "backend": str(backend),
        "ui_build": ui_build_stamp(index),
    }
