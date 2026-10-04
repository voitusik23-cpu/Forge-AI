"""Deterministic local execution adapter using safe subprocess execution."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from app.execution.policy import ExecutionPolicy
from app.execution.redaction import DefaultSecretRedactor, SecretRedactor
from app.execution.request import (
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
)


class LocalExecutionAdapter:
    """Executes authorized ExecutionRequests as isolated local processes without shell."""

    def __init__(
        self,
        workspace_root: Path | None = None,
        policy: ExecutionPolicy | None = None,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self._workspace_root = workspace_root.resolve() if workspace_root is not None else None
        self._policy = policy or ExecutionPolicy()
        self._redactor = redactor or DefaultSecretRedactor()

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Evaluate policy and execute the command locally if authorized."""
        decision = self._policy.evaluate(request)
        if not decision.allowed:
            return self._redactor.redact_result(
                ExecutionResult(
                    request_id=request.request_id if request else "unknown",
                    status=ExecutionStatus.DENIED,
                    exit_code=None,
                    stdout="",
                    stderr=f"Execution denied by policy: {decision.reason}",
                    duration_seconds=0.0,
                    metadata={"denial_reason": decision.reason},
                )
            )

        assert request.profile is not None  # Guaranteed by policy evaluation

        # Resolve working directory safely
        if self._workspace_root is not None:
            resolved_cwd = (self._workspace_root / request.working_directory).resolve()
            try:
                resolved_cwd.relative_to(self._workspace_root)
            except ValueError:
                return self._redactor.redact_result(
                    ExecutionResult(
                        request_id=request.request_id,
                        status=ExecutionStatus.DENIED,
                        exit_code=None,
                        stdout="",
                        stderr="Execution denied: working directory escapes workspace root",
                        duration_seconds=0.0,
                        metadata={"denial_reason": "working_directory_escapes_workspace"},
                    )
                )
            if not resolved_cwd.exists() or not resolved_cwd.is_dir():
                return self._redactor.redact_result(
                    ExecutionResult(
                        request_id=request.request_id,
                        status=ExecutionStatus.ERROR,
                        exit_code=None,
                        stdout="",
                        stderr=f"Working directory does not exist: {resolved_cwd}",
                        duration_seconds=0.0,
                        metadata={"error": "working_directory_missing"},
                    )
                )
            cwd_path = resolved_cwd
        else:
            cwd_path = Path(request.working_directory).resolve()
            if not cwd_path.exists() or not cwd_path.is_dir():
                return self._redactor.redact_result(
                    ExecutionResult(
                        request_id=request.request_id,
                        status=ExecutionStatus.ERROR,
                        exit_code=None,
                        stdout="",
                        stderr=f"Working directory does not exist: {cwd_path}",
                        duration_seconds=0.0,
                        metadata={"error": "working_directory_missing"},
                    )
                )

        # Assemble process environment safely
        env = os.environ.copy()
        for k, v in request.profile.environment_variables.items():
            env[str(k)] = str(v)
        for k, v in request.environment_variables.items():
            env[str(k)] = str(v)

        # Determine timeout
        timeout = (
            request.timeout_seconds
            if request.timeout_seconds is not None
            else request.profile.timeout_seconds
        )

        max_output_bytes = request.profile.max_output_bytes

        # Execute process strictly without shell
        start_time = time.monotonic()
        try:
            proc = subprocess.run(
                list(request.command),
                cwd=str(cwd_path),
                env=env,
                timeout=timeout,
                capture_output=True,
                shell=False,
            )
            duration = time.monotonic() - start_time
            raw_stdout = proc.stdout.decode("utf-8", errors="replace")
            raw_stderr = proc.stderr.decode("utf-8", errors="replace")
            exit_code = proc.returncode
            status = (
                ExecutionStatus.SUCCESS
                if exit_code == 0
                else ExecutionStatus.FAILURE
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - start_time
            raw_stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
            raw_stderr = (
                exc.stderr.decode("utf-8", errors="replace")
                if exc.stderr
                else f"Process timed out after {timeout} seconds"
            )
            exit_code = None
            status = ExecutionStatus.TIMEOUT
        except FileNotFoundError:
            duration = time.monotonic() - start_time
            raw_stdout = ""
            raw_stderr = f"Executable not found: {request.command[0]}"
            exit_code = None
            status = ExecutionStatus.ERROR
        except Exception as exc:
            duration = time.monotonic() - start_time
            raw_stdout = ""
            raw_stderr = f"Adapter execution exception: {type(exc).__name__}: {str(exc)}"
            exit_code = None
            status = ExecutionStatus.ERROR

        # Enforce output truncation limit
        truncated = False
        stdout_bytes = raw_stdout.encode("utf-8")
        if len(stdout_bytes) > max_output_bytes:
            raw_stdout = stdout_bytes[:max_output_bytes].decode("utf-8", errors="ignore")
            truncated = True

        stderr_bytes = raw_stderr.encode("utf-8")
        if len(stderr_bytes) > max_output_bytes:
            raw_stderr = stderr_bytes[:max_output_bytes].decode("utf-8", errors="ignore")
            truncated = True

        raw_result = ExecutionResult(
            request_id=request.request_id,
            status=status,
            exit_code=exit_code,
            stdout=raw_stdout,
            stderr=raw_stderr,
            duration_seconds=duration,
            truncated=truncated,
            metadata=dict(request.metadata),
        )

        return self._redactor.redact_result(raw_result)
