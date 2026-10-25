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
* **Readiness is the runner's own port detection.** The supervisor reads the port from
  two sources -- the address the child's own output announced, and the sockets its pid
  tree listens on -- and proves each candidate with a real ``GET /`` before it is
  called ready (:mod:`app.projects.ports`). This module only *waits* for that fact
  (bounded); it never probes a guessed port, and it never opens a portless URL.

The manifest is a claim, reality wins
-------------------------------------

The project record's specification is the owner's own plan; it may name a port
(«ЗАПУСК: ... открывает http://127.0.0.1:8765»). That claim is **never** used to open
anything: the opened URL is the port a real HTTP answer proved. When the claim and the
observation disagree the launch says both out loud in its ``notice`` («Порт из
манифеста 8765, фактический 8790»), so a manifest that drifted cannot quietly decide
what the owner sees.

An application that exits **0** while an address it printed already answers was not a
failure: another instance of the project is serving, the launch says ``reused: true``
and opens that window. Only when *no* candidate answers is the launch refused.

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
without binding a port, the window program missing. A process that never bound a port
is **never** followed by a browser window: the answer is a typed refusal with the run's
state, its exit code and the redacted tail of its log, because a URL without a port
would show the owner a blank page and teach him nothing. When the project's folder also
holds a static page, that page is offered as ``fallback_available``/``fallback_url`` --
an explicit second action the caller must ask for (``static_fallback: true``), never a
silent substitution for the run the owner actually requested.

A blocking console application is diagnosed, not shown. The child is created without a
console window (``CREATE_NO_WINDOW``); when its output reads like a prompt (a line that
ends with ``:`` or contains ``Выбрать``/``Введите``/``Enter``/``input``) or when it exits
without a port, the refusal says «Приложение ждёт ввода в консоли — так запускать
нельзя» and points at the log. No input is ever sent to the child.

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
    is_loopback_url_with_port,
)
from app.projects.entrypoint import (
    SERVE_KIND_STATIC,
    EntryPoint,
    resolve_entry,
)
from app.projects.models import ProjectError
from app.projects.ports import port_from_url, probe_http
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
    "CONSOLE_INPUT_MESSAGE",
    "DESKTOP_ENTRY_KINDS",
    "LaunchRefusal",
    "LaunchResult",
    "MANIFEST_PORT_NOTE",
    "NO_ENTRY_MESSAGE",
    "NO_FILES_MESSAGE",
    "NOT_A_SERVER_MESSAGE",
    "ProjectLauncher",
    "READINESS_TIMEOUT_SECONDS",
    "REUSED_SERVER_NOTE",
    "looks_like_console_prompt",
]

#: The two sentences a successful launch may add beside the URL. Both name a fact the
#: owner cannot see from the window itself: a server that was already up, and a
#: manifest whose port is not the port reality chose.
REUSED_SERVER_NOTE = "Приложение уже было запущено — открыт существующий сервер."
MANIFEST_PORT_NOTE = "Порт из манифеста {manifest}, фактический {observed}."

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

#: The plain sentence a no-port launch owes the owner. It is the first thing the
#: refusal says, before the exit code and the log path.
NOT_A_SERVER_MESSAGE = "Приложение не стало сервером: порт не открылся."

#: The sentence for a child that looks like it is waiting for console input. Forge has
#: no console to type into: such an application cannot be started this way.
CONSOLE_INPUT_MESSAGE = "Приложение ждёт ввода в консоли — так запускать нельзя."

#: What a prompt-like line looks like. The rule is deliberately plain: a line that ends
#: with a colon, or that names one of the four words below, is a prompt.
_PROMPT_WORDS = ("выбрать", "введите", "enter", "input")

#: Structural lines of a Python traceback are not prompts, even though the header ends
#: with a colon. A crash must be reported as a crash, not as "waiting for input".
_TRACEBACK_PREFIXES = (
    "traceback (most recent call last)",
    "during handling of the above exception",
    "the above exception was the direct cause",
)


def looks_like_console_prompt(lines: Any) -> bool:
    """Whether a log's lines look like an application waiting for console input.

    This is a diagnosis, not an accusation: nothing here proves *why* a process is
    silent. It only recognises the two cheap shapes -- a line that ends with ``:``
    (``Введите пароль:``) and a line that contains one of the prompt words. Traceback
    structure and indented source frames are skipped: ``Traceback (most recent call
    last):`` is a crash, and calling it a prompt would hide the real error. The caller
    prints the sentence and points at the log; it never types anything into the child.
    """

    for raw in lines or ():
        original = str(raw or "").rstrip("\r\n")
        line = original.strip()
        if not line or line.startswith("--- ") or line.startswith("# "):
            # Forge's own log header and stream markers are not the child's output.
            continue
        lowered = line.casefold()
        if lowered.startswith(_TRACEBACK_PREFIXES):
            continue
        if line.startswith(("File \"", "File '")):
            continue
        if original.startswith((" ", "\t")):
            # Indented lines are source frames / continuation, never a prompt.
            continue
        if line.endswith(":") or line.endswith("："):
            return True
        if any(word in lowered for word in _PROMPT_WORDS):
            return True
    return False


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
    #: Set only when the caller explicitly asked for the project's static page
    #: (``static_fallback: true``) and that page was served instead of a process. The
    #: sentence names the request, so the fallback is never a silent success.
    notice: str = ""
    fallback: bool = False
    #: The port that really answered (verified with a ``GET /``), never a claim. ``0``
    #: only on a payload that opened no window at all.
    observed_port: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """The public shape: the facts a client needs and nothing else."""

        payload: Dict[str, Any] = {
            "launched": bool(self.launched),
            "url": self.url,
            "window": self.window,
            "pid": int(self.pid),
            "reused": bool(self.reused),
            "project_id": self.project_id,
            "entry_kind": self.entry_kind,
            "observed_port": int(self.observed_port),
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
    ``probe`` is the HTTP probe used to re-check a remembered address before a window
    is opened on it; it is injectable for the same reason as the rest.
    """

    def __init__(
        self,
        *,
        runner: Any = None,
        previews: Any = None,
        windows: Any = None,
        probe: Any = None,
        readiness_timeout: float = READINESS_TIMEOUT_SECONDS,
        clock: Any = None,
        sleep: Any = None,
    ) -> None:
        self._runner = runner
        self._previews = previews
        self._windows = windows if windows is not None else AppWindowOpener()
        #: ``None`` means "the module's real probe", resolved at call time so a test can
        #: patch :func:`app.projects.ports.probe_http` and still be asked.
        self._probe = probe
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
        static_fallback: bool = False,
        manifest_port: int = 0,
    ) -> LaunchResult:
        """Show the project, starting it only when it is not already running.

        The refusal order is the order the facts become known: the project id, the
        archive state, the files on disk, the entry point, the kind. Only after all
        of them a process may be started, and only the runner's own approval chain
        can start it.

        ``static_fallback`` is the owner's **explicit** request for the project's
        static page (the choice the refusal offers as ``fallback_available``). It is
        consulted only when the declared process did not become a server; without it a
        dead process is a refusal and no window opens.

        ``manifest_port`` is the port the project's **manifest claims** (its own
        specification text), and it is used for one thing only: the ``notice`` that
        names both numbers when reality chose a different one. It never becomes a URL.
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
            key,
            target,
            entry,
            confirm=confirm,
            digest=digest,
            static_fallback=bool(static_fallback),
            manifest_port=int(manifest_port or 0),
        )

    # ----------------------------------------------------------- a static page
    def _ensure_page_url(
        self, project_id: str, target: Any, entry: EntryPoint
    ) -> Tuple[str, bool]:
        """Serve a static page read-only (or reuse its preview) and return its URL.

        The URL is always one a probe has just answered on. A preview the registry
        still remembers is reused **only while ``GET <url>`` answers right now**: a
        session whose server stopped answering is a stale address, and opening it is
        the grey window this rule exists to prevent. Such a session is stopped and the
        page is served again; a page that still does not answer afterwards is a refusal
        with the address in it, never a window.
        """

        if self._previews is None:
            raise LaunchRefusal(
                "Локальный предпросмотр недоступен в этой сборке: показать нечего.",
                reason_code="preview_unavailable",
                facts={"project_id": project_id, "entry_kind": str(entry.kind)},
            )
        running = self._preview_status(project_id)
        reused = bool(running.get("running")) and str(running.get("url", "") or "") != ""
        url = str(running.get("url", "") or "") if reused else ""
        if reused and not self._url_answers(url):
            # The registry still lists a preview this process started, but nothing
            # answers on its port any more: that URL is stale, so it is dropped
            # instead of being opened, and the page is served afresh below.
            reused = False
            url = ""
            self._stop_preview(project_id)
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
        if not is_loopback_url_with_port(url):
            # The preview builds its URL from a bound port; a value that does not carry
            # one is not opened either -- the same rule as a project process.
            raise LaunchRefusal(
                "У локального предпросмотра нет порта: открывать нечего.",
                reason_code="preview_failed",
                facts={"project_id": project_id, "entry_kind": str(entry.kind)},
            )
        if not self._url_answers(url):
            raise LaunchRefusal(
                "Локальный предпросмотр не отвечает по адресу "
                f"{url}: окно не открыто.",
                reason_code="preview_failed",
                facts={"project_id": project_id, "entry_kind": str(entry.kind)},
            )
        return url, reused

    def _launch_page(
        self, project_id: str, target: Any, entry: EntryPoint, *, notice: str = ""
    ) -> LaunchResult:
        """Serve a static page read-only (or reuse its preview) and open it."""

        url, reused = self._ensure_page_url(project_id, target, entry)
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
            observed_port=port_from_url(url),
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
        static_fallback: bool = False,
        manifest_port: int = 0,
    ) -> LaunchResult:
        """Start (or reuse) the declared process, wait for its port, open the URL.

        A URL is opened only when it carries a port a real ``GET /`` answered on
        (``http://127.0.0.1:<port>/``). A process that did not become a server is a
        typed refusal -- or, when the caller explicitly asked for it, the project's
        static page -- and never a browser pointed at nothing.
        """

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
        # A remembered address is reused only while it still answers. The "already
        # running" case leaves a URL in a *terminal* run, so a later click has to ask
        # that address again: a server that has since stopped means the process must be
        # started afresh, never a window opened on nothing.
        reused = url != "" and self._url_answers(url)
        if url != "" and not reused:
            url = ""
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
                status = started
                if state in (REFUSED, RUN_BLOCKED):
                    raise self._start_refusal(project_id, entry, started)

        if url == "":
            url, status = self._wait_for_url(project_id, target)
        state = str(status.get("state", "") or state)
        if url == "":
            if static_fallback:
                return self._explicit_static_fallback(
                    project_id, target, entry, status, state
                )
            raise self._port_refusal(project_id, target, entry, status, state)
        if not is_loopback_url_with_port(url):
            # Defence in depth: the opener refuses this too, but the refusal has to
            # carry the run's facts, and no window may be opened for it.
            raise self._port_refusal(
                project_id, target, entry, status, state, url=url
            )

        observed_port = self._observed_port(url, status)
        # A server that was already up before this click is a success, and the payload
        # says so instead of pretending a second copy was started.
        reused = reused or bool(status.get("reused"))
        window = self._open_window(project_id, url)
        return LaunchResult(
            project_id=project_id,
            url=url,
            window=window.window,
            pid=window.pid,
            reused=reused,
            entry_kind=str(entry.kind),
            notice=self._launch_notice(
                manifest_port, observed_port, bool(status.get("reused"))
            ),
            observed_port=observed_port,
        )

    # ------------------------------------------------------------- the two numbers
    def _url_answers(self, url: str) -> bool:
        """Whether a loopback URL still answers, right now.

        Called before any remembered address is reused -- a preview the registry still
        holds or a run that recorded a port. A stale URL is the difference between a
        window that shows the project and a grey window that shows nothing.
        """

        port = port_from_url(url)
        probe = self._probe if self._probe is not None else probe_http
        return port > 0 and probe(port) > 0

    @staticmethod
    def _observed_port(url: str, status: Dict[str, Any]) -> int:
        """The port reality answered on: the supervisor's own fact, else the URL's.

        The runner records ``observed_port`` when a real ``GET /`` succeeded; the run
        block also keeps ``port``. The URL is the last source, and it is parsed only
        because it is the address about to be opened -- never used to invent one.
        """

        for key in ("observed_port", "port"):
            try:
                number = int(status.get(key) or 0)
            except (TypeError, ValueError):
                number = 0
            if number > 0:
                return number
        return port_from_url(url)

    @staticmethod
    def _launch_notice(manifest_port: int, observed_port: int, reused: bool) -> str:
        """What the owner has to read beside the URL, and nothing more.

        Two facts can only be seen from here: that the window shows a server which was
        already running, and that the manifest's port is not the port that answered.
        The second sentence is emitted **only** when the two numbers really differ.
        """

        parts = []
        if reused:
            parts.append(REUSED_SERVER_NOTE)
        try:
            claimed = int(manifest_port or 0)
        except (TypeError, ValueError):
            claimed = 0
        if claimed > 0 and observed_port > 0 and claimed != observed_port:
            parts.append(
                MANIFEST_PORT_NOTE.format(manifest=claimed, observed=observed_port)
            )
        return " ".join(parts)

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

    def _stop_preview(self, project_id: str) -> None:
        """Forget a preview that no longer answers; a failure here is not fatal."""

        if self._previews is None:
            return
        stop = getattr(self._previews, "stop", None)
        if not callable(stop):
            return
        try:
            stop(project_id)
        except Exception:  # noqa: BLE001 - the restart below is what matters
            return

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

    def _explicit_static_fallback(
        self,
        project_id: str,
        target: Any,
        entry: EntryPoint,
        status: Dict[str, Any],
        state: str,
    ) -> LaunchResult:
        """Serve the project's static page because the caller explicitly asked for it.

        This is the second action the refusal offers as ``fallback_available``: the
        owner read that the application never became a server, and chose the saved
        page on purpose. It is never taken on his behalf.
        """

        page = self._static_page_on_disk(getattr(target, "workspace", None))
        if page is None:
            raise self._port_refusal(
                project_id, target, entry, status, state, fallback_requested=True
            )
        _facts, sentence = self._refusal_facts(status, state)
        notice = (
            f"{sentence} По запросу показана статическая страница {page.path}."
        )
        return self._launch_page(project_id, target, page, notice=notice)

    def _port_refusal(
        self,
        project_id: str,
        target: Any,
        entry: EntryPoint,
        status: Dict[str, Any],
        state: str,
        *,
        url: str = "",
        fallback_requested: bool = False,
    ) -> LaunchRefusal:
        """The honest answer to a process that never became a server.

        Nothing is opened. The refusal carries the run's state, its exit code, the
        redacted tail of its log (including the prompt-like line, when that is what the
        log shows) and -- when the folder holds one -- the project's static page as an
        explicit *offer*: ``fallback_available`` plus a real ``fallback_url``. The page
        is served read-only so the offer has a URL, but no window is opened for it here.
        """

        facts, sentence = self._refusal_facts(status, state)
        facts["project_id"] = project_id
        facts["entry_kind"] = str(entry.kind)
        if url:
            facts["url"] = url
            sentence = (
                "Адрес проекта без порта: открывать нечего. " + NOT_A_SERVER_MESSAGE
            )
        if fallback_requested:
            facts["fallback_requested"] = True
        page = self._static_page_on_disk(getattr(target, "workspace", None))
        fallback_url = ""
        if page is not None:
            try:
                fallback_url, _reused = self._ensure_page_url(project_id, target, page)
            except LaunchRefusal:
                fallback_url = ""
        facts["fallback_available"] = fallback_url != ""
        if fallback_url:
            facts["fallback_url"] = fallback_url
            facts["fallback_page"] = page.path if page is not None else ""
            if page is not None:
                sentence = (
                    f"{sentence} Сохранённую страницу {page.path} можно показать "
                    "отдельным действием."
                )
        reason_code = (
            "console_input_required"
            if facts.get("waiting_for_input")
            else "port_not_opened"
        )
        facts["refusal_reason"] = reason_code
        return LaunchRefusal(sentence, reason_code=reason_code, facts=facts)

    def _refusal_facts(
        self, status: Dict[str, Any], state: str
    ) -> Tuple[Dict[str, Any], str]:
        """The run's own facts and the one sentence a no-port launch owes the owner."""

        code = status.get("exit_code")
        reason = str(status.get("reason", "") or "")
        event = str(status.get("last_event", "") or "")
        log = dict(status.get("log", {}) or {})
        lines = [
            str(item)
            for item in log.get("lines", []) or []
            if str(item).strip() != ""
        ]
        tail = lines[-20:]
        run_state = str(status.get("state", "") or state)
        waiting = looks_like_console_prompt(tail)
        facts: Dict[str, Any] = {
            "run_state": run_state,
            "exit_code": code if isinstance(code, int) else None,
            "run_reason": reason,
            "last_event": event,
            "waiting_for_input": bool(waiting),
            "log_tail": tail,
            "log_path": str(log.get("path", "") or ""),
        }
        sentence = NOT_A_SERVER_MESSAGE
        if run_state in TERMINAL_STATES:
            if isinstance(code, int):
                sentence = f"{sentence} Процесс завершился с кодом {code}."
            else:
                sentence = f"{sentence} Процесс завершился, не открыв порт."
        else:
            timeout = f"{self._readiness_timeout:g}"
            sentence = (
                f"{sentence} За {timeout} с процесс не открыл локальный порт."
            )
        if waiting:
            sentence = f"{CONSOLE_INPUT_MESSAGE} {sentence}"
        if facts["log_path"]:
            sentence = f"{sentence} Журнал: {facts['log_path']}."
        return facts, sentence

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
