"""Launch one project into its own window: resolve, start (or reuse), wait, open.

The owner's sentence this module answers: *«Я хочу видеть результат, а не вечное
меню Forge.»* One request has to do what a person means by "open the project":
resolve the entry point, start the project if it is not already running, wait
briefly until it really answers, and then open **the result** in its own OS window
(:mod:`app.projects.app_window`). No second click, no URL to copy, no Forge screen.

What is real here and what is reused
------------------------------------

* **The entry point is the existing detection.** :func:`app.projects.entrypoint.resolve_entry`
  decides, from the current version's manifest only; this module never guesses.
* **A process is started by the existing runner.** :class:`app.projects.runner.LocalRunner`
  supervises it, resolves the argv, enforces the approval and the containment, and
  discovers the port. This module calls ``start_confirmed`` with exactly the
  ``confirm``/``entrypoint_digest`` a caller supplied, so the approval boundary is
  unchanged: a launch without the digest is refused by the runner, not self-approved
  here.
* **A static page is served by the existing preview.** :class:`app.projects.preview.PreviewRegistry`
  binds the read-only server; no child process is started for such a project.
* **Readiness is the runner's own port detection.** The supervisor notices the child
  binding a localhost port; this module only *waits* for that fact (bounded), it does
  not probe a guessed port.

Idempotency
-----------

A second launch of the same project is the same request and does not restart
anything: a running server (the project's own process or the read-only preview) is
**reused**, ``reused`` is ``true``, and the window is opened again -- which is what
"open it again / bring it to the front" means for a Chromium app window sharing the
same per-project profile.

Honesty
-------

Every failure is a :class:`LaunchRefusal` carrying one plain sentence: no files, no
entry point, an unlaunchable kind, the approvals missing or stale, the process dying
without binding a port, the window program missing. When a process dies but the
project's folder holds a static page, that page is served read-only and opened
instead, and the answer says both facts (``fallback: true`` plus the failure in
``notice``): the owner sees *something* and is not told the run succeeded.

A desktop-kind entry point has no windowed form in the product today; the kind set
is empty and a recognised-but-desktop kind is refused with a typed sentence rather
than launched into a browser that could not host it.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import time
from typing import Any, Dict, Optional, Tuple

from app.projects.app_window import (
    AppWindowOpener,
    WindowRefusal,
    WindowResult,
)
from app.projects.entrypoint import (
    SERVE_KIND_STATIC,
    EntryPoint,
    resolve_entry,
)
from app.projects.models import ProjectError
from app.projects.runner import (
    REFUSED,
    RUN_BLOCKED,
    RUNNING,
    STARTING,
    STOPPING,
    TERMINAL_STATES,
)
from app.projects.store import is_safe_project_id

__all__ = [
    "DESKTOP_ENTRY_KINDS",
    "LaunchRefusal",
    "LaunchResult",
    "NO_ENTRY_MESSAGE",
    "NO_FILES_MESSAGE",
    "ProjectLauncher",
    "READINESS_TIMEOUT_SECONDS",
]

#: Entry kinds that need a desktop window this product cannot host yet. The set is
#: empty today, and the check exists so a future kind is refused with a typed
#: sentence instead of being handed to a browser.
DESKTOP_ENTRY_KINDS: frozenset = frozenset()

#: How long one launch waits for the project to bind a localhost port. The runner's
#: own startup window is 8 s (``DEFAULT_STARTUP_TIMEOUT``), so this is a little
#: longer: a process that is still alive at 8 s is not failed by the runner, and the
#: launch then reports that it never opened a port rather than waiting forever.
READINESS_TIMEOUT_SECONDS = 10.0

#: The poll interval while waiting. The supervisor itself probes every 250 ms; this
#: is finer so a bound port is noticed as soon as the runner has recorded it.
READINESS_POLL_SECONDS = 0.05

#: A refusal owes the owner one sentence, and these are the two most common ones.
NO_FILES_MESSAGE = (
    "В проекте нет файлов: показывать нечего. "
    "Сначала выполните запуск, который создаст файлы."
)
NO_ENTRY_MESSAGE = "Точка входа не найдена: запускать нечего."


class LaunchRefusal(ProjectError):
    """Nothing was shown, and this is why.

    ``facts`` are additive, non-secret facts about the refusal (the run's state, its
    exit code, the redacted tail of its log). They are published beside the sentence
    so the owner can tell "the port was busy" from "the interpreter is missing"
    without reading a log file by hand.
    """

    code = "project_launch_refused"

    def __init__(
        self,
        message: str,
        *,
        facts: Optional[Dict[str, Any]] = None,
        reason_code: str = "",
    ) -> None:
        super().__init__(message)
        self.facts: Dict[str, Any] = dict(facts or {})
        self.reason_code = str(reason_code or "")


@dataclass(frozen=True)
class LaunchResult:
    """What the launch delivered: the URL, the window, and whether it was reused."""

    project_id: str
    url: str
    window: str
    pid: int
    reused: bool
    entry_kind: str
    #: True for every result this module returns: a ``LaunchResult`` exists only when a
    #: window was really opened. The field is explicit so the payload never has to infer
    #: success from the presence of a URL.
    launched: bool = True
    #: Set only when a process failed and a static page was shown instead. The
    #: sentence names the failure, so the fallback is never a silent success.
    notice: str = ""
    fallback: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """The public shape: the eight facts a client needs and nothing else."""

        payload: Dict[str, Any] = {
            "launched": bool(self.launched),
            "url": self.url,
            "window": self.window,
            "pid": int(self.pid),
            "reused": bool(self.reused),
            "project_id": self.project_id,
            "entry_kind": self.entry_kind,
        }
        if self.fallback:
            payload["fallback"] = True
        if self.notice:
            payload["notice"] = self.notice
        return payload


class ProjectLauncher:
    """Launch one project's resolved entry point into its own window.

    ``runner`` and ``previews`` are the process's one supervisor and one preview
    registry -- the same objects the routes report -- so a launch and a status
    request can never disagree about whether something is running. ``windows`` is
    the OS-window integration; a test supplies one whose ``spawn`` records the
    argument vector, so no browser opens on the machine running the suite.
    """

    def __init__(
        self,
        *,
        runner: Any = None,
        previews: Any = None,
        windows: Any = None,
        readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
        clock: Any = None,
        sleep: Any = None,
    ) -> None:
        self._runner = runner
        self._previews = previews
        self._windows = windows if windows is not None else AppWindowOpener()
        self._readiness_timeout = float(readiness_timeout)
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else time.sleep

    # ------------------------------------------------------------------ public
    def launch(
        self,
        *,
        project_id: object,
        target: Any,
        entry: Optional[EntryPoint],
        files: int,
        confirm: object = False,
        digest: object = None,
        archived: bool = False,
    ) -> LaunchResult:
        """Show the project, starting it only when it is not already running.

        The refusal order is the order the facts become known: the project id, the
        archive state, the files on disk, the entry point, the kind. Only after all
        of them a process may be started, and only the runner's own approval chain
        can start it.
        """

        key = str(project_id or "")
        if not is_safe_project_id(key):
            raise LaunchRefusal(
                "Идентификатор проекта не годится для запуска: ничего не открыто.",
                reason_code="unsafe_project_id",
                facts={"project_id": key},
            )
        if archived:
            raise LaunchRefusal(
                "Проект в архиве: сначала восстановите его, чтобы запустить.",
                reason_code="project_archived",
                facts={"project_id": key},
            )
        if int(files) <= 0:
            raise LaunchRefusal(
                NO_FILES_MESSAGE,
                reason_code="no_files",
                facts={"project_id": key, "files": int(files)},
            )
        if entry is None:
            raise LaunchRefusal(
                NO_ENTRY_MESSAGE,
                reason_code="no_entry_point",
                facts={"project_id": key},
            )
        kind = str(getattr(entry, "kind", "") or "")
        if kind in DESKTOP_ENTRY_KINDS:
            # A desktop kind exists in the vocabulary but has no windowed form in
            # this product yet: refuse it in words, never hand it to a browser.
            raise LaunchRefusal(
                "Проект этого типа открывается как отдельное приложение, "
                "и Forge пока не умеет его запускать: ничего не открыто.",
                reason_code="desktop_entry_unsupported",
                facts={"project_id": key, "entry_kind": kind},
            )

        if entry.serve_kind == SERVE_KIND_STATIC:
            return self._launch_page(key, target, entry)

        return self._launch_process(
            key, target, entry, confirm=confirm, digest=digest
        )

    # ----------------------------------------------------------- a static page
    def _launch_page(
        self, project_id: str, target: Any, entry: EntryPoint, *, notice: str = ""
    ) -> LaunchResult:
        """Serve a static page read-only (or reuse its preview) and open it."""

        if self._previews is None:
            raise LaunchRefusal(
                "Локальный предпросмотр недоступен в этой сборке: показать нечего.",
                reason_code="preview_unavailable",
                facts={"project_id": project_id, "entry_kind": str(entry.kind)},
            )
        running = self._preview_status(project_id)
        reused = bool(running.get("running")) and str(running.get("url", "") or "") != ""
        url = str(running.get("url", "") or "") if reused else ""
        if not reused:
            try:
                started = self._previews.start(
                    project_id, getattr(target, "workspace", None), entry
                )
            except Exception as exc:  # noqa: BLE001 - a preview failure is a refusal
                raise LaunchRefusal(
                    "Локальный предпросмотр не запустился "
                    f"({type(exc).__name__}): показать нечего.",
                    reason_code="preview_failed",
                    facts={"project_id": project_id, "entry_kind": str(entry.kind)},
                ) from exc
            started = dict(started or {})
            url = str(started.get("url", "") or "")
            if not started.get("running") or url == "":
                raise LaunchRefusal(
                    str(started.get("reason", "") or "Локальный предпросмотр не запустился: показать нечего."),
                    reason_code="preview_failed",
                    facts={"project_id": project_id, "entry_kind": str(entry.kind)},
                )
        window = self._open_window(project_id, url)
        return LaunchResult(
            project_id=project_id,
            url=url,
            window=window.window,
            pid=window.pid,
            reused=reused,
            entry_kind=str(entry.kind),
            notice=notice,
            fallback=bool(notice),
        )

    # --------------------------------------------------------------- a process
    def _launch_process(
        self,
        project_id: str,
        target: Any,
        entry: EntryPoint,
        *,
        confirm: object,
        digest: object,
    ) -> LaunchResult:
        """Start (or reuse) the declared process, wait for its port, open the URL."""

        if self._runner is None:
            raise LaunchRefusal(
                "Локальный запуск недоступен в этой сборке: показать нечего.",
                reason_code="runner_unavailable",
                facts={"project_id": project_id, "entry_kind": str(entry.kind)},
            )
        reason = str(getattr(entry, "reason", "") or "")
        if not entry.runnable:
            raise LaunchRefusal(
                (
                    f"Точка входа {entry.path} пока не запускается: {reason}. "
                    "Установите недостающую среду и повторите."
                )
                if reason
                else (
                    f"У точки входа {entry.path} нет команды запуска: "
                    "запускать нечего."
                ),
                reason_code="entry_not_runnable",
                facts={"project_id": project_id, "entry_kind": str(entry.kind)},
            )

        status = self._safe_status(project_id, target)
        url = str(status.get("url", "") or "")
        state = str(status.get("state", "") or "")
        reused = url != ""
        if not reused:
            if state in (STARTING, RUNNING, STOPPING):
                # A process that is already starting is this project's process: the
                # second click must not start a second copy of it.
                pass
            else:
                started = self._start(
                    project_id, target, confirm=confirm, digest=digest
                )
                state = str(started.get("state", "") or "")
                url = str(started.get("url", "") or "")
                if state in (REFUSED, RUN_BLOCKED):
                    raise self._start_refusal(project_id, entry, started)

        if url == "":
            url, status = self._wait_for_url(project_id, target)
        if url == "":
            return self._process_failure(project_id, target, entry, status, state)

        window = self._open_window(project_id, url)
        return LaunchResult(
            project_id=project_id,
            url=url,
            window=window.window,
            pid=window.pid,
            reused=reused,
            entry_kind=str(entry.kind),
        )

    # ------------------------------------------------------------------ pieces
    def _preview_status(self, project_id: str) -> Dict[str, Any]:
        if self._previews is None:
            return {}
        try:
            return dict(self._previews.status(project_id) or {})
        except Exception:  # noqa: BLE001 - an unreadable registry is "nothing running"
            return {}

    def _safe_status(self, project_id: str, target: Any) -> Dict[str, Any]:
        try:
            return dict(self._runner.status(target) or {})
        except Exception:  # noqa: BLE001 - an unreadable runner is "no live process"
            return {}

    def _start(
        self, project_id: str, target: Any, *, confirm: object, digest: object
    ) -> Dict[str, Any]:
        """Start through the runner, which is the only thing that may start a child.

        The ``confirm``/``digest`` pair is passed through unchanged: this module
        holds no approval of its own, so a caller without the digest is refused by
        the runner's own boundary instead of being self-approved here.
        """

        try:
            return dict(
                self._runner.start_confirmed(
                    target, confirm=confirm, digest=digest, offline=False
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001 - a start failure is a refusal
            raise LaunchRefusal(
                "Проект не удалось запустить "
                f"({type(exc).__name__}): показать нечего.",
                reason_code="start_failed",
                facts={"project_id": project_id},
            ) from exc

    def _start_refusal(
        self, project_id: str, entry: EntryPoint, started: Dict[str, Any]
    ) -> LaunchRefusal:
        """Turn the runner's refusal into the owner's sentence."""

        code = str(started.get("blocked_code", "") or "")
        reason = str(started.get("reason", "") or "")
        next_step = str(started.get("next_step", "") or "")
        facts: Dict[str, Any] = {
            "project_id": project_id,
            "entry_kind": str(entry.kind),
            "run_state": str(started.get("state", "") or ""),
            "run_reason": reason,
        }
        if code == "approval_required":
            return LaunchRefusal(
                "Запуск не одобрен: нужен отпечаток команды (entrypoint_digest) "
                "и подтверждение. Откройте карточку проекта заново.",
                reason_code=code,
                facts=facts,
            )
        if code == "approval_stale":
            return LaunchRefusal(
                "Команда изменилась с момента показа отпечатка: "
                "откройте карточку проекта заново и повторите запуск.",
                reason_code=code,
                facts=facts,
            )
        sentence = f"Запуск заблокирован: {reason}" if reason else "Запуск заблокирован."
        if next_step:
            sentence = f"{sentence} {next_step}."
        return LaunchRefusal(
            sentence,
            reason_code=code or "run_blocked",
            facts=facts,
        )

    def _wait_for_url(
        self, project_id: str, target: Any
    ) -> Tuple[str, Dict[str, Any]]:
        """Wait (bounded) for the runner to record the port the child bound."""

        end = self._clock() + self._readiness_timeout
        status = self._safe_status(project_id, target)
        while True:
            url = str(status.get("url", "") or "")
            if url != "":
                return url, status
            if str(status.get("state", "") or "") in TERMINAL_STATES:
                return "", status
            if self._clock() >= end:
                return "", status
            self._sleep(READINESS_POLL_SECONDS)
            status = self._safe_status(project_id, target)

    def _process_failure(
        self,
        project_id: str,
        target: Any,
        entry: EntryPoint,
        status: Dict[str, Any],
        state: str,
    ) -> LaunchResult:
        """Report the death plainly, then show a static page if the folder holds one.

        A process that never bound a port is a failure with a sentence, never a
        silent no-op. The project's own static page may still be the result the
        owner wanted to see; it is served read-only and opened, and the answer keeps
        both facts: ``fallback: true`` and the failure in ``notice``.
        """

        sentence, facts = self._failure_sentence(status, state)
        page = self._static_page_on_disk(getattr(target, "workspace", None))
        if page is not None:
            notice = f"{sentence} Показана статическая страница {page.path}."
            try:
                return self._launch_page(project_id, target, page, notice=notice)
            except LaunchRefusal:
                # The fallback did not work either: the failure above is the answer.
                pass
        raise LaunchRefusal(sentence, reason_code="run_failed", facts=facts)

    def _failure_sentence(
        self, status: Dict[str, Any], state: str
    ) -> Tuple[str, Dict[str, Any]]:
        code = status.get("exit_code")
        reason = str(status.get("reason", "") or "")
        event = str(status.get("last_event", "") or "")
        lines = [
            str(item)
            for item in (status.get("log", {}) or {}).get("lines", []) or []
            if str(item).strip() != ""
        ]
        tail = lines[-8:]
        facts: Dict[str, Any] = {
            "run_state": str(status.get("state", "") or state),
            "exit_code": code if isinstance(code, int) else None,
            "run_reason": reason,
            "log_tail": tail,
        }
        if str(status.get("state", "") or state) in TERMINAL_STATES:
            sentence = "Проект не открылся: процесс завершился"
            if isinstance(code, int):
                sentence = f"{sentence} с кодом {code}"
            sentence = f"{sentence}, не открыв локальный порт."
            if reason:
                sentence = f"{sentence} Причина: {reason}."
            elif event:
                sentence = f"{sentence} Событие: {event}."
            return sentence, facts
        timeout = f"{self._readiness_timeout:g}"
        return (
            f"Проект не открылся: за {timeout} с процесс не открыл локальный порт.",
            facts,
        )

    def _static_page_on_disk(self, workspace: Any) -> Optional[EntryPoint]:
        """The project's own static page, read from the folder as it really is.

        This is the fallback for a process that died. It reads **files on disk**,
        not prose and not a manifest claim: the page is there or it is not. The
        entry-point detection resolves it exactly as the manifest path would, and
        the preview server serves it read-only.
        """

        root = _workspace_path(workspace)
        if root is None:
            return None
        names = []
        try:
            with os.scandir(str(root)) as entries:
                for item in entries:
                    try:
                        if not item.is_file(follow_symlinks=False):
                            continue
                    except OSError:  # pragma: no cover - defensive
                        continue
                    name = item.name
                    if name.startswith(".") or not name.lower().endswith(".html"):
                        continue
                    names.append(name)
        except OSError:
            return None
        if not names:
            return None
        # ``index.html`` first, then a deterministic name: the same rule the
        # manifest detection uses for a root page.
        if "index.html" in names:
            chosen = "index.html"
        else:
            chosen = sorted(names)[0]
        try:
            return resolve_entry([{"path": chosen}], workspace=root).entry
        except Exception:  # noqa: BLE001 - detection never breaks the refusal
            return None

    def _open_window(self, project_id: str, url: str) -> WindowResult:
        try:
            return self._windows.open(project_id, url)
        except WindowRefusal as exc:
            raise LaunchRefusal(
                str(exc),
                reason_code="window_refused",
                facts={"project_id": project_id, "url": url},
            ) from exc
        except Exception as exc:  # noqa: BLE001 - an opener failure is a refusal
            raise LaunchRefusal(
                "Не удалось открыть окно приложения "
                f"({type(exc).__name__}): показать нечего.",
                reason_code="window_failed",
                facts={"project_id": project_id, "url": url},
            ) from exc


def _workspace_path(workspace: Any) -> Optional[Path]:
    """A path for a workspace value, or ``None`` when there is none."""

    if workspace is None:
        return None
    try:
        if isinstance(workspace, (str, os.PathLike)):
            return Path(workspace)
        # ``WindowsPath`` already has a ``root`` of its own, so a path-like must be
        # converted before any ``.root`` attribute is consulted.
        return Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return None
