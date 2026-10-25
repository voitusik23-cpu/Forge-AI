"""Run one project's own entry point locally, under the existing execution plane.

What this module is, and what it is not
---------------------------------------

It is the **supervisor** for a project's declared process: it starts the argv that
:mod:`app.projects.entrypoint` resolved (never one taken from a request), keeps the
child inside its own workspace, captures bounded and redacted output, notices when
the child binds a localhost port, and terminates the process **tree** on stop.

It is not a second execution plane. Before a single process exists, every start
passes the repository's existing authorization chain --
``RunScope`` -> ``ExecutionRequest`` -> ``ExecutionPolicy`` -> ``ApprovalPolicy`` ->
:meth:`app.execution.authorizer.ExecutionCoordinator.authorize` -- and the spawn
uses only the token that coordinator mints. The supervisor exists because the
existing adapter's ``execute`` is *synchronous*: it waits for the child and kills
the tree when it returns, which is the right shape for a build step and the wrong
one for a server that must stay up while a preview serves it. ``authorize`` is the
same chain without the dispatch, so nothing here can widen authority; it can only
use authority that was granted.

The hard rules, each of which is a test:

* an explicit approval is required, for **this** project and **this** exact command:
  the API must be given ``{"confirm": true}`` and the digest the owner was shown,
  and the approval is single-use and expires;
* ``subprocess.Popen(..., shell=False)`` with an explicitly minimal environment:
  ``PATH``, ``SYSTEMROOT``, ``TEMP`` and ``PYTHONIOENCODING=utf-8`` only, plus the
  marker ``FORGE_SANDBOX=1``. No Forge credential, no ``*_API_KEY`` and no ``.env``
  value ever reaches a child;
* the child is **contained by the OS** where that is possible without administrator
  rights (:mod:`app.execution.containment`): a job object with
  ``KILL_ON_JOB_CLOSE``, a per-process memory ceiling and an active-process ceiling;
  a suspended spawn whose primary token is lowered to low integrity and verified;
  and a measured write boundary that records whether the child could write Forge's
  own data. The run payload carries the measured result and never upgrades a guess
  to a claim;
* output is captured into two bounded ring buffers (64 KiB each), every line is
  scanned by the repository's own secret scanner and persisted redacted to
  ``.forge/projects/<id>/run.log``;
* a one-shot command that overruns its timeout is killed (``TIMED_OUT``); a server
  that bound a port is *not* killed by that timeout, because for it the timeout only
  answers "did it start". A server *is* killed when the configured maximum lifetime
  (default: none) is reached;
* stop kills the tree by verified pid (job object / Toolhelp snapshot /
  ``taskkill /T``), never by image name;
* the child's network is **inherited**, and the result says so: there is no network
  isolation here and claiming one would be a lie;
* ``pip install`` and ``npm install`` never run. A missing dependency is reported as
  ``RUN_BLOCKED`` with the reason and the next step, not fixed silently.
"""

from __future__ import annotations

import atexit
import codecs
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.context.validation import find_forbidden_value_content
from app.execution.adapter import (
    assign_process_tree_job,
    close_process_tree_job,
    create_process_tree_job,
    process_tree_pids,
    set_process_tree_job_limits,
    terminate_process_tree,
)
from app.execution.authorizer import ExecutionCoordinator
from app.execution.containment import (
    DEFAULT_MAX_PROCESSES,
    DEFAULT_MEMORY_MB,
    INTEGRITY_RESTRICTED,
    INTEGRITY_UNCHANGED,
    NETWORK_INHERITED,
    SANDBOX_ENV_VAR,
    SandboxLimits,
    SandboxReport,
    lower_process_integrity,
    prepare_low_integrity_workspace,
    probe_write_boundary,
    process_integrity_level,
    resume_process,
)
from app.execution.identity import CommandIdentity, WorkspaceIdentity
from app.execution.intent import AuthorizedExecution, IntentBuilder
from app.execution.profile import ExecutionEnvironmentType, ProjectExecutionProfile
from app.execution.redaction import DefaultSecretRedactor
from app.execution.request import ExecutionRequest
from app.execution.sandbox import (
    WorkspaceBoundary,
    WorkspaceContainmentError,
    canonical_root,
)
from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.projects.entrypoint import (
    ENTRY_KIND_NODE_NPM,
    ENTRY_KIND_NODE_SCRIPT,
    ENTRY_KIND_PYTHON_MODULE,
    ENTRY_KIND_PYTHON_SCRIPT,
    ENTRY_KIND_STATIC_HTML,
    entry_digest,
    node_executable,
    npm_command,
    python_executable,
)
from app.projects.ports import (
    first_answering,
    loopback_url,
    parse_banner_ports,
)
from app.projects.store import (
    ProjectStore,
    default_projects_root,
    is_safe_project_id,
    repository_root,
)
from app.runtime.run_scope import RunScope, require_active_scope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import (
    ApprovalPolicy,
    ApprovalResolution,
    ApprovalRequest,
    ApprovalState,
)
from app.tools.workspace import Workspace, WorkspacePathError

__all__ = [
    "FAILED",
    "LocalRunner",
    "MAX_LOG_BYTES",
    "NOT_RUN",
    "REFUSED",
    "RUNNABLE_KINDS",
    "RUN_BLOCKED",
    "RUNNING",
    "RUN_STATES",
    "RunTarget",
    "STARTING",
    "STOPPED",
    "STOPPING",
    "SUCCEEDED",
    "TIMED_OUT",
    "redact_output_text",
]


# --------------------------------------------------------------------------- #
# states
# --------------------------------------------------------------------------- #

NOT_RUN = "NOT_RUN"
STARTING = "STARTING"
RUNNING = "RUNNING"
STOPPING = "STOPPING"
STOPPED = "STOPPED"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"
TIMED_OUT = "TIMED_OUT"
REFUSED = "REFUSED"
#: A recognised entry point that must not be launched as declared: an interpreter
#: is missing, or the project's declared dependencies are not installed.
RUN_BLOCKED = "RUN_BLOCKED"

RUN_STATES = (
    NOT_RUN,
    STARTING,
    RUNNING,
    STOPPING,
    STOPPED,
    SUCCEEDED,
    FAILED,
    TIMED_OUT,
    REFUSED,
    RUN_BLOCKED,
)

#: The states in which no process of this project is alive.
TERMINAL_STATES = frozenset(
    {NOT_RUN, STOPPED, SUCCEEDED, FAILED, TIMED_OUT, REFUSED, RUN_BLOCKED}
)

#: The kinds this module may supervise. ``static_html`` belongs to the preview
#: server and is deliberately not runnable here.
RUNNABLE_KINDS = (
    ENTRY_KIND_PYTHON_SCRIPT,
    ENTRY_KIND_PYTHON_MODULE,
    ENTRY_KIND_NODE_SCRIPT,
    ENTRY_KIND_NODE_NPM,
)

#: A child is created suspended and only resumed after its token has been lowered to
#: low integrity: without this window the child could run before the restriction
#: applies, which would make the containment a race instead of a boundary.
_CREATE_SUSPENDED = 0x00000004

#: A console application run **without a console window**. Forge itself runs under
#: ``pythonw`` (no console), so a console-subsystem child created without this flag
#: gets a brand-new console allocated by Windows -- the black window the owner saw
#: when «Запустить приложение» started the project. It is combined with
#: ``CREATE_NEW_PROCESS_GROUP``: the child keeps its own group and its pipes, and no
#: window is ever shown. ``0`` off Windows.
_CREATE_NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)

#: Per-stream capture bound. Beyond it the stream is truncated with a marker: an
#: unbounded buffer would let a chatty child exhaust this process.
MAX_LOG_BYTES = 64 * 1024

#: Written in place of secret material. The scanner decides; this is the word.
REDACTED = "[redacted]"
#: Appended once when a stream passed :data:`MAX_LOG_BYTES`.
TRUNCATION_MARKER = "[output truncated: only the first 64 KiB of this stream was kept]"

#: How long an approval stays usable without being consumed.
APPROVAL_TTL_SECONDS = 600.0

#: The bounded tail the status and log routes report.
MAX_REPORTED_LINES = 200

#: A child that has neither exited nor bound a port within this many seconds is
#: treated as a long-running one-shot, and the run timeout then applies to it.
DEFAULT_STARTUP_TIMEOUT = 8.0
#: The wall-clock bound for a script that is meant to finish.
DEFAULT_TIMEOUT = 60.0

#: The words a missing dependency appears as. Reported, never repaired.
_MISSING_DEPENDENCY_MARKERS = (
    "modulenotfounderror",
    "no module named",
    "importerror: cannot import",
    "cannot find module",
    "err_module_not_found",
    "module_not_found",
)

REASON_DEPENDENCIES = "Причина: зависимости не установлены"
NEXT_STEP_DEPENDENCIES = "Следующий шаг: установить зависимости явно"

_REDACTOR = DefaultSecretRedactor()


# --------------------------------------------------------------------------- #
# redaction and bounded output
# --------------------------------------------------------------------------- #


def redact_output_text(text: str) -> str:
    """Redact one line of child output with the repository's own scanner.

    Two layers, both already used by this repository:

    * :class:`app.execution.redaction.DefaultSecretRedactor` replaces known
      credential shapes (``sk-…``, ``ghp_…``, ``Bearer …``, ``token=…``) with
      ``[REDACTED]``;
    * :func:`app.context.validation.find_forbidden_value_content` is the scanner
      that already refuses secret material elsewhere. When it still recognises
      something the shape patterns did not cover, the whole line is replaced by
      ``[redacted]``: a partially rewritten secret is a leaked secret.
    """

    if not isinstance(text, str) or text == "":
        return text
    cleaned = _REDACTOR.redact_text(text)
    try:
        if find_forbidden_value_content(cleaned) is not None:
            return REDACTED
    except Exception:  # noqa: BLE001 - the scanner must not break a run
        return REDACTED
    return cleaned


class _StreamLog:
    """One stream's bounded, line-oriented, redacted capture.

    The bound is applied to the *redacted* text that is kept, so a child cannot
    grow the buffer with either a flood of output or a flood of secrets. Lines are
    assembled with an incremental UTF-8 decoder, so a multi-byte character split
    across two reads is not mangled.
    """

    def __init__(self, limit: int = MAX_LOG_BYTES) -> None:
        self._limit = limit
        self._lock = threading.Lock()
        self._lines: List[str] = []
        self._partial = ""
        self._size = 0
        self._truncated = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        text = self._decoder.decode(chunk)
        if not text:
            return
        self._append(text)

    def close(self) -> None:
        try:
            tail = self._decoder.decode(b"", True)
        except Exception:  # noqa: BLE001 - a damaged stream is not a failure
            tail = ""
        if tail:
            self._append(tail)

    def _append(self, text: str) -> None:
        with self._lock:
            buffered = self._partial + text
            parts = buffered.split("\n")
            self._partial = parts.pop()
            for line in parts:
                self._store(line)

    def _store(self, line: str) -> None:
        if self._truncated:
            # The bound is on what is *kept*: once it is reached, later lines are
            # dropped rather than silently rewriting history. The marker that is
            # appended already says so.
            return
        redacted = redact_output_text(line.rstrip("\r"))
        cost = len(redacted.encode("utf-8")) + 1
        if self._size + cost > self._limit:
            self._truncated = True
            return
        self._lines.append(redacted)
        self._size += cost

    def snapshot(self) -> Tuple[Tuple[str, ...], bool]:
        with self._lock:
            lines = list(self._lines)
            if self._partial:
                lines.append(redact_output_text(self._partial))
            truncated = self._truncated
        if truncated:
            lines.append(TRUNCATION_MARKER)
        return tuple(lines), bool(truncated)

    def text(self) -> str:
        lines, _truncated = self.snapshot()
        return "\n".join(lines)

    @property
    def truncated(self) -> bool:
        with self._lock:
            return self._truncated


# --------------------------------------------------------------------------- #
# the target of one local run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunTarget:
    """The three facts a local run needs, all resolved server-side.

    ``workspace`` is the project's own directory (an absolute path or a
    :class:`app.tools.workspace.Workspace`). ``entry`` is the one entry point
    :mod:`app.projects.entrypoint` detected from the manifest; it may be ``None``,
    and that is reported rather than guessed. ``project_path`` is only the
    repository-relative string the payload publishes.
    """

    project_id: str
    workspace: Any = None
    entry: Any = None
    project_path: str = ""


@dataclass
class _RunFacts:
    """Everything the status payload is derived from, for one project."""

    project_id: str
    state: str = NOT_RUN
    reason: str = ""
    next_step: str = ""
    blocked_code: str = ""
    entry_kind: str = ""
    entry_path: str = ""
    run_command: str = ""
    command_display: str = ""
    executable_display: str = ""
    arguments: Tuple[str, ...] = ()
    digest: str = ""
    project_path: str = ""
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    port: int = 0
    #: The port a real ``GET /`` really answered on (:mod:`app.projects.ports`). It is
    #: the same number as ``port`` for a serving child; it is the *observed* fact the
    #: launch payload publishes, and it can name a server this run did not start.
    observed_port: int = 0
    #: True when the port that answers belongs to a server that was **already running**:
    #: the child printed its address and exited 0 without ever binding it. A reused
    #: server is a success, not a refusal, and this flag is how the payload says so.
    reused: bool = False
    url: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    updated_at: float = 0.0
    network_policy: str = "inherited"
    offline: bool = False
    served: bool = False
    stop_requested: bool = False
    sandbox: SandboxReport = field(default_factory=SandboxReport)
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    process: Optional[subprocess.Popen] = None
    job: Optional[int] = None
    log_path: Optional[Path] = None
    stdout_log: _StreamLog = field(default_factory=_StreamLog)
    stderr_log: _StreamLog = field(default_factory=_StreamLog)
    readers: List[threading.Thread] = field(default_factory=list)
    last_event: str = ""
    last_persist: float = 0.0

    @property
    def alive(self) -> bool:
        return self.state in (STARTING, RUNNING, STOPPING)

    @property
    def running(self) -> bool:
        return self.state == RUNNING


def _utc_now() -> float:
    return time.time()


def _iso(timestamp: float) -> str:
    if not timestamp:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #


class LocalRunner:
    """Start, supervise and stop one project's declared local process.

    One instance per process, composed beside the preview registry so a status
    request and a detail payload can never disagree about whether a process is up.
    The job-object handle, the child and the log buffers live here and nowhere
    else; nothing about them is written into a project record, where it would be a
    claim that outlives the process.
    """

    def __init__(
        self,
        *,
        store: Optional[ProjectStore] = None,
        previews: Any = None,
        coordinator: Optional[ExecutionCoordinator] = None,
        timeout: float = DEFAULT_TIMEOUT,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        memory_mb: int = DEFAULT_MEMORY_MB,
        max_processes: int = DEFAULT_MAX_PROCESSES,
        server_lifetime: Optional[float] = None,
        sandbox_enabled: bool = True,
    ) -> None:
        self._store = store if store is not None else ProjectStore()
        self._previews = previews
        self._coordinator = coordinator or ExecutionCoordinator()
        self._timeout = float(timeout)
        self._startup_timeout = float(startup_timeout)
        # Containment configuration. ``server_lifetime`` is a wall-clock maximum for a
        # *serving* process and is off by default: a preview the owner is using must
        # not disappear on a timer unless the owner asked for one.
        self._memory_mb = max(0, int(memory_mb))
        self._max_processes = max(0, int(max_processes))
        self._server_lifetime = (
            float(server_lifetime) if server_lifetime not in (None, 0) else 0.0
        )
        self._sandbox_enabled = bool(sandbox_enabled)
        self._lock = threading.RLock()
        self._persist_lock = threading.Lock()
        self._runs: Dict[str, _RunFacts] = {}
        self._approvals: Dict[str, Tuple[str, str, float, bool]] = {}
        self._atexit_registered = False

    # ------------------------------------------------------------------ public
    def digest_for(self, target: RunTarget) -> str:
        """The digest of the command this target would run, or ``""``.

        The value the owner is shown and the value an approval must name. It is
        computed from the resolved argv, so a command that changed cannot inherit an
        approval that named the old one.
        """

        entry = getattr(target, "entry", None)
        if entry is None:
            return ""
        return entry_digest(
            getattr(entry, "kind", ""),
            getattr(entry, "path", ""),
            tuple(getattr(entry, "command", ()) or ()),
        )

    def confirm(
        self,
        target: RunTarget,
        *,
        confirm: object,
        digest: object,
        offline: object = False,
    ) -> Optional[str]:
        """Record an explicit approval for this target, or return ``None``.

        ``confirm`` must be the boolean ``true`` -- a truthy string is not a
        confirmation -- and ``digest`` must equal the digest of the command that
        would run *now*. Neither value can come from prose: both are compared.

        ``offline`` records the owner's declaration that this command is
        offline-only. It is a **marker**, not a boundary: there is no proxy and no
        packet filter here, the child still inherits the machine's network, and the
        payload says ``network_policy: "inherited"`` either way.
        """

        if confirm is not True:
            return None
        current = self.digest_for(target)
        if not current:
            return None
        if not isinstance(digest, str) or not digest.strip() or digest.strip() != current:
            return None
        if not isinstance(offline, bool):
            return None
        token = secrets.token_hex(16)
        with self._lock:
            self._approvals[token] = (
                str(getattr(target, "project_id", "") or ""),
                current,
                time.monotonic(),
                bool(offline),
            )
        return token

    def start_confirmed(
        self,
        target: RunTarget,
        *,
        confirm: object,
        digest: object,
        offline: object = False,
    ) -> Dict[str, Any]:
        """``confirm`` then ``start``: the one call the HTTP route makes."""

        return self.start(
            target, self.confirm(target, confirm=confirm, digest=digest, offline=offline)
        )

    def restart_confirmed(
        self,
        target: RunTarget,
        *,
        confirm: object,
        digest: object,
        offline: object = False,
    ) -> Dict[str, Any]:
        """Stop any current process, then start this command under a fresh approval."""

        return self.restart(
            target,
            self.confirm(target, confirm=confirm, digest=digest, offline=offline),
        )

    def start(self, target: RunTarget, approval_token: object = None) -> Dict[str, Any]:
        """Start the target's declared command **only** with a valid approval.

        Refuses -- in the order the facts become known -- when the project id is
        unusable, the workspace is gone, the entry point is static or absent, the
        recognised shape is blocked (no interpreter, or dependencies not installed),
        the path is not a root file, the command is not in the allowlist, the
        approval is missing/expired/for another command, a process is already up, or
        the execution chain denies it. Every refusal is a returned state, never an
        exception.
        """

        project_id = str(getattr(target, "project_id", "") or "")
        facts = self._facts_for(target)

        if not is_safe_project_id(project_id):
            return self._refuse(facts, "unsafe_project_id", "the project id is not usable")
        entry = getattr(target, "entry", None)

        if entry is None:
            return self._refuse(
                facts,
                "no_entry_point",
                "no entry point was detected in the artifact manifest",
            )

        kind = str(getattr(entry, "kind", "") or "")
        if kind == ENTRY_KIND_STATIC_HTML:
            return self._refuse(
                facts,
                "static_entry_point",
                "this entry point is a static page: open the preview instead of running it",
            )
        if kind not in RUNNABLE_KINDS:
            return self._refuse(
                facts, "unsupported_entry_kind", f"the entry kind {kind!r} cannot be run"
            )

        blocked = str(getattr(entry, "reason", "") or "")
        command = tuple(getattr(entry, "command", ()) or ())
        if blocked or not command:
            return self._block(
                facts,
                blocked or "the entry point has no command on this machine",
                "install the missing runtime, then run again",
                code="runtime_missing",
            )

        try:
            workspace = self._workspace_root(target)
        except _TargetError as exc:
            return self._refuse(facts, exc.code, exc.message)
        facts.diagnostics["workspace_root"] = str(workspace)

        try:
            self._check_path(kind, str(getattr(entry, "path", "") or ""), workspace)
        except _TargetError as exc:
            return self._refuse(facts, exc.code, exc.message)

        try:
            self._check_allowlist(kind, command, str(getattr(entry, "path", "") or ""))
        except _TargetError as exc:
            return self._refuse(facts, exc.code, exc.message)

        with self._lock:
            current = self._runs.get(project_id)
            if current is not None and current.alive:
                return self._payload(current, include_log=True)

        approval = self._consume_approval(project_id, approval_token)
        if approval is None:
            return self._refuse(
                facts,
                "approval_required",
                (
                    "the run was not approved: POST /run must carry "
                    '{"confirm": true} and the entrypoint digest that was shown'
                ),
            )
        if approval[0] != facts.digest:
            return self._refuse(
                facts,
                "approval_stale",
                "the command changed since the approval, so the approval no longer applies",
            )
        facts.offline = bool(approval[1])

        authorized, denial = self._authorize(
            project_id, workspace, kind, command, facts.digest
        )
        if denial is not None or authorized is None:
            reason = str(
                (denial.metadata or {}).get("denial_reason")
                if denial is not None
                else "authorization_failed"
            )
            message = str(getattr(denial, "stderr", "") or "the execution chain refused")
            return self._refuse(facts, reason or "authorization_failed", message)

        return self._spawn(facts, authorized, workspace)

    def stop(self, project_id: object) -> Dict[str, Any]:
        """Terminate this project's process tree and report ``STOPPED``."""

        key = str(project_id or "")
        with self._lock:
            facts = self._runs.get(key)
            if facts is None:
                return self._payload(self._empty(key), include_log=True)
            if not facts.alive:
                return self._payload(facts, include_log=True)
            process = facts.process
            facts.state = STOPPING
            facts.stop_requested = True
            facts.last_event = "stop requested"
            facts.updated_at = _utc_now()
        if process is not None:
            self._finish(facts, process, process.poll())
        else:
            with self._lock:
                facts.state = STOPPED
                facts.finished_at = _utc_now()
                facts.updated_at = facts.finished_at
                facts.last_event = "stopped"
            self._unregister_preview(facts)
            self._persist(facts)
        return self._payload(facts, include_log=True)

    def restart(self, target: RunTarget, approval_token: object = None) -> Dict[str, Any]:
        """Stop any process this project has, then start it again under approval.

        One approval token buys exactly one start, so a restart needs its own. A
        restart while nothing is running is still a start (a fresh process), never a
        silent re-use of the previous one.
        """

        project_id = str(getattr(target, "project_id", "") or "")
        with self._lock:
            facts = self._runs.get(project_id)
            was_alive = facts is not None and facts.alive
        if was_alive:
            self.stop(project_id)
        return self.start(target, approval_token)

    # ------------------------------------------------------------------ status
    def status(self, target: RunTarget) -> Dict[str, Any]:
        """The run block for one project, derived from live state only."""

        key = str(getattr(target, "project_id", "") or "")
        with self._lock:
            facts = self._runs.get(key)
        if facts is None:
            facts = self._facts_for(target)
        return self._payload(facts, include_log=True)

    def log(self, target: RunTarget) -> Dict[str, Any]:
        """The bounded, redacted log of one project's last run."""

        key = str(getattr(target, "project_id", "") or "")
        with self._lock:
            facts = self._runs.get(key)
        payload = self._payload(facts, include_log=True) if facts is not None else self._payload(
            self._facts_for(target), include_log=True
        )
        return {
            "project_id": key,
            "state": payload["state"],
            "path": payload["log"]["path"],
            "relative_path": payload["log"]["relative_path"],
            "truncated": payload["log"]["truncated"],
            "lines": payload["log"]["lines"],
            "count": payload["log"]["count"],
            "readable": payload["log"]["readable"],
            "network_policy": payload["network_policy"],
        }

    def state(self, project_id: object) -> str:
        """The bare state word, for the stage gate."""

        with self._lock:
            facts = self._runs.get(str(project_id or ""))
        return facts.state if facts is not None else NOT_RUN

    def port(self, project_id: object) -> int:
        """The port the process bound, or ``0`` when it bound none."""

        with self._lock:
            facts = self._runs.get(str(project_id or ""))
        return facts.port if facts is not None else 0

    def stop_all(self) -> None:
        """Stop every process this runner started (used at shutdown)."""

        with self._lock:
            keys = [key for key, facts in self._runs.items() if facts.alive]
        for key in keys:
            try:
                self.stop(key)
            except Exception:  # noqa: BLE001 - shutdown is best effort
                pass

    # ------------------------------------------------------------------ facts
    def _facts_for(self, target: RunTarget) -> _RunFacts:
        entry = getattr(target, "entry", None)
        key = str(getattr(target, "project_id", "") or "")
        facts = _RunFacts(project_id=key)
        facts.project_path = str(getattr(target, "project_path", "") or "")
        if entry is not None:
            command = tuple(getattr(entry, "command", ()) or ())
            facts.entry_kind = str(getattr(entry, "kind", "") or "")
            facts.entry_path = str(getattr(entry, "path", "") or "")
            facts.run_command = str(getattr(entry, "run_command", "") or "")
            facts.command_display = facts.run_command
            facts.executable_display = self._executable_display(command)
            facts.arguments = self._public_arguments(command)
            facts.digest = self.digest_for(target)
        return facts

    def _empty(self, project_id: str) -> _RunFacts:
        return _RunFacts(project_id=project_id)

    @staticmethod
    def _executable_display(command: Sequence[str]) -> str:
        if not command:
            return ""
        name = os.path.basename(str(command[0]).replace("\\", "/"))
        return name

    @staticmethod
    def _public_arguments(command: Sequence[str]) -> Tuple[str, ...]:
        if not command:
            return ()
        # An absolute path is never published outside ``diagnostics``: a wrapper
        # path (``npm-cli.js``) is an interpreter detail, so it is dropped from the
        # public argv rather than named.
        return tuple(
            str(part) for part in command[1:] if not os.path.isabs(str(part))
        )

    # -------------------------------------------------------------- validation
    def _workspace_root(self, target: RunTarget) -> Path:
        raw = getattr(target, "workspace", None)
        if raw is None:
            raise _TargetError("workspace_unavailable", "the project workspace is unavailable")
        try:
            workspace = raw if isinstance(raw, Workspace) else Workspace(root=raw)
            root = Path(workspace.root)
        except (WorkspacePathError, OSError, RuntimeError, TypeError, ValueError):
            raise _TargetError(
                "workspace_unavailable", "the project workspace is unavailable"
            ) from None
        if not root.is_dir():
            raise _TargetError("workspace_missing", "the project workspace is missing")
        return root

    def _check_path(self, kind: str, path_value: str, workspace: Path) -> None:
        """The entry path must be a **root** file (or ``package.json``) that exists.

        Nothing here trusts the dataclass's own validation: this is the last point
        before a process is authorized, so the path is normalized again, refused if
        it has a component, and resolved inside the workspace with ``..``, absolute
        paths and symlinks all refused.
        """

        try:
            normalized = normalize_workspace_relative_path(path_value)
        except PathSecurityError as exc:
            raise _TargetError(
                "unsafe_entry_path", f"the entry path is not allowed: {exc}"
            ) from None
        if normalized == ".":
            raise _TargetError("unsafe_entry_path", "the entry path is not allowed")
        if "/" in normalized:
            # A script entry is a file at the project root; a module entry names a
            # root package. A nested path would let the manifest reach outside the
            # files the run wrote at the root.
            raise _TargetError(
                "unsafe_entry_path",
                "the entry point must be at the project root, not in a subdirectory",
            )
        if kind == ENTRY_KIND_NODE_NPM and normalized != "package.json":
            raise _TargetError(
                "unsafe_entry_path", "an npm entry point must be package.json"
            )
        boundary = WorkspaceBoundary.for_workspace(Workspace(root=workspace))
        try:
            target = boundary.resolve_relative(normalized)
        except WorkspaceContainmentError as exc:
            raise _TargetError(
                "unsafe_entry_path", f"the entry path leaves the workspace: {exc}"
            ) from None
        if target.is_symlink():
            raise _TargetError("unsafe_entry_path", "the entry point is a link")
        if kind == ENTRY_KIND_PYTHON_MODULE:
            main = target / "__main__.py"
            if not target.is_dir() or main.is_symlink() or not main.is_file():
                raise _TargetError(
                    "entry_missing", "the package has no __main__.py"
                )
            return
        if not target.is_file():
            raise _TargetError("entry_missing", "the entry point is not a file")

    def _check_allowlist(
        self, kind: str, command: Sequence[str], path_value: str
    ) -> None:
        """The command must be exactly one of the shapes this module may start."""

        parts = tuple(str(part) for part in command)
        python = python_executable()
        node = node_executable()
        ok = False
        if kind == ENTRY_KIND_PYTHON_SCRIPT:
            ok = len(parts) == 2 and parts[0] == python and parts[1] == path_value
        elif kind == ENTRY_KIND_PYTHON_MODULE:
            ok = (
                len(parts) == 3
                and parts[0] == python
                and parts[1] == "-m"
                and parts[2] == path_value
            )
        elif kind == ENTRY_KIND_NODE_SCRIPT:
            ok = bool(node) and len(parts) == 2 and parts[0] == node and parts[1] == path_value
        elif kind == ENTRY_KIND_NODE_NPM:
            ok = self._npm_shape_ok(parts)
        if not ok:
            raise _TargetError(
                "command_not_allowed",
                "the command is not in the allowlist for this entry kind",
            )

    @staticmethod
    def _npm_shape_ok(parts: Sequence[str]) -> bool:
        if len(parts) != 4 or parts[2] != "run" or parts[3] not in ("start", "dev"):
            return False
        node = node_executable()
        if not node or parts[0] != node:
            return False
        cli = str(parts[1]).replace("\\", "/")
        return os.path.isabs(str(parts[1])) and cli.endswith("/npm/bin/npm-cli.js")

    def _consume_approval(self, project_id: str, token: object) -> Optional[Tuple[str, bool]]:
        """Consume one approval, returning ``(digest, offline)`` or ``None``."""

        if not isinstance(token, str) or not token:
            return None
        with self._lock:
            record = self._approvals.pop(token, None)
        if record is None:
            return None
        approved_project, digest, issued, offline = record
        if approved_project != project_id:
            return None
        if time.monotonic() - issued > APPROVAL_TTL_SECONDS:
            return None
        return digest, bool(offline)

    # -------------------------------------------------------------- authority
    def _authorize(
        self,
        project_id: str,
        workspace: Path,
        kind: str,
        command: Sequence[str],
        digest: str,
    ) -> Tuple[Optional[AuthorizedExecution], Any]:
        """Pass the existing execution chain and return its token, or the denial."""

        argv = tuple(str(part) for part in command)
        # One run id per (project, exact command, live workspace identity), so one
        # project has one **frozen** scope per command it has been approved to run in
        # this workspace. A changed command is a different scope, and so is a replaced
        # workspace directory -- both are the truth of what was authorized. The same
        # command in the same directory reuses the scope it was frozen with, which is
        # why nothing ever has to release one: no production code may clear the freeze
        # registry (see ``tests/test_run_scope_trust_boundary.py``).
        request_run_id = (
            f"project-run-{project_id}-{str(digest or '')[:12] or 'default'}"
            f"-{_workspace_tag(workspace)}"
        )
        language = "python" if kind.startswith("python") else "node"
        profile = ProjectExecutionProfile(
            profile_id=f"project-local-run:{project_id}",
            environment_type=ExecutionEnvironmentType.HOST,
            runtime_name=language,
            working_directory=".",
            allowed_commands=(CommandIdentity(executable=argv[0], argv=argv[1:]),),
            timeout_seconds=self._timeout,
            max_output_bytes=MAX_LOG_BYTES,
            # Honest: this child inherits the machine's network. There is no
            # network isolation here, and the payload says ``inherited`` too.
            network_access=True,
        )
        try:
            scope = RunScope(
                run_id=request_run_id,
                workspace=Workspace(root=workspace),
                execution_profile=profile,
                allowed_tool_ids=frozenset(),
                allowed_execution_commands=frozenset(
                    {CommandIdentity(executable=argv[0], argv=argv[1:])}
                ),
                acceptance_criteria=(
                    AcceptanceCriterion(
                        criterion_id="local-run-authorized",
                        description="the declared entry point is authorized to run locally",
                    ),
                ),
            )
            scope.freeze()
            require_active_scope(request_run_id, scope)
        except Exception as exc:  # noqa: BLE001 - a scope refusal is a refusal
            return None, _denial(f"run_scope_refused: {exc}")

        request = ExecutionRequest(
            command=argv,
            working_directory=".",
            timeout_seconds=self._timeout,
            profile=profile,
            approval_required=True,
            metadata={"request_id": request_run_id, "run_id": request_run_id},
        )
        try:
            fingerprint = IntentBuilder.from_request(request, profile).build().fingerprint
        except Exception as exc:  # noqa: BLE001 - an unbuildable intent is a refusal
            return None, _denial(f"intent_unavailable: {exc}")

        resolver = _FixedApprovalResolver(fingerprint)
        try:
            authorized, denial = self._coordinator.authorize(
                request,
                workspace_root=canonical_root(workspace),
                run_id=request_run_id,
                allowed_commands=scope.allowed_execution_commands,
                approval_policy=ApprovalPolicy(),
                approval_resolver=resolver,
                run_scope=scope,
            )
        except Exception as exc:  # noqa: BLE001 - the chain never raises into a route
            return None, _denial(f"authorization_error: {exc}")
        return authorized, denial

    # ------------------------------------------------------------------- spawn
    def _spawn(
        self,
        facts: _RunFacts,
        authorized: AuthorizedExecution,
        workspace: Path,
    ) -> Dict[str, Any]:
        """Create the child inside the token's workspace and start supervising it.

        Containment is layered around one ``Popen`` and every layer is recorded as
        the fact it is:

        1. the project's own folder is prepared for a low-integrity child;
        2. a probe process, under the *same* containment, measures what the child
           could write (its own folder, Forge's store root, a record there);
        3. a job object with limits is created and the child is assigned to it;
        4. the child is created suspended, its token is lowered to low integrity and
           read back, and only then resumed.

        If a step cannot be taken, the run still happens -- with the fact recorded as
        not taken. That is the difference between containment and a claim of it.
        """

        intent = authorized.intent
        command = (intent.resolved_executable or intent.executable,) + intent.argv
        cwd = canonical_root(workspace)
        boundary = WorkspaceBoundary.for_workspace(Workspace(root=cwd))
        if not boundary.contains(cwd) or not cwd.is_dir():
            return self._refuse(
                facts,
                "workspace_unavailable",
                "the project workspace is no longer inside the authorized root",
            )

        limits = SandboxLimits(
            memory_mb=self._memory_mb, max_processes=self._max_processes
        ).sanitized()
        sandbox = facts.sandbox
        sandbox.network = NETWORK_INHERITED
        sandbox.server_lifetime_seconds = self._server_lifetime
        environment = self._minimal_environment()
        drop_integrity = False
        if not self._sandbox_enabled:
            sandbox.disabled = True
            sandbox.notes.append("containment is disabled for this runner")
        elif os.name != "nt":
            sandbox.notes.append(
                "OS-level containment (job object, low-integrity token) is implemented "
                "for Windows only; nothing is claimed on this platform"
            )
        else:
            prepared = prepare_low_integrity_workspace(cwd)
            sandbox.workspace_label = str(prepared.get("label", "unknown"))
            sandbox.workspace_label_applied = bool(prepared.get("applied"))
            drop_integrity = bool(prepared.get("low"))
            if not drop_integrity:
                sandbox.notes.append(
                    "the project folder is not writable at low integrity, so the "
                    "token was left unchanged"
                )
            else:
                # Python and most build tools want a writable TEMP. The machine's TEMP
                # is a medium-integrity folder a low child cannot write, so the
                # child's TEMP points inside its own folder -- the one place it may
                # write anyway.
                temp_dir = cwd / ".forge-tmp"
                try:
                    temp_dir.mkdir(parents=True, exist_ok=True)
                    environment["TEMP"] = str(temp_dir)
                    environment["TMP"] = str(temp_dir)
                except OSError:
                    sandbox.notes.append("no writable TEMP inside the project folder")
            probe = probe_write_boundary(
                interpreter=python_executable(),
                cwd=cwd,
                environment=environment,
                workspace=cwd,
                outside_directory=self._store_root(),
                limits=limits,
                drop_integrity=drop_integrity,
            )
            sandbox.writes_inside = str(probe.get("writes_inside", "unknown"))
            sandbox.writes_outside = str(probe.get("writes_outside", "unknown"))
            sandbox.probe_error = str(probe.get("probe_error", "") or "")

        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        if os.name == "nt":
            # No console window: the project runs, but the owner never sees a console.
            creationflags |= _CREATE_NO_WINDOW
        if drop_integrity:
            creationflags |= _CREATE_SUSPENDED

        def spawn(flags: int) -> "subprocess.Popen[bytes]":
            return subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=flags,
                start_new_session=(os.name != "nt"),
            )

        job: Optional[int] = None
        limits_report: Optional[Dict[str, Any]] = None
        if self._sandbox_enabled:
            job = create_process_tree_job()
            if job is not None:
                limits_report = set_process_tree_job_limits(
                    job, memory_mb=limits.memory_mb, max_processes=limits.max_processes
                )
                if limits_report is None:
                    sandbox.notes.append("the job object accepted no limits")
                    close_process_tree_job(job)
                    job = None

        try:
            process = spawn(creationflags)
        except FileNotFoundError:
            if job is not None:
                close_process_tree_job(job)
            return self._refuse(
                facts,
                "executable_missing",
                "the declared interpreter is not available on this machine",
            )
        except Exception as exc:  # noqa: BLE001 - a spawn failure is a refusal
            if job is not None:
                close_process_tree_job(job)
            return self._refuse(
                facts, "spawn_failed", f"the process could not be started: {type(exc).__name__}"
            )

        if job is not None and assign_process_tree_job(job, process):
            sandbox.job_object = True
            if limits_report:
                sandbox.kill_on_close = bool(limits_report.get("kill_on_job_close"))
                sandbox.process_memory_limit_mb = int(
                    limits_report.get("process_memory_limit_mb") or 0
                )
                sandbox.active_process_limit = int(
                    limits_report.get("active_process_limit") or 0
                )
                sandbox.memory_mb = sandbox.process_memory_limit_mb
                sandbox.max_processes = (
                    sandbox.active_process_limit if sandbox.active_process_limit else 0
                )
        elif job is not None:
            close_process_tree_job(job)
            job = None
            sandbox.notes.append("the child could not be assigned to a job object")

        if drop_integrity:
            before = process_integrity_level(process.pid)
            applied, level = lower_process_integrity(process)
            after = process_integrity_level(process.pid)
            sandbox.integrity_level_before = before
            sandbox.integrity_level = after if after != "unknown" else level
            if not applied:
                sandbox.notes.append(
                    "the child's token could not be lowered to low integrity; the "
                    "measured write boundary describes the token it really has"
                )
            resumed = resume_process(process)
            if not resumed:
                # A suspended child that cannot be resumed must not be left hanging.
                # It is killed and started again without the token layer, and the
                # report says so instead of keeping a claim it did not deliver.
                sandbox.probe_error = "resume_failed"
                sandbox.writes_inside = "unknown"
                sandbox.writes_outside = "unknown"
                sandbox.notes.append("the child could not be resumed; restarted unchanged")
                terminate_process_tree(process, job=job, exit_code=1)
                if job is not None:
                    close_process_tree_job(job)
                    job = None
                    sandbox.job_object = False
                drop_integrity = False
                sandbox.integrity_level_before = "unknown"
                sandbox.integrity_level = "unknown"
                try:
                    retry_flags = (
                        subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                    )
                    if os.name == "nt":
                        retry_flags |= _CREATE_NO_WINDOW
                    process = spawn(retry_flags)
                except Exception as exc:  # noqa: BLE001 - a second spawn failure
                    return self._refuse(
                        facts,
                        "spawn_failed",
                        f"the process could not be started: {type(exc).__name__}",
                    )
        if not drop_integrity:
            sandbox.integrity_level = process_integrity_level(process.pid)
            sandbox.integrity_level_before = sandbox.integrity_level
        # The token is restricted when the child really runs below the owner's normal
        # level. Whether Forge applied the change or the child inherited it (a
        # low-labelled interpreter) is stated in ``detail.mechanism``.
        if sandbox.integrity_level == "low":
            sandbox.restricted_token = True
            sandbox.integrity = INTEGRITY_RESTRICTED
            if not sandbox.mechanism:
                sandbox.mechanism = (
                    "low_integrity_token" if drop_integrity else "inherited_low_integrity"
                )
        elif sandbox.integrity_level != "unknown":
            sandbox.integrity = INTEGRITY_UNCHANGED
            sandbox.mechanism = sandbox.mechanism or "unchanged"
        if sandbox.writes_outside == "allowed" and sandbox.integrity_level == "low":
            # The measured outcome, explained without upgrading it: the child really
            # is low integrity, and it still reached Forge's data because that data
            # (and the Forge process that wrote it) is low-integrity too. Integrity
            # levels only separate a lower child from higher objects.
            sandbox.notes.append(
                "Forge's own files are reachable from this child: the Forge process "
                "runs at the same low integrity level as the child, so its records "
                "carry no higher label to protect them"
            )

        now = _utc_now()
        with self._lock:
            facts.state = STARTING
            facts.pid = int(process.pid)
            facts.started_at = now
            facts.updated_at = now
            facts.finished_at = 0.0
            facts.exit_code = None
            facts.port = 0
            facts.observed_port = 0
            facts.reused = False
            facts.url = ""
            facts.served = False
            facts.reason = ""
            facts.next_step = ""
            facts.blocked_code = ""
            facts.stop_requested = False
            facts.process = process
            facts.job = job
            facts.log_path = self._log_path(facts.project_id)
            facts.stdout_log = _StreamLog()
            facts.stderr_log = _StreamLog()
            facts.last_event = "process started"
            facts.diagnostics["argv"] = [str(part) for part in command]
            facts.diagnostics["cwd"] = str(cwd)
            facts.diagnostics["job_object"] = job is not None
            facts.diagnostics["environment_keys"] = sorted(environment)
            facts.diagnostics["sandbox"] = sandbox.to_payload()
            self._runs[facts.project_id] = facts

        self._ensure_atexit()
        self._start_readers(facts, process)
        supervisor = threading.Thread(
            target=self._supervise,
            args=(facts, process, job),
            name=f"forge-run-{facts.project_id}",
            daemon=True,
        )
        supervisor.start()
        self._persist(facts)
        return self._payload(facts, include_log=True)

    def _store_root(self) -> Path:
        """The directory that holds this project's records (Forge's own data)."""

        try:
            return Path(getattr(self._store, "root", default_projects_root()))
        except (TypeError, ValueError):
            return default_projects_root()

    def _minimal_environment(self) -> Dict[str, str]:
        """Six variables: PATH, SYSTEMROOT, TEMP, PYTHONIOENCODING, PYTHONUNBUFFERED,
        FORGE_SANDBOX.

        Everything else -- a Forge credential, a provider key, a value read from
        ``.env`` -- is *absent* rather than blanked, so a child cannot read it back.
        ``FORGE_SANDBOX=1`` is a marker a program may read; it is not a boundary and
        the payload never treats it as one. ``PYTHONUNBUFFERED=1`` matters for honesty,
        not speed: stdout is a pipe here, so a project's startup prints (including a
        console prompt) would otherwise sit in Python's block buffer and never reach
        the log the owner is pointed at.
        """

        environment: Dict[str, str] = {}
        for name in ("PATH", "SYSTEMROOT", "TEMP"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        environment[SANDBOX_ENV_VAR] = "1"
        return environment

    def _start_readers(self, facts: _RunFacts, process: subprocess.Popen) -> None:
        def pump(stream: Any, log: _StreamLog) -> None:
            if stream is None:
                return
            # ``read1`` returns whatever is available instead of blocking until the
            # requested size is filled: a server that prints one startup line and
            # then serves would otherwise show no output at all until it exits.
            read = getattr(stream, "read1", None) or stream.read
            try:
                for chunk in iter(lambda: read(65536), b""):
                    log.feed(chunk)
                    self._flush(facts, log)
            except Exception:  # noqa: BLE001 - a killed pipe is an expected outcome
                pass
            finally:
                log.close()
                self._flush(facts, log)

        for stream, log in ((process.stdout, facts.stdout_log), (process.stderr, facts.stderr_log)):
            thread = threading.Thread(target=pump, args=(stream, log), daemon=True)
            thread.start()
            facts.readers.append(thread)

    def _supervise(self, facts: _RunFacts, process: subprocess.Popen, job: Optional[int]) -> None:
        """Follow the child: exit, timeout, and a bound localhost port."""

        started = time.monotonic()
        startup_deadline = started + self._startup_timeout
        hard_deadline = started + self._timeout
        next_probe = started + 0.1
        while True:
            code = process.poll()
            if code is not None:
                self._finish(facts, process, code)
                return
            now = time.monotonic()
            if facts.port == 0 and now >= next_probe:
                next_probe = now + 0.25
                # Two independent sources, one verdict: the ports the child's own output
                # names (first match first) and the sockets its pid tree owns. Every
                # candidate is proven with a real ``GET /`` before it is called ready.
                port = self._discover_port(process.pid, self._output_ports(facts))
                if port:
                    self._publish_preview(facts, port)
            if self._server_lifetime and facts.port and now - started > self._server_lifetime:
                # A *serving* process has its own bound, configured by the owner and
                # off by default. When it is reached the whole job is terminated, the
                # same way a stop is.
                with self._lock:
                    facts.state = STOPPING
                    facts.reason = (
                        f"the server exceeded its {self._server_lifetime:g} s lifetime"
                    )
                self._finish(facts, process, None, timed_out=True)
                return
            if facts.port == 0 and now > hard_deadline:
                # A script that is meant to finish did not. A server that already
                # bound a port is never killed by this deadline: for it the
                # timeout only answered "did it start".
                with self._lock:
                    facts.state = STOPPING
                    facts.reason = (
                        f"the process did not finish within {self._timeout:g} s"
                    )
                self._finish(facts, process, None, timed_out=True)
                return
            if facts.port == 0 and now > startup_deadline:
                with self._lock:
                    if facts.state == STARTING:
                        # Alive and not serving: it is a long-running one-shot, and
                        # the run timeout above now bounds it. The state says it is
                        # running, because it is.
                        changed = facts.state == STARTING
                        facts.state = RUNNING
                        if changed:
                            facts.last_event = "running (no local port bound)"
                            facts.updated_at = _utc_now()
                time.sleep(0.05)
                continue
            time.sleep(0.05)

    def _finish(
        self,
        facts: _RunFacts,
        process: subprocess.Popen,
        code: Optional[int],
        *,
        timed_out: bool = False,
    ) -> None:
        """Reap the process tree and record the end, unless a stop already did."""

        with self._lock:
            job = facts.job
            facts.job = None
        if job is not None or process.poll() is None:
            # Reaping is attempted unconditionally: a child that spawns a helper and
            # returns would otherwise leave work running on the host. The kill goes
            # through the verified pid, its descendants and its job -- never a name.
            # A job kill reports a non-zero code, so a terminated child is never
            # mistaken for a clean ``exit 0``.
            try:
                terminate_process_tree(process, job=job, exit_code=1)
            except Exception:  # noqa: BLE001 - a gone tree is the goal
                pass
        if job is not None:
            close_process_tree_job(job)
        for thread in facts.readers:
            try:
                thread.join(timeout=1.0)
            except RuntimeError:  # pragma: no cover - joining from its own thread
                pass
        if code is None and not timed_out:
            code = process.poll()
        # The "already running" case, decided before the URL is cleared: a child that
        # exits 0 while an address it printed still answers is a *success* -- the
        # server the owner wants is up, it just belongs to another instance of the
        # project (or outlived the child). The child is dead at this point and its tree
        # was just reaped, so an answer cannot be the child's own process. Only a real
        # ``GET /`` may say so, and only a clean exit qualifies: a crash that happens
        # to name a busy port is not evidence of a running application.
        reused_port = 0
        if not timed_out and not facts.stop_requested and code == 0:
            reused_port = self._answering_output_port(facts)
        with self._lock:
            # A URL is only ever true while the process is up: the port it bound is
            # remembered as a fact, but nobody can open it after the process ended.
            # The one exception is proven above: the address still answers.
            facts.url = ""
            if timed_out:
                # A killed process has no meaningful exit code. Reporting the code a
                # terminated child happened to leave would invite "exit 0" to be
                # read as success.
                code = None
            if reused_port:
                # Written before the state on purpose: a concurrent status read sees
                # ``state`` before ``url``, so a terminal state that carries a reused
                # address is never observed with an empty URL.
                facts.reused = True
                facts.observed_port = int(reused_port)
                facts.port = int(reused_port)
                facts.served = True
                facts.url = loopback_url(reused_port)
            if facts.state == STOPPING and facts.stop_requested:
                facts.state = STOPPED
                facts.exit_code = code
                facts.finished_at = _utc_now()
                facts.updated_at = facts.finished_at
                facts.last_event = "stopped"
            elif facts.state not in (STOPPED, TIMED_OUT, REFUSED, RUN_BLOCKED):
                facts.exit_code = code
                facts.finished_at = _utc_now()
                facts.updated_at = facts.finished_at
                if timed_out:
                    facts.state = TIMED_OUT
                    facts.last_event = "timed out and killed"
                elif code == 0:
                    facts.state = SUCCEEDED
                    facts.last_event = (
                        f"already running on 127.0.0.1:{reused_port}"
                        if reused_port
                        else "finished with exit code 0"
                    )
                elif self._missing_dependency(facts):
                    facts.state = RUN_BLOCKED
                    facts.blocked_code = "dependencies_missing"
                    facts.reason = REASON_DEPENDENCIES
                    facts.next_step = NEXT_STEP_DEPENDENCIES
                    facts.last_event = "blocked: dependencies are not installed"
                else:
                    facts.state = FAILED
                    facts.last_event = f"failed with exit code {code}"
        self._close_streams(process)
        self._unregister_preview(facts)
        self._persist(facts)

    @staticmethod
    def _close_streams(process: subprocess.Popen) -> None:
        for name in ("stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001 - a descendant may hold the pipe
                    pass

    def _missing_dependency(self, facts: _RunFacts) -> bool:
        """Whether the failure looks like a dependency this runner must not install."""

        tail = "\n".join(
            list(facts.stderr_log.snapshot()[0])[-20:]
            + list(facts.stdout_log.snapshot()[0])[-20:]
        ).lower()
        if not any(marker in tail for marker in _MISSING_DEPENDENCY_MARKERS):
            return False
        return self._declares_dependencies(facts)

    def _declares_dependencies(self, facts: _RunFacts) -> bool:
        try:
            root = Path(str(facts.diagnostics.get("cwd") or ""))
        except (TypeError, ValueError):
            return False
        if not root.is_dir():
            return False
        requirements = root / "requirements.txt"
        try:
            if requirements.is_file() and requirements.read_text(
                encoding="utf-8", errors="replace"
            ).strip():
                return True
        except OSError:
            pass
        package = root / "package.json"
        try:
            if package.is_file():
                document = json.loads(package.read_text(encoding="utf-8"))
                dependencies = document.get("dependencies") if isinstance(document, dict) else None
                if isinstance(dependencies, dict) and dependencies:
                    return True
        except (OSError, ValueError):
            pass
        return False

    # ------------------------------------------------------------------- ports
    def _output_ports(self, facts: _RunFacts) -> Tuple[int, ...]:
        """The ports the child's own output names, in the order it printed them.

        Both streams are read: a runtime prints its address on stdout, but a server
        that started through a wrapper may announce it on stderr. The parsing rules
        live in :mod:`app.projects.ports`; this method only feeds them.
        """

        stdout_lines, _stdout_truncated = facts.stdout_log.snapshot()
        stderr_lines, _stderr_truncated = facts.stderr_log.snapshot()
        return parse_banner_ports(tuple(stdout_lines) + tuple(stderr_lines))

    def _discover_port(
        self,
        root_pid: Optional[int],
        candidates: Sequence[int] = (),
    ) -> int:
        """The first port that **really answers** among the child's claims and sockets.

        ``candidates`` are the ports the child's own output announced; the sockets the
        child (and its descendants) listen on are appended after them, because the
        address a program prints is the one it chose. Each candidate is verified with
        a real ``GET /`` (:meth:`app.projects.ports.first_answering`): a bound socket
        that never answers is not a server, and a printed address nobody answers on is
        not one either. ``0`` means "nothing answered", never "there is no port".
        """

        socket_ports: Tuple[int, ...] = ()
        if isinstance(root_pid, int) and root_pid > 0:
            pids = {root_pid}
            if os.name == "nt":
                pids.update(process_tree_pids(root_pid))
            try:
                socket_ports = listening_ports(pids)
            except Exception:  # noqa: BLE001 - an unreadable table is "unknown"
                socket_ports = ()
        ordered = _ordered_ports(candidates, socket_ports)
        if not ordered:
            return 0
        port, _status = first_answering(ordered)
        return int(port)

    def _answering_output_port(self, facts: _RunFacts) -> int:
        """The port the child's own output named that still answers after it exited.

        The sockets of a dead pid say nothing, so only the announced addresses are
        left. An answer here means a server that this run did not start is up -- the
        "already running" case.
        """

        candidates = self._output_ports(facts)
        if not candidates:
            return 0
        port, _status = first_answering(candidates)
        return int(port)

    def _publish_preview(self, facts: _RunFacts, port: int) -> None:
        url = ""
        if self._previews is not None:
            try:
                session = self._previews.register_process(
                    facts.project_id,
                    port=port,
                    entry_path=facts.entry_path,
                    entry_kind=facts.entry_kind,
                )
                url = str((session or {}).get("url", "") or "")
            except Exception:  # noqa: BLE001 - a preview registration is not the run
                url = ""
        with self._lock:
            facts.port = int(port)
            facts.observed_port = int(port)
            facts.served = True
            facts.state = RUNNING
            facts.url = url or loopback_url(port)
            facts.last_event = f"listening on 127.0.0.1:{int(port)}"
            facts.updated_at = _utc_now()
        self._persist(facts)

    def _unregister_preview(self, facts: _RunFacts) -> None:
        if self._previews is None:
            return
        try:
            self._previews.unregister_process(facts.project_id)
        except Exception:  # noqa: BLE001 - a preview is not the run
            pass

    # -------------------------------------------------------------- persistence
    def _log_path(self, project_id: str) -> Optional[Path]:
        try:
            root = Path(getattr(self._store, "root", default_projects_root()))
        except (TypeError, ValueError):
            return None
        if not is_safe_project_id(project_id):
            return None
        return root / project_id / "run.log"

    def _log_path_relative(self, project_id: str) -> str:
        try:
            root = Path(getattr(self._store, "root", default_projects_root()))
        except (TypeError, ValueError):
            return ""
        folder = root / project_id / "run.log"
        try:
            return folder.resolve().relative_to(repository_root()).as_posix()
        except (ValueError, OSError):
            return f".forge/projects/{project_id}/run.log"

    def _persist(self, facts: _RunFacts) -> None:
        """Write the bounded, redacted log beside the project record."""

        path = facts.log_path or self._log_path(facts.project_id)
        if path is None:
            return
        facts.log_path = path
        body = self._log_body(facts)
        facts.last_persist = time.monotonic()
        # Two writers can be here at once (a reader thread flushing while the
        # supervisor records a state change), so the temporary file is unique and
        # the replace is serialised: an interleaved pair would otherwise let one
        # writer replace the other's half-written file.
        with self._persist_lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                descriptor, temp_name = tempfile.mkstemp(
                    dir=str(path.parent), prefix=".run-", suffix=".tmp"
                )
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                        handle.write(body)
                except BaseException:
                    try:
                        os.unlink(temp_name)
                    except OSError:
                        pass
                    raise
                # Windows refuses to replace a file a reader currently holds open; the
                # store already tolerates that with a short bounded retry, so the log
                # keeps the same tolerance instead of silently losing a flush.
                delay = 0.02
                for attempt in range(6):
                    try:
                        os.replace(temp_name, path)
                        break
                    except PermissionError:
                        if attempt == 5:
                            raise
                        time.sleep(delay)
            except OSError:
                return

    def _log_body(self, facts: _RunFacts) -> str:
        header = [
            "# Forge local run log (redacted, bounded)",
            f"# project_id: {facts.project_id}",
            f"# state: {facts.state}",
            f"# entry: {facts.entry_kind} {facts.entry_path}",
            f"# command: {facts.command_display}",
            f"# network_policy: {facts.network_policy}",
            (
                "# sandbox: job_object=%s integrity=%s writes_outside=%s network=%s"
                % (
                    "on" if facts.sandbox.job_object else "off",
                    facts.sandbox.integrity,
                    facts.sandbox.writes_outside,
                    facts.sandbox.network,
                )
            ),
        ]
        if facts.reason:
            header.append(f"# reason: {redact_output_text(facts.reason)}")
        if facts.next_step:
            header.append(f"# next_step: {redact_output_text(facts.next_step)}")
        header.append("")
        stdout_text = facts.stdout_log.text()
        stderr_text = facts.stderr_log.text()
        return "\n".join(
            header
            + ["--- stdout ---", stdout_text]
            + ["--- stderr ---", stderr_text]
            + [""]
        )

    def _ensure_atexit(self) -> None:
        """Register one shutdown hook, once, so a started child is not orphaned."""

        if self._atexit_registered:
            return
        try:
            atexit.register(self.stop_all)
            self._atexit_registered = True
        except Exception:  # noqa: BLE001 - a missing hook must not block a run
            pass

    def _flush(self, facts: _RunFacts, _log: _StreamLog) -> None:
        """Persist the log at most once a second while output keeps arriving.

        A long-running server keeps writing to stdout, and a log file that only
        changed on a state transition would keep showing the first line.
        """

        now = time.monotonic()
        if now - facts.last_persist < 1.0:
            return
        facts.last_persist = now
        self._persist(facts)

    # ------------------------------------------------------------------ payload
    def _refuse(self, facts: _RunFacts, code: str, message: str) -> Dict[str, Any]:
        with self._lock:
            facts.state = REFUSED
            facts.reason = message
            facts.blocked_code = code
            facts.updated_at = _utc_now()
            facts.finished_at = facts.updated_at
            facts.last_event = f"refused: {code}"
            self._runs[facts.project_id] = facts
        self._persist(facts)
        return self._payload(facts, include_log=True)

    def _block(
        self, facts: _RunFacts, reason: str, next_step: str, *, code: str
    ) -> Dict[str, Any]:
        with self._lock:
            facts.state = RUN_BLOCKED
            facts.reason = reason
            facts.next_step = next_step
            facts.blocked_code = code
            facts.updated_at = _utc_now()
            facts.finished_at = facts.updated_at
            facts.last_event = f"blocked: {code}"
            self._runs[facts.project_id] = facts
        self._persist(facts)
        return self._payload(facts, include_log=True)

    def _payload(self, facts: _RunFacts, *, include_log: bool = False) -> Dict[str, Any]:
        stdout_lines, stdout_truncated = facts.stdout_log.snapshot()
        stderr_lines, stderr_truncated = facts.stderr_log.snapshot()
        lines = list(stdout_lines) + ["--- stderr ---"] + list(stderr_lines)
        if len(lines) > MAX_REPORTED_LINES:
            lines = lines[-MAX_REPORTED_LINES:]
            truncated = True
        else:
            truncated = bool(stdout_truncated or stderr_truncated)
        relative = self._log_path_relative(facts.project_id)
        payload: Dict[str, Any] = {
            "project_id": facts.project_id,
            "state": facts.state,
            "running": facts.alive,
            # A port is a fact of the last process, not a promise about a dead one: a
            # finished run must never claim it is still serving.
            "serving": bool(facts.port) and bool(facts.alive),
            "reason": facts.reason,
            "next_step": facts.next_step,
            "blocked_code": facts.blocked_code,
            "last_event": facts.last_event,
            "entry_kind": facts.entry_kind,
            "entry_path": facts.entry_path,
            "language": _language_of(facts.entry_kind),
            "command": facts.command_display,
            "executable": facts.executable_display,
            "arguments": list(facts.arguments),
            "entrypoint_digest": facts.digest,
            "workspace": facts.project_path,
            "pid": facts.pid,
            "exit_code": facts.exit_code,
            "port": facts.port,
            # The port a real ``GET /`` answered on, and whether that server was
            # already running before this launch. Both are observations, published so
            # the launch can open the address it really verified.
            "observed_port": facts.observed_port,
            "reused": bool(facts.reused),
            "url": facts.url,
            "started_at": _iso(facts.started_at),
            "finished_at": _iso(facts.finished_at),
            "updated_at": _iso(facts.updated_at),
            "network_policy": facts.network_policy,
            "offline": bool(facts.offline),
            "approval_required": True,
            "sandbox": facts.sandbox.to_payload(),
            "log": {
                "path": relative,
                "relative_path": relative,
                "readable": True,
                "truncated": truncated,
                "count": len(lines),
                "lines": lines if include_log else [],
                "max_bytes": MAX_LOG_BYTES,
            },
            "diagnostics": dict(facts.diagnostics),
        }
        return payload


# --------------------------------------------------------------------------- #
# small value types used above
# --------------------------------------------------------------------------- #


class _TargetError(Exception):
    """A target fact that makes the run impossible, with the word for it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _Denial:
    """A refusal produced before the coordinator could return one."""

    def __init__(self, message: str) -> None:
        self.stderr = message
        self.metadata: Dict[str, Any] = {"denial_reason": "authorization_error"}


def _denial(message: str) -> _Denial:
    return _Denial(message)


class _FixedApprovalResolver:
    """An approval resolver bound to one exact intent fingerprint.

    The fingerprint is computed from the command **this runner is about to run**,
    after the owner's approval named the same digest. The coordinator re-computes
    the fingerprint itself and refuses when the two disagree, so this resolver
    cannot approve a different invocation than the one authorized.
    """

    def __init__(self, fingerprint: str) -> None:
        self._fingerprint = str(fingerprint or "")
        self._used = False

    def resolve(self, request: ApprovalRequest) -> Optional[ApprovalResolution]:
        if self._used:
            return None
        if not self._fingerprint or request.intent_fingerprint != self._fingerprint:
            return None
        self._used = True
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=self._fingerprint,
            approval_id="local-run",
        )


def _workspace_tag(workspace: Path) -> str:
    """A short, stable tag for one live workspace directory identity.

    The scope a run is authorized against is bound to the *directory*, not just its
    path: a workspace that was deleted and recreated at the same path is a different
    place to run code in. Hashing the captured identity keeps the run id deterministic
    (so a repeated start reuses the frozen scope instead of needing a release) while
    still telling two different directories apart.
    """

    try:
        identity = WorkspaceIdentity.capture(Path(workspace))
        material = f"{identity.canonical_root}|{identity.device}|{identity.inode}"
    except Exception:  # noqa: BLE001 - an uncapturable identity is already refused later
        material = str(workspace)
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:12]


def _language_of(kind: str) -> str:
    if kind == ENTRY_KIND_STATIC_HTML:
        return "HTML"
    if kind in (ENTRY_KIND_PYTHON_SCRIPT, ENTRY_KIND_PYTHON_MODULE):
        return "Python"
    if kind in (ENTRY_KIND_NODE_SCRIPT, ENTRY_KIND_NODE_NPM):
        return "Node.js"
    return ""


# --------------------------------------------------------------------------- #
# port discovery
# --------------------------------------------------------------------------- #


def _ordered_ports(
    candidates: Sequence[int], socket_ports: Sequence[int]
) -> Tuple[int, ...]:
    """The announced ports first (in the child's own order), then the bound sockets.

    A program's printed address is the port it chose; its sockets confirm it. Both are
    candidates, both are verified, and neither is trusted alone. Duplicates collapse
    so a port announced *and* bound is probed once.
    """

    ordered: list[int] = []
    for value in tuple(candidates or ()) + tuple(socket_ports or ()):
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 65535 and number not in ordered:
            ordered.append(number)
    return tuple(ordered)


def _netstat_path() -> str:
    root = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot") or r"C:\Windows"
    candidate = os.path.join(root, "System32", "netstat.exe")
    if os.path.isfile(candidate):
        return candidate
    return "netstat"


def _windows_listening_ports(pids: Sequence[int]) -> Tuple[int, ...]:
    """Listeners owned by ``pids``, read from ``netstat -ano``."""

    wanted = {int(pid) for pid in pids if int(pid) > 0}
    if not wanted:
        return ()
    try:
        completed = subprocess.run(
            [_netstat_path(), "-ano", "-p", "TCP"],
            capture_output=True,
            timeout=10,
            check=False,
            # A helper the owner never asked to see: no console window, ever.
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001 - an unreadable table is "unknown", not a port
        return ()
    text = (completed.stdout or b"").decode("utf-8", errors="replace")
    ports = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if "LISTENING" not in line.upper():
            continue
        try:
            owner = int(parts[-1])
        except ValueError:
            continue
        if owner not in wanted:
            continue
        local = parts[1]
        try:
            port = int(local.rsplit(":", 1)[-1])
        except ValueError:
            continue
        if port > 0:
            ports.add(port)
    return tuple(sorted(ports))


def _posix_listening_ports(pids: Sequence[int]) -> Tuple[int, ...]:
    """Listeners owned by ``pids``, read from ``/proc`` when it is available."""

    wanted = {int(pid) for pid in pids if int(pid) > 0}
    if not wanted or not os.path.isdir("/proc"):
        return ()
    inodes = set()
    for pid in wanted:
        fd_dir = f"/proc/{pid}/fd"
        try:
            for name in os.listdir(fd_dir):
                try:
                    link = os.readlink(os.path.join(fd_dir, name))
                except OSError:
                    continue
                if link.startswith("socket:["):
                    inodes.add(link[len("socket:[") : -1])
        except OSError:
            continue
    if not inodes:
        return ()
    ports = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table, "r", encoding="utf-8", errors="replace") as handle:
                next(handle, None)
                for line in handle:
                    parts = line.split()
                    if len(parts) < 10 or parts[3] != "0A":
                        continue
                    if parts[9] not in inodes:
                        continue
                    try:
                        ports.add(int(parts[1].rsplit(":", 1)[-1], 16))
                    except ValueError:
                        continue
        except OSError:
            continue
    return tuple(sorted(port for port in ports if port > 0))


def listening_ports(pids: Sequence[int]) -> Tuple[int, ...]:
    """TCP ports on which one of ``pids`` is listening, best effort.

    Windows: ``netstat -ano``. Linux: ``/proc/net/tcp`` joined with the process's
    socket inodes. Anywhere else, an empty answer -- which the caller reads as "no
    port was found", never as "no port exists".
    """

    if os.name == "nt":
        return _windows_listening_ports(pids)
    return _posix_listening_ports(pids)
