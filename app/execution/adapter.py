"""Deterministic local execution backend using safe subprocess execution in ephemeral workspaces."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import ctypes
from ctypes import wintypes
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.execution.intent import AuthorizedExecution, ExecutionIntent
from app.execution.paths import is_bare_executable
from app.execution.policy import ExecutionPolicy
from app.execution.redaction import (
    _SENSITIVE_KEY_PATTERN,
    DefaultSecretRedactor,
    SecretRedactor,
    looks_like_credential,
)
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionResult,
    ExecutionStatus,
)
from app.execution.workspace_manager import EphemeralWorkspaceManager
from app.execution.sandbox import (
    RESIDUAL_RISK,
    WorkspaceBoundary,
    WorkspaceContainmentError,
    canonical_root,
)


class ExecutionAuthorizationError(Exception):
    """Raised when LocalExecutionAdapter is invoked without proper authorization."""


SAFE_ENV_WHITELIST_KEYS: frozenset[str] = frozenset({
    # Windows system environment
    "ALLUSERSPROFILE",
    "APPDATA",
    "COMMONPROGRAMFILES",
    "COMMONPROGRAMFILES(X86)",
    "COMMONPROGRAMW6432",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROCESSOR_LEVEL",
    "PROCESSOR_REVISION",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "PROGRAMW6432",
    "PUBLIC",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
    # POSIX system environment
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "PWD",
    "SHELL",
    "TERM",
    "TMPDIR",
    "USER",
    # Python & runtime environment
    "PYTHONHOME",
    "PYTHONIOENCODING",
    "PYTHONUNBUFFERED",
    "PYTHONUTF8",
    "VIRTUAL_ENV",
})


def _windows_process_tree(root_pid: int) -> tuple[int, ...]:
    """Return ``root_pid`` and its descendants from a point-in-time snapshot.

    ``taskkill /T`` is not available in every restricted Windows runtime.  The
    Toolhelp snapshot is a standard OS API and lets the adapter enforce the same
    process-tree boundary without relying on a shell command or elevated rights.

    This is the *fallback*: a job object, when one could be created and the child
    could be assigned to it, terminates the tree without a snapshot at all.
    """

    if os.name != "nt":
        return ()

    class _ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == INVALID_HANDLE_VALUE:
        # "Unknown" must not be reported as "the tree is just the root": the caller
        # uses an empty result to reach the legacy taskkill fallback.
        return ()

    try:
        entry = _ProcessEntry32W()
        entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
        parent_by_pid: dict[int, int] = {}
        first = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while first:
            parent_by_pid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            first = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)

    tree = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent_pid in parent_by_pid.items():
            if parent_pid in tree and pid not in tree:
                tree.add(pid)
                changed = True
    return tuple(sorted(tree, reverse=True))


_KERNEL32: "ctypes.WinDLL | None" = None


def _kernel32() -> "ctypes.WinDLL":
    """Return kernel32 with the process/job prototypes declared exactly once.

    The declarations matter: without ``restype`` ctypes truncates a 64-bit handle
    to ``c_int``, so ``CreateToolhelp32Snapshot``'s failure value arrives as ``-1``
    while the constant it was compared against is ``2**64 - 1``. That comparison
    could never be true, the legacy ``taskkill`` fallback was unreachable, and a
    failed snapshot silently reported "the tree is just the root".
    """

    global _KERNEL32
    if _KERNEL32 is None:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        library.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        library.Process32FirstW.restype = wintypes.BOOL
        library.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.Process32NextW.restype = wintypes.BOOL
        library.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.CloseHandle.restype = wintypes.BOOL
        library.CloseHandle.argtypes = [ctypes.c_void_p]
        library.CreateJobObjectW.restype = ctypes.c_void_p
        library.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        library.AssignProcessToJobObject.restype = wintypes.BOOL
        library.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.TerminateJobObject.restype = wintypes.BOOL
        library.TerminateJobObject.argtypes = [ctypes.c_void_p, wintypes.UINT]
        _KERNEL32 = library
    return _KERNEL32


def _create_process_tree_job() -> int | None:
    """Create the Windows job object that will own one child's process tree."""

    if os.name != "nt":
        return None
    try:
        handle = _kernel32().CreateJobObjectW(None, None)
        return int(handle) if handle else None
    except Exception:  # noqa: BLE001 - a job is an optimisation, not a requirement
        return None


def _assign_process_to_job(job: int, proc: "subprocess.Popen[bytes]") -> bool:
    """Put the child in ``job`` so every process it creates belongs to it as well."""

    try:
        return bool(
            _kernel32().AssignProcessToJobObject(
                ctypes.c_void_p(job), ctypes.c_void_p(int(proc._handle))
            )
        )
    except Exception:  # noqa: BLE001 - fall back to the snapshot path
        return False


def _terminate_process_tree_job(job: int) -> None:
    """Terminate every process in the job, which is the whole tree at once."""

    try:
        _kernel32().TerminateJobObject(ctypes.c_void_p(job), 0)
    except Exception:  # noqa: BLE001 - best effort, the caller still kills the child
        pass


def _close_process_tree_job(job: int) -> None:
    try:
        _kernel32().CloseHandle(ctypes.c_void_p(job))
    except Exception:  # noqa: BLE001 - a leaked handle is not worth failing a run
        pass


@runtime_checkable
class ExecutionBackend(Protocol):
    """Contract for Forge execution backends."""

    def execute(self, target: Any) -> ExecutionResult:
        """Execute an authorized execution in an isolated environment."""
        ...



@dataclass(frozen=True)
class _RootContract:
    """Minimal workspace contract over a plain root path.

    ``WorkspaceBoundary`` requires an explicit workspace, and the only trusted
    root available inside the adapter is the one carried by the
    ``AuthorizedExecution`` token. This adapts that path without inventing a
    second workspace concept.
    """

    root: Path


#: Surfaced so callers and documentation can see exactly what the execution
#: boundary does not do.
EXECUTION_RESIDUAL_RISK = RESIDUAL_RISK


class LocalExecutionAdapter:
    """Executes AuthorizedExecution tokens as isolated local processes in ephemeral workspaces.

    Direct execution without AuthorizedExecution from ExecutionCoordinator is rejected fail-closed (resolves F5).
    """

    def __init__(
        self,
        workspace_root: Path | None = None,
        policy: ExecutionPolicy | None = None,
        redactor: SecretRedactor | None = None,
        workspace_manager: EphemeralWorkspaceManager | None = None,
        *,
        isolate_workspace: bool = True,
        harvests_artifacts: bool = False,
    ) -> None:
        self._workspace_root = workspace_root.resolve() if workspace_root is not None else None
        self._policy = policy or ExecutionPolicy()
        self._redactor = redactor or DefaultSecretRedactor()
        self._workspace_manager = workspace_manager
        self._isolate_workspace = isolate_workspace
        # HARVESTING IS A SEPARATE DECLARATION, NOT A CONSEQUENCE OF ISOLATION.
        # It used to be reachable only inside the isolation branch, so an adapter
        # composed with ``isolate_workspace=False`` silently dropped every declared
        # artifact while the step runner still asserted ``artifacts_are_harvested=
        # True`` about it. A step that really wrote its declared file was therefore
        # reported FAILED with ``artifact_not_produced``, and a FAILED step blocks
        # its dependents -- a wrong claim about a file the operator could see.
        #
        # The parameter is named as the invariant it is: "this adapter harvests
        # declared artifacts". When it is true, harvesting happens on BOTH branches.
        # ``artifacts_are_harvested`` on the step runner is derived from this value
        # rather than hard-coded, so the runner's verdict can no longer disagree
        # with the adapter's behaviour.
        self._harvests_artifacts = bool(harvests_artifacts)

    @property
    def harvests_artifacts(self) -> bool:
        """Whether this adapter harvests declared artifacts, on either branch.

        Read by the composition so the step runner's own ``artifacts_are_harvested``
        claim is derived from the adapter instead of asserted about it. When this is
        ``False`` the runner reports an ``ARTIFACT_PRODUCED`` check as unevaluable
        (``ERROR``), not as absent (``FAILED``).

        AN ISOLATING ADAPTER ALWAYS HARVESTS, and this property says so. Harvesting
        used to live inside the isolation branch, so ``isolate_workspace=True`` alone
        harvested. Making it depend on the new declaration ALONE silently stopped
        every existing isolating composition from harvesting -- measured: with the
        default ``harvests_artifacts=False`` an isolating adapter harvested nothing,
        while the step runner was still told the artifact had been looked for, and a
        genuinely produced artifact was reported ``FAILED`` with
        ``artifact_not_produced``. The declaration therefore *adds* harvesting to the
        non-isolated branch; it does not remove it from the isolated one. It remains
        an accurate answer to "does this adapter harvest": the composition that
        isolates and the composition that declares the flag both get ``True``.
        """

        return bool(self._harvests_artifacts or self._isolate_workspace)

    def execute(self, target: Any) -> ExecutionResult:
        """Execute an AuthorizedExecution token safely."""
        # Backend authority check: direct calls without AuthorizedExecution are rejected fail-closed (resolves F5)
        if not isinstance(target, AuthorizedExecution):
            raise ExecutionAuthorizationError(
                "Execution denied: LocalExecutionAdapter requires AuthorizedExecution from ExecutionCoordinator"
            )
        if not target.is_valid():
            raise ExecutionAuthorizationError(
                "Execution denied: Invalid or forged coordinator token in AuthorizedExecution"
            )

        intent = target.intent
        workspace_root = target.workspace_root
        request_id = target.request_id
        metadata = dict(target.metadata)

        # Secret registration & environment preparation
        active_redactor = self._prepare_redactor(intent)
        env = self._build_scoped_environment(intent)

        # Ephemeral workspace isolation & execution
        if self._isolate_workspace:
            ws_mgr = self._workspace_manager or EphemeralWorkspaceManager(
                source_workspace_root=workspace_root
            )
            # A caller-supplied manager carries a scratch location but no source root, and
            # without one the isolated run executes in an EMPTY directory: the project's
            # files are absent and a command that references one fails with "can't open
            # file". Binding the frozen root here is what makes staging happen. A manager
            # that already named a source root keeps it.
            ws_mgr.bind_source_root(workspace_root)
            with ws_mgr:
                scratch_root = ws_mgr.scratch_root
                # The trusted execution root actually in use for this child. The
                # boundary is derived from it, never from the path being checked.
                boundary = WorkspaceBoundary.for_workspace(_RootContract(scratch_root))
                resolved_cwd, err_res = self._resolve_working_directory(
                    intent.working_directory, scratch_root, request_id, metadata
                )
                if err_res is not None:
                    return active_redactor.redact_result(err_res)

                raw_result = self._run_process(
                    intent, resolved_cwd, env, request_id, metadata, boundary=boundary
                )

                # Harvest artifacts before workspace cleanup
                raw_result = self._harvest_declared_artifacts(
                    intent,
                    raw_result,
                    request_id=request_id,
                    metadata=metadata,
                    manager=ws_mgr,
                )
        else:
            # The trusted execution root carried by the AuthorizedExecution token.
            boundary = WorkspaceBoundary.for_workspace(_RootContract(workspace_root))
            resolved_cwd, err_res = self._resolve_working_directory(
                intent.working_directory, workspace_root, request_id, metadata
            )
            if err_res is not None:
                return active_redactor.redact_result(err_res)
            raw_result = self._run_process(
                intent, resolved_cwd, env, request_id, metadata, boundary=boundary
            )
            # No isolation, so there is no scratch directory to harvest from and no
            # cleanup to beat. When harvest is declared, the trusted token root is
            # the root measured -- the same root the child just wrote to.
            raw_result = self._harvest_declared_artifacts(
                intent,
                raw_result,
                request_id=request_id,
                metadata=metadata,
                manager=self._workspace_manager,
                root=workspace_root,
            )

        return active_redactor.redact_result(raw_result)

    def _harvest_declared_artifacts(
        self,
        intent: ExecutionIntent,
        raw_result: ExecutionResult,
        *,
        request_id: str,
        metadata: dict[str, object],
        manager: EphemeralWorkspaceManager | None = None,
        root: Path | None = None,
    ) -> ExecutionResult:
        """Record a digest for each declared artifact target, or report why not.

        One implementation for both branches, so "the adapter harvests artifacts"
        cannot be true on one and false on the other. The only thing that decides
        whether this runs is ``self.harvests_artifacts`` -- the isolating branch, and
        any composition that declared the flag. When it is off the result is returned
        untouched, and the step runner reads that as "unevaluable" rather than as
        "the artifact is absent".

        ``root`` is used when there is no scratch directory (the non-isolated
        branch). It is the workspace root carried by the ``AuthorizedExecution``
        token, never a value taken from the request.

        A harvest failure is reported as an ``ERROR`` result carrying
        ``artifact_error``, which the step runner classifies as
        ``verification_error:artifact_harvest_failed`` -- a distinct verdict from
        both PASSED and FAILED.
        """

        if not (self.harvests_artifacts and intent.artifact_targets):
            return raw_result

        if manager is not None and not self._isolate_workspace:
            # The composed manager belongs to an isolation run and has no scratch
            # root here. Measuring against it would raise
            # "Ephemeral workspace has not been initialized" and be reported as an
            # ``artifact_error`` -- which would look like a harvest failure while
            # really being a wiring mistake. The token root is used instead.
            manager = None

        run_id = str(metadata.get("run_id") or request_id)
        provenance = str(metadata.get("command_executable") or intent.executable)
        try:
            if manager is not None:
                artifacts = manager.harvest_artifacts(
                    intent.artifact_targets,
                    run_id=run_id,
                    provenance_source=provenance,
                    root=root,
                )
            else:
                # No manager composed: bind one to the root being measured. It is
                # used for path resolution and hashing only -- ``initialize()`` is
                # never called, so no scratch tree is created and no cleanup runs
                # against the operator's workspace.
                artifacts = EphemeralWorkspaceManager(
                    source_workspace_root=root
                ).harvest_artifacts(
                    intent.artifact_targets,
                    run_id=run_id,
                    provenance_source=provenance,
                    root=root,
                )
        except Exception as exc:
            return ExecutionResult(
                request_id=raw_result.request_id,
                status=ExecutionStatus.ERROR,
                exit_code=raw_result.exit_code,
                stdout=raw_result.stdout,
                stderr=f"{raw_result.stderr}\nArtifact harvesting error: {exc}".strip(),
                duration_seconds=raw_result.duration_seconds,
                truncated=raw_result.truncated,
                metadata={**raw_result.metadata, "artifact_error": str(exc)},
                outcome_status=raw_result.outcome_status,
            )
        return ExecutionResult(
            request_id=raw_result.request_id,
            status=raw_result.status,
            exit_code=raw_result.exit_code,
            stdout=raw_result.stdout,
            stderr=raw_result.stderr,
            duration_seconds=raw_result.duration_seconds,
            truncated=raw_result.truncated,
            metadata=raw_result.metadata,
            outcome_status=raw_result.outcome_status,
            artifacts=artifacts,
        )

    def _resolve_working_directory(
        self,
        working_directory: str,
        root: Path,
        request_id: str,
        metadata: dict[str, object],
    ) -> tuple[Path, ExecutionResult | None]:
        # One canonical containment implementation, shared with the authority
        # boundary, so the adapter cannot disagree with the RunScope about what
        # "inside the workspace" means.
        try:
            boundary = WorkspaceBoundary.for_workspace(_RootContract(root))
            resolved_cwd = boundary.resolve_relative(working_directory)
        except WorkspaceContainmentError as exc:
            return root, ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.DENIED,
                exit_code=None,
                stdout="",
                stderr=f"Execution denied by run scope: {exc}",
                duration_seconds=0.0,
                metadata={"denial_reason": "working_directory_escapes_workspace", **metadata},
            )

        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            return resolved_cwd, ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.ERROR,
                exit_code=None,
                stdout="",
                stderr=f"Working directory does not exist: {resolved_cwd}",
                duration_seconds=0.0,
                metadata={"error": "working_directory_missing", **metadata},
            )

        return resolved_cwd, None

    def _prepare_redactor(self, intent: ExecutionIntent) -> SecretRedactor:
        secrets_to_register: list[str] = []
        all_envs = list(intent.environment_variables) + list(
            getattr(intent, "profile_environment_variables", ())
        )
        for k, v in all_envs:
            val_str = str(v).strip()
            if not val_str:
                continue
            # Register by sensitive key name, and also by credential *value*
            # shape so a credential cannot bypass redaction merely because the
            # caller named its variable something innocent such as `GH_PAT`.
            if _SENSITIVE_KEY_PATTERN.search(str(k)) or looks_like_credential(val_str):
                secrets_to_register.append(val_str)

        if secrets_to_register and isinstance(self._redactor, DefaultSecretRedactor):
            return self._redactor.with_registered_secrets(secrets_to_register)
        return self._redactor

    def _build_scoped_environment(self, intent: ExecutionIntent) -> dict[str, str]:
        env: dict[str, str] = {}
        for k in SAFE_ENV_WHITELIST_KEYS:
            if k in os.environ:
                env[k] = os.environ[k]
            elif os.name == "nt":
                for ek, ev in os.environ.items():
                    if ek.upper() == k:
                        env[ek] = ev
                        break

        for k, v in intent.environment_variables:
            env[str(k)] = str(v)

        # Network denial is a POLICY boundary, not a kernel sandbox. It points the
        # proxy variables of well-behaved HTTP clients at a closed port, which is
        # effective for libraries that honour them and ineffective against a
        # process that opens a socket directly. The child is not network-isolated.
        if not intent.network_access:
            env["http_proxy"] = "http://127.0.0.1:0"
            env["https_proxy"] = "http://127.0.0.1:0"
            env["all_proxy"] = "http://127.0.0.1:0"
            env["HTTP_PROXY"] = "http://127.0.0.1:0"
            env["HTTPS_PROXY"] = "http://127.0.0.1:0"
            env["ALL_PROXY"] = "http://127.0.0.1:0"
            env["NO_PROXY"] = ""

        return env

    def _run_process(
        self,
        intent: ExecutionIntent,
        cwd_path: Path,
        env: dict[str, str],
        request_id: str,
        metadata: dict[str, object],
        *,
        boundary: WorkspaceBoundary,
    ) -> ExecutionResult:
        timeout = intent.timeout_seconds
        max_output_bytes = intent.max_output_bytes

        # THE LAST GATE BEFORE A PROCESS EXISTS. A bare executable with an empty pin
        # could only be launched by name, and the operating system resolves that name
        # at spawn time against the child's PATH -- not the PATH the fingerprint was
        # authorized against. Measured before this gate existed: one approved
        # fingerprint executed two different images depending on PATH. Refusing here
        # keeps the builder (and therefore every policy decision) total, while making
        # the property "the launched image is the authorized image" hold where it
        # matters.
        if not intent.resolved_executable and is_bare_executable(intent.executable):
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.DENIED,
                exit_code=None,
                stdout="",
                stderr=(
                    "Execution denied: the bare executable could not be pinned to an "
                    "absolute path, so the authorized image cannot be guaranteed: "
                    f"{intent.executable!r}"
                ),
                duration_seconds=0.0,
                metadata={
                    "denial_reason": "executable_not_pinned",
                    **metadata,
                },
            )

        full_command = (intent.resolved_executable or intent.executable,) + intent.argv

        # Last check before the child exists, against the trusted execution root -
        # never against the path itself. The directory is re-canonicalized from the
        # live filesystem and proved contained, and the canonical path is what
        # Popen receives, so a link swapped after the authority check is caught
        # here. This narrows the window between check and spawn; it cannot remove
        # it, because only an OS-level sandbox can, so GAP-A stays PARTIAL.
        if not isinstance(boundary, WorkspaceBoundary):
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.DENIED,
                exit_code=None,
                stdout="",
                stderr="Execution denied by run scope: no trusted workspace boundary",
                duration_seconds=0.0,
                metadata={
                    "denial_reason": "working_directory_escapes_workspace",
                    **metadata,
                },
            )
        live_cwd = canonical_root(cwd_path)
        if not boundary.contains(live_cwd) or not live_cwd.is_dir():
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.DENIED,
                exit_code=None,
                stdout="",
                stderr=(
                    "Execution denied by run scope: working directory is no longer "
                    "inside the trusted workspace root"
                ),
                duration_seconds=0.0,
                metadata={
                    "denial_reason": "working_directory_escapes_workspace",
                    **metadata,
                },
            )

        start_time = time.monotonic()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        start_new_session = (os.name != "nt")

        try:
            proc = subprocess.Popen(
                list(full_command),
                cwd=str(live_cwd),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except FileNotFoundError:
            duration = time.monotonic() - start_time
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.ERROR,
                exit_code=None,
                stdout="",
                stderr=f"Executable not found: {full_command[0]}",
                duration_seconds=duration,
                metadata=dict(metadata),
            )
        except Exception as exc:
            duration = time.monotonic() - start_time
            return ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.ERROR,
                exit_code=None,
                stdout="",
                stderr=f"Adapter execution exception: {type(exc).__name__}: {str(exc)}",
                duration_seconds=duration,
                metadata=dict(metadata),
            )

        # The whole tree is owned by a Windows job object, so terminating the job
        # terminates every descendant at once -- including one that outlived its
        # parent -- without depending on a PID snapshot or on ``taskkill``, which a
        # restricted runtime may refuse. Measured on this host: a grandchild that
        # inherited the pipes kept the adapter's staged directory locked until the
        # job object was terminated.
        process_job = _create_process_tree_job()
        if process_job is not None and not _assign_process_to_job(process_job, proc):
            _close_process_tree_job(process_job)
            process_job = None

        try:
            # The wall-clock deadline is enforced by ``wait``, NOT by
            # ``communicate(timeout=...)``. ``communicate`` returns when the pipes reach
            # EOF, and a pipe stays open for as long as *any* process holds the write end
            # -- including a descendant that inherited it and outlived the direct child.
            # A measured probe with ``timeout_seconds = 0.5`` took 6.25 s to return for
            # exactly that reason, so the configured timeout did not bound the run.
            #
            # Output is drained by reader threads instead. They are daemons, so a
            # descendant that keeps a pipe open cannot hold the adapter past the
            # deadline: after the kill we give the readers a short grace period and take
            # whatever arrived.
            stdout_buffer: list[bytes] = []
            stderr_buffer: list[bytes] = []
            readers = self._start_output_readers(proc, stdout_buffer, stderr_buffer)

            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._terminate_process_tree(proc, job=process_job)
            else:
                # The direct child is gone, but its descendants are not necessarily: a
                # child that spawns a detached sleeper and returns leaves a running
                # process behind, and the former early return in
                # ``_terminate_process_tree`` meant nothing was ever killed in that case.
                # Reaping is attempted unconditionally so a run cannot leave work running
                # on the host after the adapter has reported its outcome. This is
                # best-effort by nature -- an OS-level sandbox is what would make it a
                # guarantee, and that remains deferred.
                self._terminate_process_tree(proc, job=process_job)

            # The readers are released without waiting for a descendant that still holds
            # the pipes: whatever arrived is taken, and an unbounded wait is not.
            for stream_name, stream in (("stdout", proc.stdout), ("stderr", proc.stderr)):
                try:
                    if stream is not None and not stream.closed:
                        if os.name == "nt":
                            # Windows may block closing a pipe while a descendant
                            # still owns an inherited write handle -- measured: a
                            # synchronous close waited 20 s for a grandchild, on the
                            # normal-exit path as well as the timeout path, so the
                            # adapter's wall clock was not bounded by its own timeout.
                            # The daemon closer releases it when the OS permits and
                            # the Popen attributes are cleared so the finally block
                            # below does not repeat the blocking close.
                            threading.Thread(
                                target=stream.close,
                                name=f"forge-close-{stream_name}",
                                daemon=True,
                            ).start()
                            setattr(proc, stream_name, None)
                        else:
                            stream.close()
                except Exception:
                    pass

            duration = time.monotonic() - start_time
            self._join_output_readers(readers)
            stdout_bytes = b"".join(stdout_buffer)
            stderr_bytes = b"".join(stderr_buffer)

            if timed_out:
                exit_code = None
                status = ExecutionStatus.TIMEOUT
                raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
                raw_stderr = stderr_bytes.decode("utf-8", errors="replace")
                raw_stderr = (
                    f"{raw_stderr}\nProcess timed out after {timeout} seconds".strip()
                    if raw_stderr
                    else f"Process timed out after {timeout} seconds"
                )
            else:
                exit_code = proc.returncode
                status = (
                    ExecutionStatus.SUCCESS
                    if exit_code == 0
                    else ExecutionStatus.FAILURE
                )
                raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
                raw_stderr = stderr_bytes.decode("utf-8", errors="replace")
        finally:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
            if proc.stderr and not proc.stderr.closed:
                proc.stderr.close()
            if process_job is not None:
                _close_process_tree_job(process_job)

        # Enforce output truncation limit
        truncated = False
        out_enc = raw_stdout.encode("utf-8")
        if len(out_enc) > max_output_bytes:
            raw_stdout = out_enc[:max_output_bytes].decode("utf-8", errors="ignore")
            truncated = True

        err_enc = raw_stderr.encode("utf-8")
        if len(err_enc) > max_output_bytes:
            raw_stderr = err_enc[:max_output_bytes].decode("utf-8", errors="ignore")
            truncated = True

        return ExecutionResult(
            request_id=request_id,
            status=status,
            exit_code=exit_code,
            stdout=raw_stdout,
            stderr=raw_stderr,
            duration_seconds=duration,
            truncated=truncated,
            metadata=dict(metadata),
        )

    @staticmethod
    def _start_output_readers(
        proc: subprocess.Popen[bytes],
        stdout_buffer: list[bytes],
        stderr_buffer: list[bytes],
    ) -> list[threading.Thread]:
        """Drain stdout/stderr on daemon threads so a held-open pipe cannot block us."""

        def pump(stream, buffer: list[bytes]) -> None:
            if stream is None:
                return
            try:
                for chunk in iter(lambda: stream.read(65536), b""):
                    buffer.append(chunk)
            except Exception:
                # A closed or killed pipe is an expected outcome on the timeout path.
                return

        threads = [
            threading.Thread(target=pump, args=(proc.stdout, stdout_buffer), daemon=True),
            threading.Thread(target=pump, args=(proc.stderr, stderr_buffer), daemon=True),
        ]
        for thread in threads:
            thread.start()
        return threads

    @staticmethod
    def _join_output_readers(threads: list[threading.Thread], grace: float = 1.0) -> None:
        """Wait briefly for the readers, then stop waiting.

        The deadline is bounded on purpose: a descendant that inherited the pipes can
        hold them open indefinitely, and blocking on that is the defect this replaced.
        Anything already read is kept; anything still buffered in a dead pipe is not worth
        an unbounded wait.
        """

        deadline = time.monotonic() + grace
        for thread in threads:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)

    def _terminate_process_tree(
        self, proc: subprocess.Popen[bytes], *, job: int | None = None
    ) -> None:
        """Terminate the process tree, Windows and POSIX alike.

        The former early return on ``proc.poll() is not None`` was a defect: when the
        direct child had already exited -- precisely the daemonizing case, where the child
        spawns a descendant and returns -- the tree was never killed, so the timeout path
        could report ``TIMEOUT`` while the descendant kept running. ``taskkill /T`` and
        ``killpg`` both tolerate an already-exited leader, so the attempt is now
        unconditional.

        ``job``, when present, is the job object the child was assigned to at spawn
        time.  Terminating it is the only mechanism here that reaches a descendant
        that outlived its parent without a PID lookup, and it is the one that works
        in a runtime that refuses both ``os.kill`` on a foreign PID and
        ``taskkill``.
        """

        if os.name == "nt":
            if job is not None:
                _terminate_process_tree_job(job)
            else:
                # Fallback path: enumerate the tree from a Toolhelp snapshot. A
                # snapshot failure returns an empty tree -- never "just the root" --
                # so the legacy command below is actually reachable.
                try:
                    process_tree = _windows_process_tree(proc.pid)
                except Exception:  # noqa: BLE001 - the snapshot is best effort
                    process_tree = ()
                for pid in process_tree:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass
                if not process_tree:
                    # A snapshot can fail transiently (for example during process
                    # table pressure), so retain the legacy command as a fallback.
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            capture_output=True,
                            timeout=5,
                            check=False,
                        )
                    except Exception:
                        pass
            try:
                proc.kill()
            except Exception:
                pass
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                time.sleep(0.2)
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

        try:
            proc.wait(timeout=2)
        except Exception:
            pass


class LocalProcessExecutionBackend(LocalExecutionAdapter):
    """Default v0.1 implementation of ExecutionBackend protocol executing isolated local processes."""
