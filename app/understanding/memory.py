"""Project Memory v0.2: freshness, provenance and contradiction over understanding.

WHAT THIS IS, AND WHAT IT IS NOT
================================

This is the **memory layer of Project Understanding**, not a second memory system.
It stores one kind of thing -- an entry about this project, derived from evidence
this repository already produces -- and it reuses, rather than duplicates:

* ``StructuralFact.fact_id`` and ``InferredFact.evidence_fact_ids`` for evidence,
  so there is no parallel provenance scheme;
* ``ContextTrustLevel`` for how much a claim is trusted, so there is no second
  trust vocabulary;
* ``SnapshotFile.fingerprint`` (a SHA-256 of the file's bytes, computed by the
  existing :class:`app.understanding.scanner.BoundedProjectScanner`) for
  freshness, so there is no second hashing scheme;
* ``RunStore.project_state`` for durability, so there is no second storage
  backend.

The existing :mod:`app.memory` package is untouched and un-duplicated: that
package holds project-scoped knowledge items with their own governance, while this
module holds the *evidence-linked understanding* of the current source tree.

THE MEMORY ITEM CONTRACT
========================

Every entry carries, at minimum: **what it is** (``kind``), **where it came from**
(``source``: a source path, an extractor id, a run id, an inference id, or a data
class), **when it was obtained** (``obtained_at``), **how widely it applies**
(``scope``), and **what supports it** (``evidence_fact_ids``/``evidence_ids``).

Two orthogonal fields carry the honesty of the entry, and they are deliberately
**not** merged:

``kind`` -- what sort of thing the entry is:

``CONFIRMED_FACT``
    A deterministic structural observation. Requires at least one
    ``evidence_fact_id``; its trust is ``CONFIRMED`` (or ``VERIFIED`` when a
    recorded verification verdict supports it). It is the only kind that may be
    ``CONFIRMED``.

``EXECUTION_RESULT``
    An observed outcome of a real execution: an exit code, an
    ``ExecutionOutcomeStatus``, a verification verdict. Recorded as ``VERIFIED``:
    the execution plane produced it, not a claim. Requires ``run_id`` and/or
    ``execution_result_id``.

``VERIFIED_CONCLUSION``
    A conclusion drawn from verified evidence. **Trust ``INFERRED``.** It is
    deliberately *not* promotable by this module (see below).

``HYPOTHESIS``
    An assumption or guess. Trust ``INFERRED`` or ``UNVERIFIED``.

``UNKNOWN_DATA``
    Something the project does not know. Recorded so the absence is visible rather
    than being mistaken for a negative finding. Trust ``UNVERIFIED``.

``STALE_ENTRY``
    Written only by freshness evaluation, when an entry's pinned evidence changed,
    was deleted, or was renamed. It names the entry it replaced and why.

``CONTRADICTION``
    Written only by contradiction detection, when two entries make incompatible
    claims about the same thing in the same scope.

``status`` -- the lifecycle: ``CURRENT``, ``STALE``, ``INVALIDATED``,
``SUPERSEDED``. A stale entry keeps its history: nothing is silently dropped.

NO AUTOMATIC PROMOTION
======================

An inferred or hypothetical entry is **never** rewritten into a confirmed one by
this module. There is no promotion method, no threshold, and no scoring. The only
way a conclusion can be *superseded* by a confirmed fact is:

* a different entry is added that is a ``CONFIRMED_FACT`` about the same subject,
  which marks the conclusion ``SUPERSEDED`` (the conclusion is not deleted, and
  its recorded ``kind`` and ``trust_level`` are never rewritten); or
* an operator explicitly records a promotion in ``promoted_by``, which
  :meth:`ProjectMemory.record` accepts only as an explicit, non-empty,
  caller-supplied attribution. ``kind`` and ``trust_level`` are still not
  rewritten: the promoted entry is a *new* entry linked to the old one.

A ``VERIFIED_CONCLUSION`` is therefore **always** ``INFERRED`` trust. That is
asserted, not merely intended: :meth:`MemoryRecord.__post_init__` refuses the
combination outright, so no caller can create one by accident.

FRESHNESS AND RENAMES
=====================

An entry that rests on a file pins that file's identity **and** fingerprint
(``EvidencePin``). Freshness is then decided by comparing the pinned fingerprint
with the fingerprint the scanner computes now:

``UNCHANGED``
    The file is present and its fingerprint is identical. The entry stays
    ``CURRENT``.

``CONTENT_CHANGED``
    The file is present with a different fingerprint. The entry becomes ``STALE``
    with reason ``source_content_changed``, and a ``STALE_ENTRY`` item is recorded
    naming the old and new fingerprints, so the change is attributable.

``DELETED``
    The pinned path no longer exists and no file in the workspace carries the
    pinned fingerprint. The entry becomes ``STALE`` with reason ``source_deleted``.

``RENAMED``
    The pinned path no longer exists, but **exactly one** file elsewhere in the
    workspace carries the identical fingerprint. That is recorded as a rename:
    reason ``source_renamed``, with the new path named.

The rename decision, stated once and tested:

    **A renamed file is NOT treated as still-current under the old path, and it is
    NOT treated as a brand-new unrelated file.** The entry is marked ``STALE``
    under the path it was obtained from, and the move is recorded as evidence.
    Because the fingerprint is identical, the same facts would hold at the new
    path -- but nothing here silently relocates an entry's identity, because a
    coincidentally identical file is not proof of a move, and the memory must not
    assert a path it has not verified. Re-obtaining the entry at the new path is
    an explicit act.

    If **two or more** candidate paths share the fingerprint, no rename is claimed
    at all: reason ``source_deleted``, with the ambiguous candidates recorded. An
    ambiguous move is reported as ambiguous, never resolved by picking one.

Keeping history
---------------

Nothing is deleted. Invalidation supersedes: the entry's ``status`` becomes
``STALE`` or ``INVALIDATED``, and its ``history`` gains an append-only tuple of
``MemoryHistoryEntry`` records (``recorded``, ``revalidated``, ``stale``,
``invalidated``, ``refresh_failed``, ``superseded``) with the time and reason. A
consumer can therefore always ask what an entry said, when, and why it changed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any

from app.context.models import ContextSourceType, ContextTrustLevel
from app.snapshots import SnapshotFile
from app.understanding.models import (
    InferredFact,
    StructuralFact,
    UnderstandingSnapshot,
)

__all__ = [
    "FreshnessOutcome",
    "MemoryHistoryEntry",
    "MemoryItemKind",
    "MemoryRecord",
    "MemoryRefreshResult",
    "MemoryScope",
    "MemoryStatus",
    "ProjectMemory",
    "ProjectMemoryStore",
    "STALE_REASON_*",
    "EvidencePin",
    "build_freshness_index",
    "freshness_of",
    "record_from_inferred_fact",
    "record_from_structural_fact",
    "record_unrecallable_file",
    "record_unknown",
    "snapshot_freshness_index",
]

#: Reason codes for a stale transition. Stable, machine-readable, and each names
#: the observation that produced it.
STALE_REASON_CONTENT_CHANGED = "source_content_changed"
STALE_REASON_DELETED = "source_deleted"
STALE_REASON_RENAMED = "source_renamed"
STALE_REASON_AMBIGUOUS_MOVE = "source_deleted_with_ambiguous_move_candidates"
STALE_REASON_EXPLICIT = "invalidated_explicitly"

#: The only trust level a conclusion may carry. Enforced in ``__post_init__``.
_CONCLUSION_TRUST = ContextTrustLevel.INFERRED

#: A total order over the trust levels this module may store, used to decide that a
#: contradiction is reported at the weaker of the two strengths and never upgraded.
_TRUST_RANK: Mapping[ContextTrustLevel, int] = {
    ContextTrustLevel.UNVERIFIED: 0,
    ContextTrustLevel.INFERRED: 1,
    ContextTrustLevel.CONFIRMED: 2,
    ContextTrustLevel.VERIFIED: 3,
}


class MemoryItemKind(str, Enum):
    """What sort of thing one memory entry is. See the module docstring."""

    CONFIRMED_FACT = "CONFIRMED_FACT"
    EXECUTION_RESULT = "EXECUTION_RESULT"
    VERIFIED_CONCLUSION = "VERIFIED_CONCLUSION"
    HYPOTHESIS = "HYPOTHESIS"
    STALE_ENTRY = "STALE_ENTRY"
    CONTRADICTION = "CONTRADICTION"
    UNKNOWN_DATA = "UNKNOWN_DATA"


class MemoryStatus(str, Enum):
    """The lifecycle position of one entry."""

    CURRENT = "CURRENT"
    STALE = "STALE"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class FreshnessOutcome(str, Enum):
    """What comparing an entry's pinned evidence against the workspace found."""

    UNPINNED = "UNPINNED"
    UNCHANGED = "UNCHANGED"
    CONTENT_CHANGED = "CONTENT_CHANGED"
    DELETED = "DELETED"
    RENAMED = "RENAMED"


#: Which trust levels each kind may carry. A kind outside the table may carry any
#: level, because no honesty claim is being made about it.
_ALLOWED_TRUST: Mapping[MemoryItemKind, frozenset[ContextTrustLevel]] = {
    MemoryItemKind.CONFIRMED_FACT: frozenset(
        {ContextTrustLevel.CONFIRMED, ContextTrustLevel.VERIFIED}
    ),
    MemoryItemKind.EXECUTION_RESULT: frozenset(
        {ContextTrustLevel.VERIFIED, ContextTrustLevel.CONFIRMED}
    ),
    MemoryItemKind.VERIFIED_CONCLUSION: frozenset({_CONCLUSION_TRUST}),
    MemoryItemKind.HYPOTHESIS: frozenset(
        {ContextTrustLevel.INFERRED, ContextTrustLevel.UNVERIFIED}
    ),
    MemoryItemKind.UNKNOWN_DATA: frozenset(
        {ContextTrustLevel.UNVERIFIED, ContextTrustLevel.INFERRED}
    ),
}

#: Kinds this module writes itself. A caller may not create them directly, because
#: a hand-written contradiction or stale marker is exactly the kind of unsupported
#: claim the memory exists to prevent.
DERIVED_KINDS = frozenset(
    {MemoryItemKind.STALE_ENTRY, MemoryItemKind.CONTRADICTION}
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(*parts: object) -> str:
    blob = json.dumps(
        [str(part) for part in parts], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryScope:
    """How widely an entry applies.

    Deliberately small and explicit. A scope is *not* a permission: it narrows
    what the entry is about, and nothing reads it to decide what may be executed.
    """

    project_id: str = ""
    paths: tuple[str, ...] = ()
    symbol: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.paths, (list, tuple)):
            object.__setattr__(
                self, "paths", tuple(sorted({str(path) for path in self.paths}))
            )

    def key(self) -> tuple[str, tuple[str, ...], str]:
        return (self.project_id, self.paths, self.symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "paths": list(self.paths),
            "symbol": self.symbol,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryScope":
        raw_paths = data.get("paths") or ()
        return cls(
            project_id=str(data.get("project_id", "")),
            paths=tuple(str(path) for path in raw_paths),
            symbol=str(data.get("symbol", "")),
            note=str(data.get("note", "")),
        )


@dataclass(frozen=True)
class EvidencePin:
    """The identity of the evidence an entry rests on, plus its fingerprint.

    ``fingerprint`` is the scanner's own SHA-256 of the file's bytes
    (``SnapshotFile.fingerprint``). No second hashing scheme is introduced: if the
    scanner could not fingerprint a file (a secret file, or an unreadable one) the
    pin carries ``None``, and an unpinnable entry can never be called current on
    the strength of a hash it does not have.
    """

    source_path: str = ""
    fingerprint: str | None = None
    extractor_id: str = ""
    run_id: str = ""
    execution_result_id: str = ""
    inference_id: str = ""

    @property
    def is_file_pinned(self) -> bool:
        return bool(self.source_path) and self.fingerprint is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "fingerprint": self.fingerprint,
            "extractor_id": self.extractor_id,
            "run_id": self.run_id,
            "execution_result_id": self.execution_result_id,
            "inference_id": self.inference_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvidencePin":
        fingerprint = data.get("fingerprint")
        return cls(
            source_path=str(data.get("source_path", "")),
            fingerprint=None if fingerprint is None else str(fingerprint),
            extractor_id=str(data.get("extractor_id", "")),
            run_id=str(data.get("run_id", "")),
            execution_result_id=str(data.get("execution_result_id", "")),
            inference_id=str(data.get("inference_id", "")),
        )


@dataclass(frozen=True)
class MemoryHistoryEntry:
    """One append-only change to an entry's lifecycle."""

    action: str
    recorded_at: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "recorded_at": self.recorded_at,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryHistoryEntry":
        return cls(
            action=str(data.get("action", "")),
            recorded_at=str(data.get("recorded_at", "")),
            detail=str(data.get("detail", "")),
        )


def _statement_digest(statement: str) -> str:
    return hashlib.sha256(statement.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryRecord:
    """One durable entry about this project, with its provenance and history."""

    memory_id: str
    kind: MemoryItemKind
    statement: str
    source: ContextSourceType
    source_id: str = ""
    obtained_at: str = ""
    scope: MemoryScope = field(default_factory=MemoryScope)
    evidence_fact_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    trust_level: ContextTrustLevel = ContextTrustLevel.INFERRED
    status: MemoryStatus = MemoryStatus.CURRENT
    pin: EvidencePin = field(default_factory=EvidencePin)
    claim_key: str = ""
    stale_reason: str = ""
    contradiction_with: tuple[str, ...] = ()
    supersedes: str = ""
    superseded_by: str = ""
    promoted_by: str = ""
    details: Mapping[str, Any] = field(default_factory=dict)
    history: tuple[MemoryHistoryEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.memory_id, str) or not self.memory_id.strip():
            raise ValueError("memory_id must be a non-empty string")
        if not isinstance(self.statement, str) or not self.statement.strip():
            raise ValueError("statement must be non-empty")
        if isinstance(self.evidence_fact_ids, (list, tuple)):
            object.__setattr__(
                self, "evidence_fact_ids", tuple(self.evidence_fact_ids)
            )
        if isinstance(self.evidence_ids, (list, tuple)):
            object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        if isinstance(self.contradiction_with, (list, tuple)):
            object.__setattr__(
                self, "contradiction_with", tuple(self.contradiction_with)
            )
        if isinstance(self.history, (list, tuple)):
            object.__setattr__(self, "history", tuple(self.history))
        if isinstance(self.details, dict):
            object.__setattr__(self, "details", dict(self.details))

        allowed = _ALLOWED_TRUST.get(self.kind)
        if allowed is not None and self.trust_level not in allowed:
            raise ValueError(
                f"a {self.kind.value} entry may only carry trust "
                f"{sorted(level.value for level in allowed)}, not "
                f"{self.trust_level.value}; an inference is never promoted to a "
                "confirmed fact automatically"
            )
        if self.kind is MemoryItemKind.CONFIRMED_FACT and not self.evidence_fact_ids:
            raise ValueError(
                "a CONFIRMED_FACT must cite at least one evidence_fact_id; a "
                "confirmed fact without cited evidence is not a confirmed fact"
            )
        if self.kind is MemoryItemKind.EXECUTION_RESULT and not (
            self.pin.run_id or self.pin.execution_result_id or self.evidence_ids
        ):
            raise ValueError(
                "an EXECUTION_RESULT must name the run or execution result it "
                "observed; an unsourced result is not an observed result"
            )

    # -- identity -------------------------------------------------------
    @property
    def subject_key(self) -> str:
        """What this entry is *about*, used to find contradictions.

        An explicitly supplied ``claim_key`` wins; otherwise the subject is the
        entry's kind, scope and pinned source, which is what makes two entries
        about the same file comparable.
        """
        if self.claim_key:
            return self.claim_key
        return _digest(
            self.kind.value,
            self.scope.key(),
            self.pin.source_path,
        )

    @property
    def statement_digest(self) -> str:
        return _statement_digest(self.statement)

    @property
    def is_current(self) -> bool:
        return self.status is MemoryStatus.CURRENT

    def with_history(self, action: str, detail: str = "") -> "MemoryRecord":
        """Return a copy with one appended history entry. Original untouched."""
        return replace(
            self,
            history=self.history
            + (MemoryHistoryEntry(action=action, recorded_at=_iso_now(), detail=detail),),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "kind": self.kind.value,
            "statement": self.statement,
            "source": self.source.value,
            "source_id": self.source_id,
            "obtained_at": self.obtained_at,
            "scope": self.scope.to_dict(),
            "evidence_fact_ids": list(self.evidence_fact_ids),
            "evidence_ids": list(self.evidence_ids),
            "trust_level": self.trust_level.value,
            "status": self.status.value,
            "pin": self.pin.to_dict(),
            "claim_key": self.claim_key,
            "stale_reason": self.stale_reason,
            "contradiction_with": list(self.contradiction_with),
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "promoted_by": self.promoted_by,
            "details": dict(self.details),
            "history": [entry.to_dict() for entry in self.history],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MemoryRecord":
        if not isinstance(data, Mapping):
            raise ValueError("a saved memory record must be a mapping")
        try:
            kind = MemoryItemKind(str(data.get("kind", "")))
            status = MemoryStatus(str(data.get("status", MemoryStatus.CURRENT.value)))
            source = ContextSourceType(str(data.get("source", "")))
            trust = ContextTrustLevel(str(data.get("trust_level", "")))
        except ValueError as exc:
            raise ValueError(f"saved memory record has an unknown field: {exc}") from exc
        raw_evidence = data.get("evidence_fact_ids") or ()
        raw_ids = data.get("evidence_ids") or ()
        raw_conflicts = data.get("contradiction_with") or ()
        raw_history = data.get("history") or ()
        scope = data.get("scope")
        pin = data.get("pin")
        return cls(
            memory_id=str(data.get("memory_id", "")),
            kind=kind,
            statement=str(data.get("statement", "")),
            source=source,
            source_id=str(data.get("source_id", "")),
            obtained_at=str(data.get("obtained_at", "")),
            scope=MemoryScope.from_dict(scope) if isinstance(scope, Mapping) else MemoryScope(),
            evidence_fact_ids=tuple(str(item) for item in raw_evidence),
            evidence_ids=tuple(str(item) for item in raw_ids),
            trust_level=trust,
            status=status,
            pin=EvidencePin.from_dict(pin) if isinstance(pin, Mapping) else EvidencePin(),
            claim_key=str(data.get("claim_key", "")),
            stale_reason=str(data.get("stale_reason", "")),
            contradiction_with=tuple(str(item) for item in raw_conflicts),
            supersedes=str(data.get("supersedes", "")),
            superseded_by=str(data.get("superseded_by", "")),
            promoted_by=str(data.get("promoted_by", "")),
            details=dict(data.get("details") or {}),
            history=tuple(
                MemoryHistoryEntry.from_dict(entry)
                for entry in raw_history
                if isinstance(entry, Mapping)
            ),
        )


# ---------------------------------------------------------------------------
# freshness
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FreshnessIndex:
    """The workspace's current file fingerprints, indexed for freshness checks."""

    by_path: Mapping[str, str | None]
    paths_by_fingerprint: Mapping[str, tuple[str, ...]]

    @classmethod
    def build(cls, files: Sequence[SnapshotFile]) -> "FreshnessIndex":
        by_path: dict[str, str | None] = {}
        for entry in files:
            by_path[entry.relative_path] = entry.fingerprint
        grouped: dict[str, list[str]] = {}
        for path, fingerprint in by_path.items():
            if fingerprint is None:
                continue
            grouped.setdefault(fingerprint, []).append(path)
        return cls(
            by_path=dict(by_path),
            paths_by_fingerprint={
                fingerprint: tuple(sorted(paths))
                for fingerprint, paths in grouped.items()
            },
        )


def snapshot_freshness_index(snapshot: UnderstandingSnapshot) -> FreshnessIndex:
    """The freshness index of an understanding snapshot, using its own files.

    Reuses ``UnderstandingSnapshot.files`` and their ``fingerprint`` values; the
    scanner computed those already, so nothing is re-hashed.
    """
    return FreshnessIndex.build(snapshot.files)


def build_freshness_index(workspace: Any) -> FreshnessIndex:
    """Scan a workspace through the existing bounded scanner, or refuse.

    Imported lazily so this module stays importable without the scanner's
    dependencies, and so there is exactly one scanner in the process.
    """
    from app.understanding.scanner import BoundedProjectScanner

    files, _facts, _manifests, _warnings = BoundedProjectScanner().scan(workspace)
    return FreshnessIndex.build(files)


@dataclass(frozen=True)
class FreshnessVerdict:
    """The freshness of one entry, with the evidence that decided it."""

    memory_id: str
    outcome: FreshnessOutcome
    reason: str = ""
    pinned_path: str = ""
    pinned_fingerprint: str | None = None
    observed_fingerprint: str | None = None
    renamed_to: str = ""
    candidates: tuple[str, ...] = ()

    @property
    def is_fresh(self) -> bool:
        return self.outcome in {FreshnessOutcome.UNCHANGED, FreshnessOutcome.UNPINNED}

    def to_dict(self) -> dict[str, Any]:
        return {
            "memory_id": self.memory_id,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "pinned_path": self.pinned_path,
            "pinned_fingerprint": self.pinned_fingerprint,
            "observed_fingerprint": self.observed_fingerprint,
            "renamed_to": self.renamed_to,
            "candidates": list(self.candidates),
        }


def freshness_of(record: MemoryRecord, index: FreshnessIndex) -> FreshnessVerdict:
    """Decide one entry's freshness against a workspace index.

    See the module docstring for the rename decision and why an ambiguous move is
    reported as ambiguous instead of being resolved.
    """
    pin = record.pin
    if not pin.is_file_pinned:
        return FreshnessVerdict(
            memory_id=record.memory_id,
            outcome=FreshnessOutcome.UNPINNED,
            pinned_path=pin.source_path,
            pinned_fingerprint=pin.fingerprint,
        )
    observed = index.by_path.get(pin.source_path)
    if pin.source_path in index.by_path and observed == pin.fingerprint:
        return FreshnessVerdict(
            memory_id=record.memory_id,
            outcome=FreshnessOutcome.UNCHANGED,
            pinned_path=pin.source_path,
            pinned_fingerprint=pin.fingerprint,
            observed_fingerprint=observed,
        )
    if pin.source_path in index.by_path:
        return FreshnessVerdict(
            memory_id=record.memory_id,
            outcome=FreshnessOutcome.CONTENT_CHANGED,
            reason=STALE_REASON_CONTENT_CHANGED,
            pinned_path=pin.source_path,
            pinned_fingerprint=pin.fingerprint,
            observed_fingerprint=observed,
        )
    candidates = tuple(
        path
        for path in index.paths_by_fingerprint.get(pin.fingerprint or "", ())
        if path != pin.source_path
    )
    if len(candidates) == 1:
        return FreshnessVerdict(
            memory_id=record.memory_id,
            outcome=FreshnessOutcome.RENAMED,
            reason=STALE_REASON_RENAMED,
            pinned_path=pin.source_path,
            pinned_fingerprint=pin.fingerprint,
            renamed_to=candidates[0],
            candidates=candidates,
        )
    if len(candidates) > 1:
        return FreshnessVerdict(
            memory_id=record.memory_id,
            outcome=FreshnessOutcome.DELETED,
            reason=STALE_REASON_AMBIGUOUS_MOVE,
            pinned_path=pin.source_path,
            pinned_fingerprint=pin.fingerprint,
            candidates=candidates,
        )
    return FreshnessVerdict(
        memory_id=record.memory_id,
        outcome=FreshnessOutcome.DELETED,
        reason=STALE_REASON_DELETED,
        pinned_path=pin.source_path,
        pinned_fingerprint=pin.fingerprint,
    )


# ---------------------------------------------------------------------------
# the memory document
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryRefreshResult:
    """What a refresh found and did. Nothing here is a permission."""

    checked: int = 0
    unchanged: int = 0
    stale: int = 0
    renamed: int = 0
    deleted: int = 0
    contradictions: int = 0
    verdicts: tuple[FreshnessVerdict, ...] = ()
    stale_memory_ids: tuple[str, ...] = ()
    contradiction_ids: tuple[str, ...] = ()
    refreshed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "unchanged": self.unchanged,
            "stale": self.stale,
            "renamed": self.renamed,
            "deleted": self.deleted,
            "contradictions": self.contradictions,
            "stale_memory_ids": list(self.stale_memory_ids),
            "contradiction_ids": list(self.contradiction_ids),
            "refreshed_at": self.refreshed_at,
            "verdicts": [verdict.to_dict() for verdict in self.verdicts],
        }


@dataclass
class ProjectMemory:
    """An ordered, append-only collection of memory entries about one project.

    Every mutation returns a **new** :class:`ProjectMemory`; nothing is edited in
    place and nothing is deleted. That is what keeps history honest and makes the
    document safe to persist atomically through ``RunStore``.
    """

    project_id: str = ""
    records: tuple[MemoryRecord, ...] = ()
    updated_at: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.records, (list, tuple)):
            object.__setattr__(self, "records", tuple(self.records))

    # -- reads ----------------------------------------------------------
    def by_id(self, memory_id: str) -> MemoryRecord | None:
        for record in self.records:
            if record.memory_id == memory_id:
                return record
        return None

    def current(self, *, kind: MemoryItemKind | None = None) -> tuple[MemoryRecord, ...]:
        return tuple(
            record
            for record in self.records
            if record.is_current and (kind is None or record.kind is kind)
        )

    def of_kind(self, kind: MemoryItemKind) -> tuple[MemoryRecord, ...]:
        return tuple(record for record in self.records if record.kind is kind)

    def stale(self) -> tuple[MemoryRecord, ...]:
        return tuple(
            record
            for record in self.records
            if record.status in {MemoryStatus.STALE, MemoryStatus.INVALIDATED}
        )

    # -- writes ---------------------------------------------------------
    def record(self, entry: MemoryRecord) -> "ProjectMemory":
        """Add an entry, refusing a hand-written derived kind.

        ``STALE_ENTRY`` and ``CONTRADICTION`` are produced by this module's own
        evaluation, never asserted by a caller, because a hand-written
        contradiction is exactly the unsupported claim the memory exists to
        prevent.
        """
        if entry.kind in DERIVED_KINDS:
            raise ValueError(
                f"a {entry.kind.value} entry is produced by memory evaluation and "
                "may not be recorded directly"
            )
        if self.by_id(entry.memory_id) is not None:
            raise ValueError(f"memory id already exists: {entry.memory_id!r}")
        stored = replace(
            entry,
            obtained_at=entry.obtained_at or _iso_now(),
        ).with_history("recorded", f"kind={entry.kind.value}")
        return replace(
            self,
            records=self.records + (stored,),
            updated_at=_iso_now(),
        )

    def invalidate(self, memory_id: str, *, reason: str) -> "ProjectMemory":
        """Explicitly invalidate one entry, keeping its full history."""
        updated: list[MemoryRecord] = []
        found = False
        for record in self.records:
            if record.memory_id != memory_id:
                updated.append(record)
                continue
            found = True
            updated.append(
                replace(
                    record,
                    status=MemoryStatus.INVALIDATED,
                    stale_reason=reason,
                ).with_history("invalidated", reason)
            )
        if not found:
            raise KeyError(memory_id)
        return replace(self, records=tuple(updated), updated_at=_iso_now())

    def supersede(self, memory_id: str, *, by: str, reason: str) -> "ProjectMemory":
        """Mark one entry superseded by another. The entry itself is untouched."""
        updated: list[MemoryRecord] = []
        found = False
        for record in self.records:
            if record.memory_id != memory_id:
                updated.append(record)
                continue
            found = True
            updated.append(
                replace(
                    record,
                    status=MemoryStatus.SUPERSEDED,
                    superseded_by=by,
                ).with_history("superseded", f"by={by} {reason}".strip())
            )
        if not found:
            raise KeyError(memory_id)
        return replace(self, records=tuple(updated), updated_at=_iso_now())

    # -- evaluation -----------------------------------------------------
    def refresh(
        self, index: FreshnessIndex, *, now: str | None = None
    ) -> tuple["ProjectMemory", MemoryRefreshResult]:
        """Re-evaluate every current file-pinned entry against a workspace index.

        Returns the new memory document and what was found. Stale transitions write
        a ``STALE_ENTRY`` item that names the entry it replaced, its old and new
        fingerprints, and -- for a rename -- the path the content moved to.
        """
        refreshed_at = now or _iso_now()
        verdicts: list[FreshnessVerdict] = []
        records: list[MemoryRecord] = list(self.records)
        stale_ids: list[str] = []
        unchanged = 0
        renamed = 0
        deleted = 0

        for record in self.records:
            if not record.is_current:
                continue
            if record.kind in DERIVED_KINDS:
                continue
            verdict = freshness_of(record, index)
            if verdict.outcome is FreshnessOutcome.UNPINNED:
                continue
            verdicts.append(verdict)
            if verdict.outcome is FreshnessOutcome.UNCHANGED:
                unchanged += 1
                records[records.index(record)] = record.with_history(
                    "revalidated", f"fingerprint={verdict.observed_fingerprint}"
                )
                continue

            detail = verdict.reason
            if verdict.outcome is FreshnessOutcome.RENAMED:
                renamed += 1
                detail = (
                    f"{STALE_REASON_RENAMED}:{verdict.pinned_path}->{verdict.renamed_to}"
                )
            elif verdict.outcome is FreshnessOutcome.DELETED:
                deleted += 1
                detail = verdict.reason + (
                    ":candidates=" + ",".join(verdict.candidates)
                    if verdict.candidates
                    else ""
                )
            stale_ids.append(record.memory_id)
            records[records.index(record)] = replace(
                record,
                status=MemoryStatus.STALE,
                stale_reason=verdict.reason,
            ).with_history("stale", detail)
            records.append(
                MemoryRecord(
                    memory_id=f"stale-{_digest(record.memory_id, detail)[:16]}",
                    kind=MemoryItemKind.STALE_ENTRY,
                    statement=(
                        f"entry {record.memory_id} ({record.kind.value}) is no longer "
                        f"current: {detail}"
                    ),
                    source=ContextSourceType.PROJECT_UNDERSTANDING,
                    source_id=record.memory_id,
                    obtained_at=refreshed_at,
                    scope=record.scope,
                    evidence_fact_ids=record.evidence_fact_ids,
                    evidence_ids=(record.memory_id,) + record.evidence_ids,
                    trust_level=record.trust_level,
                    pin=record.pin,
                    supersedes=record.memory_id,
                    details={
                        "pinned_path": verdict.pinned_path,
                        "pinned_fingerprint": verdict.pinned_fingerprint,
                        "observed_fingerprint": verdict.observed_fingerprint,
                        "renamed_to": verdict.renamed_to,
                        "candidates": list(verdict.candidates),
                        "reason": verdict.reason,
                    },
                ).with_history("recorded", "kind=STALE_ENTRY")
            )

        document = replace(self, records=tuple(records), updated_at=refreshed_at)
        document, contradictions = document.detect_contradictions(now=refreshed_at)
        return document, MemoryRefreshResult(
            checked=len(verdicts),
            unchanged=unchanged,
            stale=len(stale_ids),
            renamed=renamed,
            deleted=deleted,
            contradictions=len(contradictions),
            verdicts=tuple(verdicts),
            stale_memory_ids=tuple(stale_ids),
            contradiction_ids=contradictions,
            refreshed_at=refreshed_at,
        )

    def detect_contradictions(
        self, *, now: str | None = None
    ) -> tuple["ProjectMemory", tuple[str, ...]]:
        """Record every pair of current entries that contradict each other.

        Two current entries contradict when they share a ``subject_key`` and their
        statement digests differ. The pair is recorded once as a single
        ``CONTRADICTION`` entry; neither side is deleted, neither side is
        rewritten, and neither side's ``trust_level`` is raised. The contradiction
        entry carries the **weaker** of the two trust levels, because an
        unresolved conflict cannot be stronger than its less-supported side.

        Idempotent: a contradiction already recorded for the same pair is not
        recorded twice.
        """
        detected_at = now or _iso_now()
        grouped: dict[str, list[MemoryRecord]] = {}
        for record in self.records:
            if not record.is_current or record.kind in DERIVED_KINDS:
                continue
            grouped.setdefault(record.subject_key, []).append(record)

        existing_pairs = {
            tuple(sorted((entry.details.get("left", ""), entry.details.get("right", ""))))
            for entry in self.of_kind(MemoryItemKind.CONTRADICTION)
        }
        added: list[MemoryRecord] = []
        added_ids: list[str] = []
        for subject, entries in sorted(grouped.items()):
            digests = {entry.statement_digest for entry in entries}
            if len(digests) < 2:
                continue
            ordered = sorted(entries, key=lambda entry: entry.memory_id)
            for left_index in range(len(ordered)):
                for right_index in range(left_index + 1, len(ordered)):
                    left, right = ordered[left_index], ordered[right_index]
                    if left.statement_digest == right.statement_digest:
                        continue
                    pair = tuple(sorted((left.memory_id, right.memory_id)))
                    if pair in existing_pairs:
                        continue
                    existing_pairs.add(pair)
                    weaker = min(
                        (left.trust_level, right.trust_level),
                        key=lambda level: _TRUST_RANK.get(level, 0),
                    )
                    memory_id = f"contradiction-{_digest(subject, *pair)[:16]}"
                    added.append(
                        MemoryRecord(
                            memory_id=memory_id,
                            kind=MemoryItemKind.CONTRADICTION,
                            statement=(
                                f"contradictory claims about {subject[:12]}: "
                                f"{left.memory_id} says {left.statement!r}; "
                                f"{right.memory_id} says {right.statement!r}"
                            ),
                            source=ContextSourceType.PROJECT_UNDERSTANDING,
                            source_id=pair[0],
                            obtained_at=detected_at,
                            scope=left.scope,
                            evidence_ids=pair,
                            trust_level=weaker,
                            contradiction_with=pair,
                            details={
                                "left": pair[0],
                                "right": pair[1],
                                "subject_key": subject,
                            },
                        ).with_history("recorded", "kind=CONTRADICTION")
                    )
                    added_ids.append(memory_id)
        if not added:
            return self, ()
        return (
            replace(
                self,
                records=self.records + tuple(added),
                updated_at=detected_at,
            ),
            tuple(added_ids),
        )

    # -- codec ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PROJECT_MEMORY_SCHEMA,
            "project_id": self.project_id,
            "updated_at": self.updated_at,
            "items": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectMemory":
        if not isinstance(data, Mapping):
            raise ValueError("a saved project memory must be a mapping")
        schema = str(data.get("schema", ""))
        if schema != PROJECT_MEMORY_SCHEMA:
            raise ValueError(f"unsupported project memory schema: {schema!r}")
        raw_items = data.get("items") or ()
        if not isinstance(raw_items, (list, tuple)):
            raise ValueError("saved project memory has a non-sequence items member")
        return cls(
            project_id=str(data.get("project_id", "")),
            records=tuple(
                MemoryRecord.from_dict(entry)
                for entry in raw_items
                if isinstance(entry, Mapping)
            ),
            updated_at=str(data.get("updated_at", "")),
        )


#: The persisted document version, next to the codec that reads it.
PROJECT_MEMORY_SCHEMA = "forge.project_memory.v1"

#: The ``project_state`` key the memory document occupies. It rides inside the
#: existing ``RunStore`` snapshot, so there is no second storage backend.
PROJECT_MEMORY_KEY = "project_memory"


class ProjectMemoryStore:
    """Persist the memory document through the existing ``RunStore``.

    A thin codec over ``RunStateSnapshot.project_state``: it merges into whatever
    else that snapshot holds, so writing memory can never damage a step-graph or
    recovery record stored beside it. The store is deliberately **not** an
    authority: loading memory grants nothing, and nothing in this class can
    dispatch, approve or widen anything.
    """

    def __init__(self, store: Any, *, memory_id: str = "project-memory") -> None:
        from app.runtime.run_store import RunStore

        if not isinstance(store, RunStore):
            raise TypeError("ProjectMemoryStore requires a RunStore")
        if not memory_id or not memory_id.strip():
            raise ValueError("memory_id must be a non-empty run id")
        self._store = store
        self._memory_id = memory_id.strip()

    @property
    def memory_id(self) -> str:
        return self._memory_id

    def load(self) -> ProjectMemory:
        """Read the document, or an empty one. Never raises on a damaged document."""
        snapshot = self._store.read_state(self._memory_id)
        if snapshot is None or not snapshot.project_state:
            return ProjectMemory()
        payload = snapshot.project_state.get(PROJECT_MEMORY_KEY)
        if not isinstance(payload, Mapping):
            return ProjectMemory()
        try:
            return ProjectMemory.from_dict(payload)
        except ValueError:
            # A damaged document is reported as empty rather than guessed at. The
            # raw bytes stay on disk untouched, so nothing is destroyed.
            return ProjectMemory()

    def save(self, memory: ProjectMemory) -> None:
        from app.runtime.run_store import RunStateSnapshot

        snapshot = self._store.read_state(self._memory_id)
        project_state: dict[str, Any] = (
            dict(snapshot.project_state)
            if snapshot is not None and snapshot.project_state
            else {}
        )
        project_state[PROJECT_MEMORY_KEY] = memory.to_dict()
        self._store.write_state(
            RunStateSnapshot(
                run_id=self._memory_id,
                task_id=snapshot.task_id if snapshot else "",
                state=snapshot.state if snapshot else "",
                status=snapshot.status if snapshot else "",
                attempt_number=snapshot.attempt_number if snapshot else 0,
                completed_attempt_indexes=(
                    snapshot.completed_attempt_indexes if snapshot else ()
                ),
                project_state=project_state,
                updated_at=_iso_now(),
            )
        )


# ---------------------------------------------------------------------------
# recording from the understanding layer
# ---------------------------------------------------------------------------


def record_from_structural_fact(
    fact: StructuralFact,
    *,
    project_id: str = "",
    fingerprint: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    claim_key: str = "",
) -> MemoryRecord:
    """Turn one ``StructuralFact`` into a ``CONFIRMED_FACT`` memory entry.

    The fact id is the evidence; the source path and the scanner's fingerprint are
    the pin. No new provenance scheme: ``evidence_fact_ids`` is the field the
    understanding layer already uses.
    """
    statement = _structural_statement(fact)
    return MemoryRecord(
        memory_id=f"fact-{fact.fact_id}",
        kind=MemoryItemKind.CONFIRMED_FACT,
        statement=statement,
        source=ContextSourceType.PROJECT_UNDERSTANDING,
        source_id=fact.fact_id,
        scope=MemoryScope(
            project_id=project_id,
            paths=(fact.source_path,),
            symbol=str(fact.details.get("symbol_name", "") or ""),
        ),
        evidence_fact_ids=(fact.fact_id,),
        evidence_ids=evidence_ids,
        trust_level=ContextTrustLevel.CONFIRMED,
        pin=EvidencePin(
            source_path=fact.source_path,
            fingerprint=fingerprint,
            extractor_id=fact.extractor_id,
        ),
        claim_key=claim_key,
        details={
            "fact_type": fact.fact_type.value,
            "line_start": fact.line_start,
            "line_end": fact.line_end,
        },
    )


def _structural_statement(fact: StructuralFact) -> str:
    """A bounded, deterministic statement for one structural fact.

    Deliberately built from identifiers and the fact type, never from file
    contents: a memory statement must not become a channel for source text, and
    the fact's own ``details`` are already the bounded projection.
    """
    parts = [f"{fact.fact_type.value} in {fact.source_path}"]
    symbol = str(fact.details.get("symbol_name", "") or fact.details.get("module_name", ""))
    if symbol:
        parts.append(f"name={symbol}")
    if fact.line_start or fact.line_end:
        parts.append(f"lines={fact.line_start}-{fact.line_end}")
    return " ".join(parts)


def record_from_inferred_fact(
    inference: InferredFact,
    *,
    project_id: str = "",
    source_path: str = "",
    fingerprint: str | None = None,
    kind: MemoryItemKind | None = None,
    claim_key: str = "",
) -> MemoryRecord:
    """Turn one ``InferredFact`` into memory, at ``INFERRED`` trust.

    The kind is chosen from the inference's own recorded ``trust_level`` and never
    upgrades it:

    * an inference that cites at least one evidence fact is a
      ``VERIFIED_CONCLUSION`` -- trust stays ``INFERRED``, and the constructor
      refuses anything stronger;
    * an inference that cites nothing is a ``HYPOTHESIS``.

    An explicit ``kind`` may be supplied, but it may not turn a conclusion into a
    ``CONFIRMED_FACT``: the constructor's per-kind trust rule refuses that.
    """
    inferred_kind = kind
    if inferred_kind is None:
        inferred_kind = (
            MemoryItemKind.VERIFIED_CONCLUSION
            if inference.evidence_fact_ids
            else MemoryItemKind.HYPOTHESIS
        )
    return MemoryRecord(
        memory_id=f"inference-{inference.inference_id}",
        kind=inferred_kind,
        statement=inference.statement,
        source=ContextSourceType.PROJECT_UNDERSTANDING,
        source_id=inference.inference_id,
        scope=MemoryScope(project_id=project_id, paths=(source_path,) if source_path else ()),
        evidence_fact_ids=inference.evidence_fact_ids,
        trust_level=ContextTrustLevel.INFERRED,
        pin=EvidencePin(
            source_path=source_path,
            fingerprint=fingerprint,
            inference_id=inference.inference_id,
        ),
        claim_key=claim_key,
        details={"category": inference.category, "model_id": inference.model_id},
    )


def record_execution_result(
    *,
    statement: str,
    run_id: str,
    execution_result_id: str = "",
    outcome: str = "",
    exit_code: int | None = None,
    verification_status: str = "",
    project_id: str = "",
    source_path: str = "",
    fingerprint: str | None = None,
    evidence_ids: tuple[str, ...] = (),
    claim_key: str = "",
) -> MemoryRecord:
    """Record one observed execution outcome at ``VERIFIED`` trust.

    The statement is supplied by the caller because this module will not invent a
    summary of someone else's result; what it enforces is that the entry names the
    run or result it observed, and that it never carries a bare claim with no
    execution behind it.
    """
    return MemoryRecord(
        memory_id=f"exec-{_digest(run_id, execution_result_id, statement)[:16]}",
        kind=MemoryItemKind.EXECUTION_RESULT,
        statement=statement,
        source=ContextSourceType.RUN_TRACE,
        source_id=execution_result_id or run_id,
        scope=MemoryScope(project_id=project_id, paths=(source_path,) if source_path else ()),
        evidence_ids=evidence_ids or ((execution_result_id or run_id),),
        trust_level=ContextTrustLevel.VERIFIED,
        pin=EvidencePin(
            source_path=source_path,
            fingerprint=fingerprint,
            run_id=run_id,
            execution_result_id=execution_result_id,
        ),
        claim_key=claim_key,
        details={
            "outcome": outcome,
            "exit_code": exit_code,
            "verification_status": verification_status,
        },
    )


def record_unknown(
    *,
    statement: str,
    project_id: str = "",
    source_path: str = "",
    claim_key: str = "",
    details: Mapping[str, Any] | None = None,
) -> MemoryRecord:
    """Record something the project does not know, at ``UNVERIFIED`` trust.

    Unknown data is stored on purpose. An absence that is not recorded is
    indistinguishable from an absence that was never looked for.
    """
    return MemoryRecord(
        memory_id=f"unknown-{_digest(statement, source_path, project_id)[:16]}",
        kind=MemoryItemKind.UNKNOWN_DATA,
        statement=statement,
        source=ContextSourceType.PROJECT_UNDERSTANDING,
        source_id=source_path,
        scope=MemoryScope(project_id=project_id, paths=(source_path,) if source_path else ()),
        trust_level=ContextTrustLevel.UNVERIFIED,
        pin=EvidencePin(source_path=source_path),
        claim_key=claim_key,
        details=dict(details or {}),
    )


def record_unrecallable_file(
    relative_path: str,
    *,
    project_id: str = "",
    reason: str = "the bounded scanner did not fingerprint this file",
) -> MemoryRecord:
    """Record that a file exists but its contents were deliberately not recalled.

    This is how the memory states an absence instead of merely having one. The
    entry carries the **path** and the **reason**, and never a fingerprint, a
    digest, a length, or any content: for a credential file, a digest would itself
    be a recorded fact about credential material, which memory has no business
    holding.

    The reader's consequence is deliberate: an entry of this kind is unpinned, so
    freshness evaluation can never report it as current on the strength of a hash
    it does not have, and it can never be mistaken for a confirmed structural fact.
    """
    return MemoryRecord(
        memory_id=f"unrecallable-{_digest(relative_path, project_id)[:16]}",
        kind=MemoryItemKind.UNKNOWN_DATA,
        statement=(
            f"the contents of {relative_path} were not recalled: {reason}"
        ),
        source=ContextSourceType.PROJECT_UNDERSTANDING,
        source_id=relative_path,
        scope=MemoryScope(project_id=project_id, paths=(relative_path,)),
        trust_level=ContextTrustLevel.UNVERIFIED,
        pin=EvidencePin(source_path=relative_path, fingerprint=None),
        claim_key=f"unrecallable:{relative_path}",
        details={"relative_path": relative_path, "reason": reason},
    )


def memory_from_snapshot(
    snapshot: UnderstandingSnapshot,
    *,
    include_inferred: Iterable[InferredFact] = (),
    include_unrecallable: bool = True,
) -> ProjectMemory:
    """Seed memory from one understanding snapshot, without inventing anything.

    Structural facts become ``CONFIRMED_FACT`` entries pinned to the scanner's own
    fingerprints. Inferred facts, when supplied, become conclusions or hypotheses
    at ``INFERRED`` trust. Files the scan recorded but could not fingerprint -- the
    secret files the scanner deliberately does not read, and any unreadable file --
    become ``UNKNOWN_DATA`` entries that name the path and nothing else, so the
    absence is recorded rather than merely present. Nothing is promoted and nothing
    is guessed.
    """
    fingerprints = {
        entry.relative_path: entry.fingerprint for entry in snapshot.files
    }
    memory = ProjectMemory(project_id=snapshot.project_id, updated_at=_iso_now())
    if include_unrecallable:
        for entry in snapshot.files:
            if entry.fingerprint is None:
                memory = memory.record(
                    record_unrecallable_file(
                        entry.relative_path, project_id=snapshot.project_id
                    )
                )
    for fact in snapshot.facts:
        memory = memory.record(
            record_from_structural_fact(
                fact,
                project_id=snapshot.project_id,
                fingerprint=fingerprints.get(fact.source_path),
            )
        )
    for inference in include_inferred:
        memory = memory.record(
            record_from_inferred_fact(
                inference,
                project_id=snapshot.project_id,
            )
        )
    return memory
