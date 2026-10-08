"""Durable physical execution telemetry for Forge Core.

This module owns exactly one responsibility: persisting the **physical**
measurement of a completed execution attempt so it survives process restart.

Stage 0.1 boundary (``D-PLATFORM-01..12``, frozen in ``docs/DECISIONS.md``):

* **Physical only.** ``PhysicalTelemetry`` carries tokens, duration, attempt
  identity, and outcome. It deliberately carries **no** ``estimated_cost``,
  ``provider_reported_cost``, ``effective_cost``, price, charge, wallet balance,
  credits, billing account, project ownership, organization ownership, or user
  identity. Usage is a physical measurement; money is not measured here.
* **Core stays autonomous.** This module imports nothing from a Platform layer,
  no database driver, and no payment library. The only durable mechanism is an
  append-only UTF-8 JSONL file next to the existing ``RunStore`` root, and it is
  explicitly transitional infrastructure until a Platform persistence layer
  exists.
* **Terminal attempts only.** A record is written when an attempt reaches a
  terminal outcome. An attempt that is still running, or that is waiting for
  approval, produces no record merely because intermediate values were visible
  in memory.
* **Idempotent by attempt identity.** One attempt produces at most one durable
  record. Re-persisting the same attempt is a no-op.
* **No unsafe object serialization.** Serialization is an explicit, bounded
  ``to_dict`` over a closed field set.

The writer follows the existing ``RunStore`` convention: the storage root lives
outside the source checkout, is relocatable through ``FORGE_TELEMETRY_ROOT``,
and is never the user's workspace.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

__all__ = [
    "PhysicalTelemetry",
    "PhysicalTelemetrySink",
    "FileTelemetrySink",
    "TelemetryWriteError",
    "default_telemetry_root",
]

# Environment variable that relocates the telemetry root. Its own variable, so
# telemetry can be moved independently of run history and of the idempotency
# ledger.
_ENV_ROOT_VAR = "FORGE_TELEMETRY_ROOT"

# Field names that must never appear in a physical telemetry record. This is a
# guard against a future edit quietly reintroducing financial or ownership
# semantics into the physical contract.
FORBIDDEN_TELEMETRY_FIELDS = frozenset(
    {
        "estimated_cost",
        "provider_reported_cost",
        "effective_cost",
        "total_cost",
        "price",
        "pricing",
        "charge",
        "charged",
        "wallet",
        "wallet_balance",
        "balance",
        "credits",
        "credit",
        "billing_account",
        "billing_account_id",
        "invoice",
        "payment",
        "commission",
        "project_id",
        "organization_id",
        "org_id",
        "tenant_id",
        "user_id",
        "owner_id",
        "account_id",
    }
)


class TelemetryWriteError(RuntimeError):
    """A physical telemetry record could not be durably written."""


def default_telemetry_root() -> Path:
    """Return the configured local telemetry root.

    Resolution order:

    1. ``FORGE_TELEMETRY_ROOT`` when set, which is how tests redirect storage to
       a temporary directory;
    2. a platform-appropriate per-user location, so durable telemetry never lands
       inside the source checkout and can never be committed by accident.

    The location is deliberately outside the repository: telemetry is runtime
    state, not source, and the user workspace is never used as a database.
    """
    configured = os.environ.get(_ENV_ROOT_VAR)
    if configured and configured.strip():
        return Path(configured).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base and base.strip():
            return Path(base) / "ForgeAI" / "telemetry"
    return Path.home() / ".forge" / "telemetry"


@dataclass(frozen=True)
class PhysicalTelemetry:
    """The physical measurement of one terminal execution attempt.

    Every field is either an identifier, a measured count, or an outcome label.
    The class carries no money, no tariff, no ownership, and no authority. It is
    immutable, so a persisted measurement can never be rewritten in place.
    """

    run_id: str
    attempt_number: int = 0
    provider_name: str = ""
    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    duration_seconds: float = 0.0
    success: bool = False
    fallback: bool = False
    tool_call_count: int = 0
    completed_at: str = ""
    error_type: str = ""
    attempt_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("physical telemetry requires a non-empty run_id")
        if not isinstance(self.attempt_number, int) or self.attempt_number < 0:
            raise ValueError("attempt_number must be a non-negative integer")
        for field_name in ("input_tokens", "output_tokens", "cached_tokens", "tool_call_count"):
            value = getattr(self, field_name)
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        if not isinstance(self.duration_seconds, (int, float)) or self.duration_seconds < 0:
            raise ValueError("duration_seconds must be a non-negative number")
        if not isinstance(self.success, bool):
            raise ValueError("success must be a boolean")
        if not isinstance(self.fallback, bool):
            raise ValueError("fallback must be a boolean")

    @property
    def identity(self) -> str:
        """Return the canonical, stable attempt identity.

        Uses the existing project convention for an execution attempt
        (``AttemptIdentity.attempt_id``): ``<run_id>#attempt-<n>``. There is no
        second, competing identity format.
        """
        if self.attempt_id:
            return self.attempt_id
        return f"{self.run_id}#attempt-{self.attempt_number}"

    @property
    def total_tokens(self) -> int:
        """Total tokens processed by this attempt."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        """Return the explicit, bounded, serializable projection.

        Only the closed field set below is emitted. There is no ``**metadata``
        passthrough, so a caller cannot smuggle a financial or ownership field
        into the durable record.
        """
        return {
            "attempt_id": self.identity,
            "run_id": self.run_id,
            "attempt_number": int(self.attempt_number),
            "provider_name": str(self.provider_name),
            "model_name": str(self.model_name),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cached_tokens": int(self.cached_tokens),
            "total_tokens": int(self.total_tokens),
            "duration_seconds": round(float(self.duration_seconds), 6),
            "success": bool(self.success),
            "fallback": bool(self.fallback),
            "tool_call_count": int(self.tool_call_count),
            "completed_at": self.completed_at,
            "error_type": str(self.error_type),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PhysicalTelemetry":
        """Reconstruct a record from its serialized projection."""
        if not isinstance(payload, Mapping):
            raise ValueError("telemetry record must be a mapping")
        return cls(
            run_id=str(payload.get("run_id", "")),
            attempt_number=int(payload.get("attempt_number", 0)),
            provider_name=str(payload.get("provider_name", "")),
            model_name=str(payload.get("model_name", "")),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            cached_tokens=int(payload.get("cached_tokens", 0)),
            duration_seconds=float(payload.get("duration_seconds", 0.0)),
            success=bool(payload.get("success", False)),
            fallback=bool(payload.get("fallback", False)),
            tool_call_count=int(payload.get("tool_call_count", 0)),
            completed_at=str(payload.get("completed_at", "")),
            error_type=str(payload.get("error_type", "")),
            attempt_id=str(payload.get("attempt_id", "")),
        )

    def bounded_summary(self) -> dict[str, Any]:
        """Return a small, safe summary suitable for an event payload."""
        return {
            "attempt_id": self.identity,
            "attempt_number": int(self.attempt_number),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cached_tokens": int(self.cached_tokens),
            "duration_seconds": round(float(self.duration_seconds), 3),
            "success": bool(self.success),
            "fallback": bool(self.fallback),
            "tool_call_count": int(self.tool_call_count),
        }


@runtime_checkable
class PhysicalTelemetrySink(Protocol):
    """Minimal port for durable physical telemetry.

    The contract is deliberately one method: a sink either durably records one
    terminal attempt or it raises. A sink that silently drops a record would make
    the durable boundary untrustworthy, so implementations must not swallow
    write errors.
    """

    def record(self, telemetry: PhysicalTelemetry) -> bool:
        """Persist one terminal attempt measurement.

        Returns ``True`` when a new record was written and ``False`` when the
        attempt was already recorded. Raises on a write failure.
        """
        ...


def _sanitize_component(value: str, *, fallback: str = "unknown") -> str:
    """Reduce an identifier to a safe single path component."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value or "")).strip("._")
    return cleaned[:120] or fallback


class FileTelemetrySink:
    """Append-only, local, file-per-run durable telemetry sink.

    Storage layout (deterministic, inspectable, no database)::

        <root>/<run_id>.jsonl     one JSON object per terminal attempt

    Properties:

    * append-only: an existing record is never rewritten;
    * one terminal attempt is one JSON line;
    * UTF-8, explicit bounded serialization, no unsafe object serialization;
    * idempotent per attempt identity, enforced before the append, including
      across a process restart;
    * a write failure raises :class:`TelemetryWriteError` and is never silent.

    This is transitional infrastructure. When a Platform persistence layer
    exists, a sink implementing the same port replaces it without touching Core.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(root) if root is not None else default_telemetry_root()
        # Attempt identities written by this process. Seeded lazily from what is
        # already on disk so a second writer for the same attempt never appends a
        # duplicate line.
        self._recorded: dict[str, set[str]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def path_for_run(self, run_id: str) -> Path:
        """Return the durable log path for one run."""
        return self._root / f"{_sanitize_component(run_id)}.jsonl"

    def _load_identities(self, run_id: str) -> set[str]:
        cached = self._recorded.get(run_id)
        if cached is not None:
            return cached
        identities: set[str] = set()
        path = self.path_for_run(run_id)
        try:
            if path.exists():
                with open(path, "r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            payload = json.loads(line)
                        except ValueError:
                            # A truncated tail line is not a reason to lose the
                            # rest of the log, and it must not be treated as a
                            # valid record either.
                            continue
                        identity = str(payload.get("attempt_id") or "")
                        if identity:
                            identities.add(identity)
        except OSError as exc:
            raise TelemetryWriteError(
                f"cannot read the telemetry log for run '{run_id}': {exc}"
            ) from exc
        self._recorded[run_id] = identities
        return identities

    def record(self, telemetry: PhysicalTelemetry) -> bool:
        """Append one terminal attempt measurement.

        Returns ``False`` when the attempt is already recorded. Raises
        :class:`TelemetryWriteError` when the record could not be written.
        """
        if not isinstance(telemetry, PhysicalTelemetry):
            raise TypeError("record() requires a PhysicalTelemetry instance")
        identity = telemetry.identity
        if identity in self._load_identities(telemetry.run_id):
            return False
        path = self.path_for_run(telemetry.run_id)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(
                telemetry.to_dict(), ensure_ascii=False, sort_keys=True, default=str
            )
            if "\n" in line or "\r" in line:
                raise TelemetryWriteError(
                    "a serialized telemetry record must be a single line"
                )
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except TelemetryWriteError:
            raise
        except OSError as exc:
            raise TelemetryWriteError(
                f"cannot append telemetry for run '{telemetry.run_id}': {exc}"
            ) from exc
        self._recorded[telemetry.run_id].add(identity)
        return True

    def read_records(self, run_id: str) -> tuple[PhysicalTelemetry, ...]:
        """Return the durable records for one run, oldest first.

        A malformed or truncated line is skipped rather than guessed at, so a
        partially written tail never fabricates a measurement.
        """
        path = self.path_for_run(run_id)
        if not path.exists():
            return ()
        records: list[PhysicalTelemetry] = []
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except ValueError:
                        continue
                    try:
                        records.append(PhysicalTelemetry.from_dict(payload))
                    except (ValueError, TypeError):
                        continue
        except OSError as exc:
            raise TelemetryWriteError(
                f"cannot read the telemetry log for run '{run_id}': {exc}"
            ) from exc
        return tuple(records)

    def list_run_ids(self) -> tuple[str, ...]:
        """Return the run ids that have at least one durable telemetry record."""
        if not self._root.exists():
            return ()
        names = []
        for entry in sorted(self._root.glob("*.jsonl")):
            names.append(entry.stem)
        return tuple(names)
