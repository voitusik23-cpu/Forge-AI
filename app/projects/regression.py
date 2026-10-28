"""A change that breaks a working project is rolled back from its own snapshot.

Why this module exists
----------------------

The owner's own project (`.forge/projects-visible/Почтовый центр …`) was a **working
application**: a Python server the desktop shortcut started, and a page that answered.
A change run replaced `app.py` with an answer that exits `1`, nothing served the port,
and the shortcut showed nothing. The change run reported `COMPLETED`, the project
record said `current_version: 8 · COMPLETED`, and nobody noticed -- because the write
gate checked only that the file **compiles**, never that the application still *starts*.

This module is the missing check, and it is deliberately small:

1. **Snapshot before writing.** The writer already copies every file it is about to
   modify into ``<workspace>/.forge/backups/<run_id>/`` before it writes. That copy
   *is* the pre-change snapshot; nothing here makes a second one.
2. **Proof before the change.** :func:`capture_launch_state` records whether the
   project was launchable at all -- a process entry, or a static page that really
   answered. A project that never launched is exempt: a first creation cannot break a
   launch it never had.
3. **Proof after the change.** :func:`verify_change` starts the project through the
   same supervisor the owner's button uses (:class:`app.projects.runner.LocalRunner`)
   or serves the page through the same read-only preview
   (:class:`app.projects.preview.PreviewRegistry`), and requires a real ``GET /`` to
   answer within :data:`CHANGE_LAUNCH_BUDGET_SECONDS` -- the launch readiness budget
   that already exists, never a new one.
4. **Rollback, proved.** When the answer does not come, every file the change wrote is
   restored from the snapshot (a file the change *created* is removed), and the
   restored bytes are re-read and digested. The rollback is a fact with a digest per
   file, not a claim.

Honesty rules this module keeps
-------------------------------

* It never rolls a change back on evidence that the project's **own code** did not
  cause: when the supervisor could not even start the entry (a missing interpreter,
  a stale approval, the execution chain's own refusal), the outcome is
  ``not_verified`` with the reason published -- not a rollback of a change that may be
  perfectly good. A run that starts and then fails, or that starts and never answers,
  **is** evidence about the code, and that is the case this guard acts on.
* A rollback that cannot restore every file is reported as ``rollback_failed`` with the
  paths that were not restored. It is never reported as success, and never as a
  rollback that happened.
* The message is the owner's sentence plus the reason from the run's own log; no file
  body and no credential is ever copied into it.
* Everything here is bounded: a fixed number of files, a fixed budget, a bounded log
  tail, and no network outside loopback.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple

from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.projects.entrypoint import (
    SERVE_KIND_STATIC,
    EntryPoint,
    resolve_entry,
)
from app.projects.launcher import READINESS_TIMEOUT_SECONDS
from app.runtime.run_store import is_safe_run_id

__all__ = [
    "CHANGE_LAUNCH_BUDGET_SECONDS",
    "DEFAULT_LOG_TAIL_LINES",
    "LaunchSnapshot",
    "OUTCOME_KEPT",
    "OUTCOME_NOT_REQUIRED",
    "OUTCOME_NOT_VERIFIED",
    "OUTCOME_ROLLED_BACK",
    "OUTCOME_ROLLBACK_FAILED",
    "ProbeAnswer",
    "ROLLBACK_PREFIX",
    "RollbackRecord",
    "SNAPSHOT_DIRECTORY",
    "capture_launch_state",
    "default_launch_probe",
    "restore_snapshot",
    "verify_change",
]

#: Where the writer keeps the pre-change copy of every file it modifies. The same
#: value as :data:`app.agent_runtime.code_writer.BACKUP_DIRECTORY`, named here so a
#: reader of this module does not have to import the writer to learn it.
SNAPSHOT_DIRECTORY = ".forge/backups"

#: The budget a change gets to prove the project still starts. It is the launch
#: readiness timeout the product already uses
#: (:data:`app.projects.launcher.READINESS_TIMEOUT_SECONDS`, itself a little longer
#: than the supervisor's 8 s startup window) -- not a second, larger allowance
#: invented for this check.
CHANGE_LAUNCH_BUDGET_SECONDS = float(READINESS_TIMEOUT_SECONDS)

#: How many lines of the run's own log one refusal quotes.
DEFAULT_LOG_TAIL_LINES = 12

#: How many files one rollback will restore. The writer's own cap is 20 per answer;
#: this is that cap, so a rollback can never be asked for more than one run wrote.
MAX_ROLLBACK_FILES = 20

#: The owner's sentence. The reason from the log follows it in parentheses.
ROLLBACK_PREFIX = "Изменения откатаны: после правки приложение перестало запускаться"

#: The change was verified: the project answered after it, so it was kept.
OUTCOME_KEPT = "kept"
#: The project was not launchable before the change, so no launch was required.
OUTCOME_NOT_REQUIRED = "not_required"
#: The project could not be started by this process at all; the change is untouched.
OUTCOME_NOT_VERIFIED = "not_verified"
#: The project answered before the change and did not after it: files restored.
OUTCOME_ROLLED_BACK = "rolled_back"
#: The restore did not complete: reported as a failure, never as a success.
OUTCOME_ROLLBACK_FAILED = "rollback_failed"

#: The words a probe can answer with. ``ran`` is the one fact that separates "the
#: project's own code failed" from "this process could not run it at all".
_RAN = "ran"
_NOT_RAN = "not_ran"


def _workspace_root(workspace: object) -> Optional[Path]:
    """A ``Path`` for a workspace value, or ``None`` when it is not usable."""

    if workspace is None:
        return None
    try:
        if isinstance(workspace, (str, os.PathLike)):
            root = Path(workspace)
        else:
            root = Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return None
    return root


def _digest_of(path: Path) -> str:
    """The SHA-256 of one file, or ``""`` when it cannot be read."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# what the project was before the change
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LaunchSnapshot:
    """Whether the project was launchable **before** the change, and how.

    ``launchable`` is only ever set from a fact: a process entry whose own file is on
    disk (that is what "its entry was a process" means, and the supervisor starts
    exactly that file), or a static page a read-only preview really answered on.
    Everything else -- no entry point, a missing file, an entry this build cannot run
    -- is ``launchable = False`` with the reason word kept.
    """

    launchable: bool = False
    entry_kind: str = ""
    entry_path: str = ""
    serve_kind: str = ""
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "launchable": bool(self.launchable),
            "entry_kind": self.entry_kind,
            "entry_path": self.entry_path,
            "serve_kind": self.serve_kind,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ProbeAnswer:
    """What one launch attempt of the project proved.

    ``ran`` is the split that matters: ``False`` means this process could not start
    the project's own code (a refused spawn, no interpreter), so the attempt says
    nothing about the change. ``False`` with ``ran = True`` means the project's own
    code was started and did not answer -- that is evidence.
    """

    status: int = 0
    ran: bool = False
    reason: str = ""
    log_tail: Tuple[str, ...] = ()

    @property
    def answered(self) -> bool:
        return 200 <= int(self.status or 0) < 300


#: One launch attempt. It receives the workspace and the entry point, and must not
#: write anything except what a launch already writes (a log, a remembered port).
LaunchProbe = Callable[[Path, "EntryPoint"], ProbeAnswer]


def capture_launch_state(
    workspace: object,
    *,
    entry: Optional[EntryPoint] = None,
    probe: Optional[LaunchProbe] = None,
    budget: float = CHANGE_LAUNCH_BUDGET_SECONDS,
) -> LaunchSnapshot:
    """Was this project launchable **before** the change?

    The entry point is resolved from the workspace on disk (never from a manifest this
    module would have to trust), and the answer is a fact about that tree:

    * a **process** entry whose own file exists -> launchable: the supervisor starts
      exactly that file, so its presence is what "it was a process" means;
    * a **static** page -> launchable only when a read-only preview really answered
      ``GET /`` on it just now, because a page that does not serve is not launchable;
    * anything else -> not launchable, with the reason kept.

    A project with no entry point at all is the first-creation case: there is no
    launch to break, so the change path keeps its existing compile gate only.
    """

    root = _workspace_root(workspace)
    if root is None or not root.is_dir():
        return LaunchSnapshot(reason="project_workspace_missing")

    resolved = entry
    if resolved is None:
        try:
            resolved = resolve_entry((), workspace=root).entry
        except Exception:  # noqa: BLE001 - an unreadable tree is "no entry point"
            resolved = None
    if resolved is None:
        return LaunchSnapshot(reason="no_entry_point")

    kind = str(getattr(resolved, "kind", "") or "")
    path = str(getattr(resolved, "path", "") or "")
    serve_kind = str(getattr(resolved, "serve_kind", "") or "")
    reason = str(getattr(resolved, "reason", "") or "")

    if serve_kind == SERVE_KIND_STATIC:
        answer = (probe or default_launch_probe)(root, resolved)
        return LaunchSnapshot(
            launchable=answer.answered,
            entry_kind=kind,
            entry_path=path,
            serve_kind=serve_kind,
            reason="" if answer.answered else (answer.reason or "the page did not answer"),
        )

    try:
        target = root.joinpath(*path.split("/")) if path else None
    except ValueError:
        target = None
    if target is None or not target.is_file():
        return LaunchSnapshot(
            entry_kind=kind,
            entry_path=path,
            serve_kind=serve_kind,
            reason="entry_file_missing",
        )
    if reason or not tuple(getattr(resolved, "command", ()) or ()):
        return LaunchSnapshot(
            entry_kind=kind,
            entry_path=path,
            serve_kind=serve_kind,
            reason=reason or "entry_command_unavailable",
        )
    return LaunchSnapshot(
        launchable=True,
        entry_kind=kind,
        entry_path=path,
        serve_kind=serve_kind,
    )


# --------------------------------------------------------------------------- #
# the real probe: the product's own supervisor and preview
# --------------------------------------------------------------------------- #


def _static_probe(root: Path, entry: EntryPoint) -> ProbeAnswer:
    """Serve the page read-only, prove ``GET /`` answers, and stop the server."""

    try:
        from app.projects.preview import PreviewRegistry
        from app.projects.ports import probe_http, port_from_url
    except Exception:  # noqa: BLE001 - an importable preview is a hard requirement
        return ProbeAnswer(ran=False, reason="preview_unavailable")

    registry = PreviewRegistry()
    key = "forge-change-check"
    try:
        started = dict(registry.start(key, root, entry) or {})
    except Exception:  # noqa: BLE001 - a preview failure is not evidence about code
        return ProbeAnswer(ran=False, reason="preview_failed")
    try:
        url = str(started.get("url", "") or "")
        if not started.get("running") or url == "":
            return ProbeAnswer(
                ran=False, reason=str(started.get("reason", "") or "preview_failed")
            )
        port = port_from_url(url)
        status = int(probe_http(port) or 0)
        return ProbeAnswer(status=status, ran=True, reason="" if status else "no_answer")
    finally:
        try:
            registry.stop(key)
        except Exception:  # noqa: BLE001 - a preview left behind must not break a check
            pass


def _process_probe(
    root: Path, entry: EntryPoint, *, budget: float, project_id: str
) -> ProbeAnswer:
    """Start the project through the supervisor, prove ``GET /``, then stop it.

    The supervisor is the one the owner's button uses: it resolves the argv, enforces
    the approval and the containment, and discovers the port a real ``GET /``
    answered on. The launch is approved here with the digest the runner itself
    computed for this exact command, so the approval boundary is unchanged -- this
    module does not invent a second way to start a process.
    """

    try:
        from app.projects.preview import PreviewRegistry
        from app.projects.ports import probe_http
        from app.projects.runner import (
            REFUSED,
            RUNNING,
            RUN_BLOCKED,
            TERMINAL_STATES,
            LocalRunner,
            RunTarget,
        )
    except Exception:  # noqa: BLE001
        return ProbeAnswer(ran=False, reason="runner_unavailable")

    previews = PreviewRegistry()
    runner = LocalRunner(previews=previews)
    target = RunTarget(
        project_id=project_id, workspace=root, entry=entry, project_path=""
    )
    started = False
    try:
        payload = runner.start_confirmed(
            target,
            confirm=True,
            digest=runner.digest_for(target),
            offline=False,
        )
        payload = dict(payload or {})
        state = str(payload.get("state", "") or "")
        if state in (REFUSED, RUN_BLOCKED):
            # The project's own code was never started: this is not evidence about
            # the change, so the caller must not roll anything back.
            return ProbeAnswer(
                ran=False,
                reason=str(payload.get("reason", "") or state.lower()),
                log_tail=tuple(_log_lines(payload)),
            )
        started = True
        deadline = time.monotonic() + max(0.5, float(budget))
        while time.monotonic() < deadline:
            state = str(runner.state(project_id) or "")
            if state == RUNNING:
                port = int(runner.port(project_id) or 0)
                if port:
                    status = int(probe_http(port) or 0)
                    return ProbeAnswer(
                        status=status,
                        ran=True,
                        reason="" if 200 <= status < 300 else "no_answer",
                        log_tail=tuple(_log_lines(runner.log(target))),
                    )
            if state in TERMINAL_STATES:
                break
            time.sleep(0.2)
        tail = tuple(_log_lines(runner.log(target)))
        reason = "the process ended without answering"
        exit_code = runner.status(target).get("exit_code")
        if isinstance(exit_code, int):
            reason = f"процесс завершился с кодом {exit_code}"
        return ProbeAnswer(status=0, ran=started, reason=reason, log_tail=tail)
    except Exception as error:  # noqa: BLE001 - a broken probe is not a broken project
        return ProbeAnswer(ran=started, reason=f"probe_error:{type(error).__name__}")
    finally:
        try:
            runner.stop(project_id)
        except Exception:  # noqa: BLE001 - stopping is best effort
            pass
        try:
            runner.stop_all()
        except Exception:  # noqa: BLE001
            pass


def _log_lines(payload: object, limit: int = DEFAULT_LOG_TAIL_LINES) -> Tuple[str, ...]:
    """The last bounded, non-empty lines of a run payload's log."""

    lines: Sequence[Any] = ()
    if isinstance(payload, Mapping):
        log = payload.get("log")
        if isinstance(log, Mapping):
            raw = log.get("lines")
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                lines = list(raw)
    kept = [str(item) for item in lines if str(item).strip()]
    return tuple(kept[-max(1, int(limit)) :])


def default_launch_probe(
    root: Path,
    entry: EntryPoint,
    *,
    budget: float = CHANGE_LAUNCH_BUDGET_SECONDS,
    project_id: str = "proj-forge-change-check",
) -> ProbeAnswer:
    """The real check: the product's own preview for a page, its runner for a process."""

    if str(getattr(entry, "serve_kind", "") or "") == SERVE_KIND_STATIC:
        return _static_probe(root, entry)
    return _process_probe(root, entry, budget=budget, project_id=project_id)


# --------------------------------------------------------------------------- #
# the rollback
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RestoredFile:
    """One file the rollback put back, with the digest that proves it."""

    path: str
    sha256: str
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": int(self.size)}


@dataclass
class RollbackRecord:
    """What the change-verification did, in facts.

    ``outcome`` is one of the module's five ``OUTCOME_*`` words. ``rolled_back`` is
    true **only** when every written file was restored and every restored digest
    matches the snapshot; a partial restore is ``rollback_failed``.
    """

    outcome: str = OUTCOME_NOT_REQUIRED
    checked: bool = False
    launchable_before: bool = False
    launchable_after: bool = False
    http_status: int = 0
    reason: str = ""
    message: str = ""
    snapshot: str = ""
    restored: Tuple[RestoredFile, ...] = ()
    removed: Tuple[str, ...] = ()
    failed: Tuple[str, ...] = ()
    log_tail: Tuple[str, ...] = ()
    before: LaunchSnapshot = field(default_factory=LaunchSnapshot)

    @property
    def rolled_back(self) -> bool:
        """True only for a rollback that completed and whose digests were checked."""

        return self.outcome == OUTCOME_ROLLED_BACK and not self.failed

    @property
    def status(self) -> str:
        """The word the run record stores: ``failed`` for a broken restore."""

        if self.outcome == OUTCOME_ROLLBACK_FAILED:
            return "failed"
        return "rolled_back" if self.rolled_back else self.outcome

    def to_dict(self) -> Dict[str, Any]:
        """The public shape. Digests, never contents; no absolute path."""

        return {
            "outcome": self.outcome,
            "status": self.status,
            "rolled_back": bool(self.rolled_back),
            "checked": bool(self.checked),
            "launchable_before": bool(self.launchable_before),
            "launchable_after": bool(self.launchable_after),
            "http_status": int(self.http_status or 0),
            "reason": self.reason,
            "message": self.message,
            "snapshot": self.snapshot,
            "restored": [item.to_dict() for item in self.restored],
            "removed": list(self.removed),
            "failed": list(self.failed),
            "log_tail": list(self.log_tail),
            "before": self.before.to_dict(),
        }


def _snapshot_root(root: Path, run_id: str) -> Optional[Path]:
    if not isinstance(run_id, str) or not is_safe_run_id(run_id):
        return None
    return root / SNAPSHOT_DIRECTORY / run_id


def restore_snapshot(
    workspace: object,
    run_id: str,
    paths: Sequence[object],
) -> Tuple[Tuple[RestoredFile, ...], Tuple[str, ...], Tuple[str, ...]]:
    """Put every file of one change back; return ``(restored, removed, failed)``.

    A file the change **modified** has a copy in the snapshot directory and is written
    back from it, then re-read so the returned digest is a fact about the file that
    is on disk now. A file the change **created** has no copy, so it is removed: that
    is what "the tree before the change" means for it.

    Never raises: a path that cannot be restored is returned in ``failed``, and the
    caller reports a partial rollback as a failure. A path outside the workspace is
    refused by the repository's own normaliser rather than by a second rule here.
    """

    root = _workspace_root(workspace)
    snapshot = _snapshot_root(root, run_id) if root is not None else None
    if root is None or snapshot is None:
        return (), (), tuple(str(item) for item in paths)[:MAX_ROLLBACK_FILES]

    restored: list[RestoredFile] = []
    removed: list[str] = []
    failed: list[str] = []

    for raw in list(paths)[:MAX_ROLLBACK_FILES]:
        try:
            relative = normalize_workspace_relative_path(str(raw or ""))
        except PathSecurityError:
            failed.append(str(raw or ""))
            continue
        if relative in ("", "."):
            failed.append(str(raw or ""))
            continue
        target = root.joinpath(*relative.split("/"))
        backup = snapshot.joinpath(*relative.split("/"))
        try:
            if backup.is_file() and not backup.is_symlink():
                data = backup.read_bytes()
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".forge-restore")
                with temporary.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                digest = _digest_of(target)
                if digest != hashlib.sha256(data).hexdigest():
                    failed.append(relative)
                    continue
                restored.append(
                    RestoredFile(path=relative, sha256=digest, size=len(data))
                )
            else:
                if target.exists() or target.is_symlink():
                    target.unlink()
                removed.append(relative)
        except OSError:
            failed.append(relative)
    return tuple(restored), tuple(removed), tuple(failed)


def _rollback_message(reason: str, log_tail: Sequence[str]) -> str:
    """The owner's sentence, the reason, and the run's own log tail."""

    text = ROLLBACK_PREFIX
    if reason:
        text = f"{text} ({reason})"
    tail = [item for item in log_tail if str(item).strip()]
    if tail:
        text = text + "\n" + "\n".join(str(item) for item in tail)
    return text


# --------------------------------------------------------------------------- #
# the check the change path runs
# --------------------------------------------------------------------------- #


def verify_change(
    *,
    workspace: object,
    before: LaunchSnapshot,
    written_paths: Sequence[object],
    run_id: str,
    project_id: str = "proj-forge-change-check",
    budget: float = CHANGE_LAUNCH_BUDGET_SECONDS,
    probe: Optional[LaunchProbe] = None,
) -> RollbackRecord:
    """Prove the project still launches; roll the change back when it does not.

    The order is the order the facts become known:

    * not launchable before -> no launch is required and nothing is rolled back
      (``not_required``); the existing compile gate already judged the files;
    * launched and answered -> ``kept``, with the HTTP status that proved it;
    * could not be started by this process -> ``not_verified``: the change is left
      alone and the reason is published, because a spawn this process refused says
      nothing about the project's own code;
    * started and did not answer -> every written file is restored from the snapshot
      (``rolled_back``), or the restore is reported as ``rollback_failed``.
    """

    root = _workspace_root(workspace)
    snapshot = _snapshot_root(root, run_id) if root is not None else None
    record = RollbackRecord(
        outcome=OUTCOME_NOT_REQUIRED,
        checked=bool(before.launchable),
        launchable_before=bool(before.launchable),
        snapshot=snapshot.name if snapshot is not None else "",
        before=before,
    )
    if not before.launchable:
        record.reason = before.reason or "the project was not launchable before the change"
        return record
    if root is None or not root.is_dir():
        record.outcome = OUTCOME_NOT_VERIFIED
        record.reason = "project_workspace_missing"
        return record

    entry = None
    try:
        entry = resolve_entry((), workspace=root).entry
    except Exception:  # noqa: BLE001 - an unreadable tree has no entry point
        entry = None
    if entry is None:
        # The change removed the entry point: that is the change's own doing, so it is
        # treated exactly like "it no longer starts".
        answer = ProbeAnswer(ran=True, reason="entry_point_vanished")
    else:
        answer = (probe or _default_probe_for(project_id, budget))(root, entry)

    record.http_status = int(answer.status or 0)
    record.log_tail = tuple(answer.log_tail)
    record.launchable_after = answer.answered
    if answer.answered:
        record.outcome = OUTCOME_KEPT
        return record
    if not answer.ran:
        # Nothing of the project was started, so nothing is known about the change.
        record.outcome = OUTCOME_NOT_VERIFIED
        record.reason = answer.reason or "the project could not be started by this check"
        return record

    restored, removed, failed = restore_snapshot(root, run_id, written_paths)
    record.restored = restored
    record.removed = removed
    record.failed = failed
    record.reason = answer.reason or ("HTTP %d" % record.http_status)
    if failed:
        record.outcome = OUTCOME_ROLLBACK_FAILED
        record.message = (
            "Изменения НЕ откатаны полностью: не удалось восстановить "
            + ", ".join(failed[:5])
            + f". {ROLLBACK_PREFIX.replace('откатаны', 'не откатаны')} не подтверждён."
        )
        return record
    record.outcome = OUTCOME_ROLLED_BACK
    record.message = _rollback_message(record.reason, record.log_tail)
    return record


def _default_probe_for(project_id: str, budget: float) -> LaunchProbe:
    """The real probe, bound to this run's budget and project id."""

    def probe(root: Path, entry: EntryPoint) -> ProbeAnswer:
        return default_launch_probe(
            root, entry, budget=budget, project_id=project_id
        )

    return probe
