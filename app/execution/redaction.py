"""Secret redaction contracts and default redaction implementation."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Protocol, runtime_checkable

from app.execution.request import ExecutionRequest, ExecutionResult


_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|access[_-]?token|private[_-]?key|auth|credential)",
    re.IGNORECASE,
)

# Known credential prefixes. A value starting with one of these is treated as a
# credential regardless of the name of the key or argument that carries it, so a
# leaked secret cannot slip through merely because the caller chose a key name
# like `GH_PAT` instead of `GITHUB_TOKEN`.
_CREDENTIAL_PREFIX_PATTERN = re.compile(
    r"(?:github_pat_|gh[opsrut]_|sk-|xox[baprs]-|AIza[0-9A-Za-z_-]{20,})"
)

# GitHub App installation tokens became stateless on 2026-10-02: still prefixed
# `ghs_`, but roughly 520 characters instead of 40. There is deliberately no
# upper bound here: an upper bound would let an over-long value be redacted only
# partially, leaving a usable fragment behind. Length is never used to validate a
# credential, only to recognise its prefix.
_GH_APP_TOKEN_PATTERN = r"gh[opsrut]_[A-Za-z0-9]{16,}"
_GH_FINE_GRAINED_TOKEN_PATTERN = r"github_pat_[A-Za-z0-9]{16,}_[A-Za-z0-9]{16,}"

# Suffix is a superset of GitHub's own character set (it appends a checksum),
# and the leading prefix is required so this cannot leak into ordinary prose.
_TOKEN_PREFIX = r"(?:github_pat_|gh[opsrut]_|xox[baprs]-|glpat-|npm_|pypi-)"

_COMMON_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[a-zA-Z0-9_-]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}"),
    re.compile(_GH_APP_TOKEN_PATTERN),
    re.compile(_GH_FINE_GRAINED_TOKEN_PATTERN),
    re.compile(r"xox[baprs]-[a-zA-Z0-9-]+"),
    # `~` is allowed because GitHub tokens may carry a checksum suffix.
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.~]{16,}", re.IGNORECASE),
    re.compile(rf"{_TOKEN_PREFIX}[A-Za-z0-9_\-\.~]{{16,}}"),
    # Bare `token=`-style assignments, including `access_token=`, `auth_token=`
    # and `TOKEN:`. The existing key-name heuristic does not catch these when the
    # value is quoted, JSON-escaped, or embedded in free text.
    re.compile(
        r"(?:access[_-]?|auth[_-]?|api[_-]?)?token\s*[:=]\s*[\"']?[A-Za-z0-9_\-\.~]{16,}",
        re.IGNORECASE,
    ),
)

REDACTED_PLACEHOLDER = "[REDACTED]"


def looks_like_credential(value: object) -> bool:
    """Report whether a value carries a known credential prefix.

    Used to register secret *values* for redaction regardless of the name of the
    key, environment variable, or argument that carries them.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if len(candidate) < 20:
        return False
    return _CREDENTIAL_PREFIX_PATTERN.match(candidate) is not None


@runtime_checkable
class SecretRedactor(Protocol):
    """Contract for masking secrets in execution metadata, outputs, and commands."""

    def redact_text(self, text: str) -> str:
        """Mask secrets in arbitrary text (e.g. stdout, stderr, logs)."""
        ...

    def redact_mapping(self, mapping: Mapping[str, object]) -> dict[str, object]:
        """Mask sensitive keys and values in dictionary/mapping metadata."""
        ...

    def redact_command(self, command: tuple[str, ...]) -> tuple[str, ...]:
        """Mask arguments containing secrets or secret patterns."""
        ...

    def redact_request(self, request: ExecutionRequest) -> ExecutionRequest:
        """Return a sanitized copy of an ExecutionRequest."""
        ...

    def redact_result(self, result: ExecutionResult) -> ExecutionResult:
        """Return a sanitized copy of an ExecutionResult."""
        ...

    def safe_event_data(self, result: ExecutionResult) -> dict[str, object]:
        """Produce safe, sanitized execution metadata suitable for audit events."""
        ...


class DefaultSecretRedactor:
    """Production-safe deterministic secret redactor."""

    def __init__(
        self,
        registered_secrets: Sequence[str] = (),
        custom_patterns: Sequence[re.Pattern[str]] = (),
    ) -> None:
        self._registered_secrets: tuple[str, ...] = tuple(
            s for s in registered_secrets if isinstance(s, str) and s.strip()
        )
        self._patterns: tuple[re.Pattern[str], ...] = (
            _COMMON_SECRET_PATTERNS + tuple(custom_patterns)
        )

    def with_registered_secrets(self, secrets: Sequence[str]) -> DefaultSecretRedactor:
        """Return a new redactor with additional registered secrets."""
        merged = list(self._registered_secrets)
        for s in secrets:
            if isinstance(s, str) and s.strip() and s not in merged:
                merged.append(s)
        custom = self._patterns[len(_COMMON_SECRET_PATTERNS):]
        return DefaultSecretRedactor(registered_secrets=merged, custom_patterns=custom)

    def redact_text(self, text: str) -> str:
        if not text:
            return text

        redacted = text
        for secret in self._registered_secrets:
            if secret in redacted:
                redacted = redacted.replace(secret, REDACTED_PLACEHOLDER)

        for pattern in self._patterns:
            redacted = pattern.sub(REDACTED_PLACEHOLDER, redacted)

        return redacted

    def redact_mapping(self, mapping: Mapping[str, object]) -> dict[str, object]:
        sanitized: dict[str, object] = {}
        for key, value in mapping.items():
            if _SENSITIVE_KEY_PATTERN.search(str(key)):
                sanitized[str(key)] = REDACTED_PLACEHOLDER
            elif isinstance(value, str):
                sanitized[str(key)] = self.redact_text(value)
            elif isinstance(value, Mapping):
                sanitized[str(key)] = self.redact_mapping(value)
            elif isinstance(value, (list, tuple)):
                sanitized[str(key)] = [
                    self.redact_text(item)
                    if isinstance(item, str)
                    else (
                        self.redact_mapping(item)
                        if isinstance(item, Mapping)
                        else item
                    )
                    for item in value
                ]
            else:
                sanitized[str(key)] = value
        return sanitized

    def redact_command(self, command: tuple[str, ...]) -> tuple[str, ...]:
        sanitized: list[str] = []
        for part in command:
            if "=" in part:
                prefix, sep, suffix = part.partition("=")
                if _SENSITIVE_KEY_PATTERN.search(prefix):
                    sanitized.append(f"{prefix}{sep}{REDACTED_PLACEHOLDER}")
                    continue
            sanitized.append(self.redact_text(part))
        return tuple(sanitized)

    def redact_request(self, request: ExecutionRequest) -> ExecutionRequest:
        sanitized_command = self.redact_command(request.command)
        sanitized_env = {
            k: REDACTED_PLACEHOLDER
            if _SENSITIVE_KEY_PATTERN.search(k)
            else self.redact_text(str(v))
            for k, v in request.environment_variables.items()
        }
        sanitized_metadata = self.redact_mapping(request.metadata)
        # Preserve every declared field. Dropping one here would change how the
        # sanitized request behaves if a caller ever executed it, so the redacted
        # value object must be equivalent to the original except for secrets.
        return replace(
            request,
            command=sanitized_command,
            environment_variables=sanitized_env,
            metadata=sanitized_metadata,
        )

    def redact_result(self, result: ExecutionResult) -> ExecutionResult:
        return ExecutionResult(
            request_id=result.request_id,
            status=result.status,
            exit_code=result.exit_code,
            stdout=self.redact_text(result.stdout),
            stderr=self.redact_text(result.stderr),
            duration_seconds=result.duration_seconds,
            truncated=result.truncated,
            metadata=self.redact_mapping(result.metadata),
            outcome_status=result.outcome_status,
            artifacts=getattr(result, "artifacts", ()),
        )

    def safe_event_data(self, result: ExecutionResult) -> dict[str, object]:
        sanitized = self.redact_result(result)
        return {
            "request_id": sanitized.request_id,
            "status": sanitized.status.value,
            "outcome_status": sanitized.outcome_status.value if sanitized.outcome_status else sanitized.status.value,
            "exit_code": sanitized.exit_code,
            "duration_seconds": sanitized.duration_seconds,
            "truncated": sanitized.truncated,
            "stdout_len": len(sanitized.stdout),
            "stderr_len": len(sanitized.stderr),
            "metadata": sanitized.metadata,
        }
