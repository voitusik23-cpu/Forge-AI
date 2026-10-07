"""Deterministic local execution backend using safe subprocess execution in ephemeral workspaces."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.execution.intent import AuthorizedExecution, ExecutionIntent
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


@runtime_checkable
class ExecutionBackend(Protocol):
    """Contract for Forge execution backends."""

    def execute(self, target: Any) -> ExecutionResult:
        """Execute an authorized execution in an isolated environment."""
        ...


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
    ) -> None:
        self._workspace_root = workspace_root.resolve() if workspace_root is not None else None
        self._policy = policy or ExecutionPolicy()
        self._redactor = redactor or DefaultSecretRedactor()
        self._workspace_manager = workspace_manager
        self._isolate_workspace = isolate_workspace

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
            with ws_mgr:
                scratch_root = ws_mgr.scratch_root
                resolved_cwd, err_res = self._resolve_working_directory(
                    intent.working_directory, scratch_root, request_id, metadata
                )
                if err_res is not None:
                    return active_redactor.redact_result(err_res)

                raw_result = self._run_process(
                    intent, resolved_cwd, env, request_id, metadata
                )

                # Harvest artifacts before workspace cleanup
                if intent.artifact_targets:
                    run_id = str(metadata.get("run_id") or request_id)
                    provenance = str(metadata.get("command_executable") or intent.executable)
                    try:
                        artifacts = ws_mgr.harvest_artifacts(
                            intent.artifact_targets,
                            run_id=run_id,
                            provenance_source=provenance,
                        )
                        raw_result = ExecutionResult(
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
                    except Exception as exc:
                        raw_result = ExecutionResult(
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
        else:
            resolved_cwd, err_res = self._resolve_working_directory(
                intent.working_directory, workspace_root, request_id, metadata
            )
            if err_res is not None:
                return active_redactor.redact_result(err_res)
            raw_result = self._run_process(
                intent, resolved_cwd, env, request_id, metadata
            )

        return active_redactor.redact_result(raw_result)

    def _resolve_working_directory(
        self,
        working_directory: str,
        root: Path,
        request_id: str,
        metadata: dict[str, object],
    ) -> tuple[Path, ExecutionResult | None]:
        resolved_cwd = (root / working_directory).resolve()
        try:
            resolved_cwd.relative_to(root)
        except ValueError:
            return resolved_cwd, ExecutionResult(
                request_id=request_id,
                status=ExecutionStatus.DENIED,
                exit_code=None,
                stdout="",
                stderr="Execution denied: working directory escapes workspace root",
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

        # Best-effort network restrictions
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
    ) -> ExecutionResult:
        timeout = intent.timeout_seconds
        max_output_bytes = intent.max_output_bytes
        full_command = (intent.executable,) + intent.argv

        start_time = time.monotonic()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        start_new_session = (os.name != "nt")

        try:
            proc = subprocess.Popen(
                list(full_command),
                cwd=str(cwd_path),
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

        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            duration = time.monotonic() - start_time
            exit_code = proc.returncode
            status = ExecutionStatus.SUCCESS if exit_code == 0 else ExecutionStatus.FAILURE
            raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
            raw_stderr = stderr_bytes.decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            self._terminate_process_tree(proc)
            duration = time.monotonic() - start_time
            try:
                out_b, err_b = proc.communicate(timeout=2)
                raw_stdout = out_b.decode("utf-8", errors="replace") if out_b else ""
                raw_stderr = err_b.decode("utf-8", errors="replace") if err_b else ""
            except Exception:
                raw_stdout = ""
                raw_stderr = ""
            raw_stderr = (
                f"{raw_stderr}\nProcess timed out after {timeout} seconds".strip()
                if raw_stderr
                else f"Process timed out after {timeout} seconds"
            )
            exit_code = None
            status = ExecutionStatus.TIMEOUT
        finally:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
            if proc.stderr and not proc.stderr.closed:
                proc.stderr.close()

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

    def _terminate_process_tree(self, proc: subprocess.Popen[bytes]) -> None:
        """Terminate process tree with first-class Windows and POSIX support."""
        if proc.poll() is not None:
            return

        if os.name == "nt":
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
                if proc.poll() is None:
                    os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
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
