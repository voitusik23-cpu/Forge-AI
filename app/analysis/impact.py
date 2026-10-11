"""Change Impact Engine: what changed, what depends on it, and on what basis.

WHAT THIS MODULE IS
===================

The reasoning half of the Block #8 change-impact report. It answers the question
the Block #8 report only partly answered: **which parts of the project may be
affected by an observed change, and why**. It does so by comparing data the
server already holds and by naming the exact evidence behind every statement:

* ``app/snapshots.SnapshotFile.fingerprint`` -- the scanner's own SHA-256 of the
  bytes, for the baseline snapshot and for the snapshot taken now;
* ``app.understanding.models.StructuralFact`` -- the structural facts the
  scanners extracted, per source path;
* ``app.understanding.models.ProjectTopology`` -- the ``TopologyNode`` /
  ``TopologyEdge`` records that cite those fact ids, which is how a *dependent*
  node is named rather than guessed;
* ``app/understanding.models.ManifestDescriptor`` -- declared manifests, which is
  how a change is linked to a declared manifest;
* ``AcceptanceSpec`` / ``AcceptanceCriterion`` / ``VerificationExpectation``
  (``app.agent_runtime/acceptance_spec.py``) -- the operator's own declared
  acceptance expectations, which is how a check is linked to a changed path;
* ``ProjectMemory`` (``app/understanding/memory.py``) -- the entries pinned to a
  changed path, and the entries whose ``evidence_fact_ids`` cite a fact that is
  no longer current.

WHAT IT DELIBERATELY DOES NOT DO
================================

* It adds **no** second fact registry, topology graph, manifest reader, or memory
  store. Facts come from ``UnderstandingSnapshot.facts``; dependencies come from
  that snapshot's own topology; staleness is decided by
  ``app.understanding.memory.freshness_of``; the invalidation vocabulary is
  ``memory.py``'s own codes.
* It adds **no** auto-promotion. There is no code path here that raises a
  hypothesis to a confirmed fact, and no path that rewrites a memory record.
* It resolves **no** ambiguous move. Two files with identical bytes are
  indistinguishable from a move, so a single candidate is reported as
  ``probable_rename_to`` (``PROBABLE``) and several candidates are reported as
  ``ambiguous_move`` (``UNKNOWN``) with the candidate list retained. The engine
  never picks one.
* It returns **no** file contents and calls **no** model.

HOW A CLASSIFICATION IS DECIDED
===============================

Every finding carries a ``classification`` (``CONFIRMED`` / ``PROBABLE`` /
``UNKNOWN``) **and** the list of ``basis`` items that produced it, each naming a
concrete observation and the identifier it came from. :func:`classify_basis` is
the single place that maps basis to classification, and its rule is deliberately
boring:

* ``CONFIRMED`` requires at least one structural basis: a fingerprint mismatch or
  absence measured by the scanner itself, or a structural fact extracted from
  the path. These are observations, not inferences;
* otherwise, with at least one weak basis (an edge that *cites* a fact of the
  changed file, a manifest that is the changed path, a declared-expectation path
  match, a memory entry pinned to the path, a test file whose name relates to the
  changed path), the finding is ``PROBABLE``: a real, cited relationship that is
  not by itself proof the dependent thing is affected;
* with no basis at all the finding is ``UNKNOWN``. **An empty basis never
  classifies as CONFIRMED**, which is the point of routing every finding through
  one function.

A ``support_rank`` is also reported. It is **not** a confidence and is not used as
one: it is the count of *distinct* basis kinds present, so a reader can see how
many independent observations exist without being handed a number that looks like
a probability. Rank never changes the classification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from app.understanding.memory import (
    FreshnessIndex,
    MemoryStatus,
    ProjectMemory,
    freshness_of,
)

__all__ = [
    "BasisItem",
    "BasisKind",
    "ChangeFinding",
    "ChangeImpactEngine",
    "ChangeImpactReport",
    "ChangeKind",
    "DependentConclusion",
    "DependencyLink",
    "FindingClassification",
    "MAX_ITEMS_PER_CHANGE",
    "STRUCTURAL_BASIS",
    "StaleFact",
    "classify_basis",
    "support_rank",
]


class ChangeKind(str, Enum):
    """What happened to one path between the baseline and now.

    ``RENAMED`` is deliberately absent as an established kind. A path either
    exists at baseline and not now (``DELETED``, possibly with a probable
    destination), exists now and not at baseline (``ADDED``, possibly itself the
    destination of a probable rename), or exists in both with different bytes
    (``MODIFIED``). "A rename happened" is an inference about a *pair*, so it is
    reported on the pair as ``probable_rename_to`` / ``ambiguous_move`` rather
    than asserted as a kind of a single path.
    """

    ADDED = "ADDED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"


class FindingClassification(str, Enum):
    """How well established one finding is, and never more than its basis."""

    CONFIRMED = "CONFIRMED"
    PROBABLE = "PROBABLE"
    UNKNOWN = "UNKNOWN"


class BasisKind(str, Enum):
    """One concrete observation a finding rests on.

    The split between *structural* and *weak* kinds **is** the classification
    rule, not a stylistic choice: see :func:`classify_basis`.
    """

    # structural: measured by the scanner itself
    FINGERPRINT_MISMATCH = "FINGERPRINT_MISMATCH"
    FINGERPRINT_ABSENT_AT_BASELINE = "FINGERPRINT_ABSENT_AT_BASELINE"
    FINGERPRINT_ABSENT_NOW = "FINGERPRINT_ABSENT_NOW"
    SCANNER_FACT_MATCHED_BY_PATH = "SCANNER_FACT_MATCHED_BY_PATH"
    # weak: a cited relationship that is not by itself proof of impact
    TOPOLOGY_EDGE_CITES_FACT = "TOPOLOGY_EDGE_CITES_FACT"
    MANIFEST_DECLARED_FOR_PATH = "MANIFEST_DECLARED_FOR_PATH"
    DECLARED_EXPECTATION_NAMES_PATH = "DECLARED_EXPECTATION_NAMES_PATH"
    MEMORY_PINNED_TO_PATH = "MEMORY_PINNED_TO_PATH"
    MEMORY_CITES_INVALIDATED_FACT = "MEMORY_CITES_INVALIDATED_FACT"
    TEST_FILE_RELATED_BY_PATH = "TEST_FILE_RELATED_BY_PATH"


#: Basis kinds that are observations rather than inferences.
STRUCTURAL_BASIS: frozenset[BasisKind] = frozenset(
    {
        BasisKind.FINGERPRINT_MISMATCH,
        BasisKind.FINGERPRINT_ABSENT_AT_BASELINE,
        BasisKind.FINGERPRINT_ABSENT_NOW,
        BasisKind.SCANNER_FACT_MATCHED_BY_PATH,
    }
)

#: Hard ceiling on how many dependent links one path may contribute, so a very
#: large project cannot make one response unbounded. Truncation is always reported.
MAX_ITEMS_PER_CHANGE = 50


@dataclass(frozen=True)
class BasisItem:
    """One named observation behind a finding, with the object it came from.

    ``ref`` is a concrete identifier (a ``fact_id``, an ``edge_id``, a
    ``node_id``, a manifest path, a ``criterion_id``, a ``memory_id``, a test
    path). A basis item without a ``ref`` is not produced by this module.
    """

    kind: BasisKind
    ref: str
    detail: str = ""

    @property
    def structural(self) -> bool:
        return self.kind in STRUCTURAL_BASIS

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "ref": self.ref,
            "detail": self.detail,
            "structural": self.structural,
        }


def classify_basis(basis: Sequence[BasisItem]) -> FindingClassification:
    """The single rule that turns a basis into a classification.

    This function is the only place a classification is decided, so "an empty
    basis is never CONFIRMED" is provable by reading one function. An empty basis
    yields ``UNKNOWN``.
    """

    if not basis:
        return FindingClassification.UNKNOWN
    for item in basis:
        if item.structural:
            return FindingClassification.CONFIRMED
    return FindingClassification.PROBABLE


def support_rank(basis: Sequence[BasisItem]) -> int:
    """The number of *distinct* basis kinds present.

    Reported next to the classification and explicitly documented as **not** a
    confidence: it counts independent observations, it does not weight them, and
    it can never change the classification -- :func:`classify_basis` ignores it.
    """

    return len({item.kind for item in basis})


def _basis_dict(basis: Sequence[BasisItem]) -> dict[str, Any]:
    return {
        "classification": classify_basis(basis).value,
        "support_rank": support_rank(basis),
        "support_rank_meaning": (
            "count of distinct basis kinds; NOT a probability and NOT used to "
            "decide the classification"
        ),
        "basis": [item.to_dict() for item in basis],
        "basis_is_empty": not basis,
    }


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DependencyLink:
    """A dependent object reached from a changed file, with the link that did it."""

    kind: str
    ref: str
    name: str = ""
    path: str = ""
    via: str = ""
    basis: tuple[BasisItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "ref": self.ref,
            "name": self.name,
            "path": self.path,
            "via": self.via,
        }
        payload.update(_basis_dict(self.basis))
        return payload


@dataclass(frozen=True)
class StaleFact:
    """A structural fact that was current at baseline and is not now.

    ``reason`` is one of ``app.understanding.memory``'s own invalidation codes --
    ``source_content_changed``, ``source_deleted``, ``source_renamed``,
    ``source_deleted_with_ambiguous_move_candidates`` -- or
    ``fact_not_extracted_from_current_content`` when the file is still present
    with different bytes but the extractor no longer produces this fact. The last
    case is deliberately a different code: the file did not disappear, the
    observation did.
    """

    fact_id: str
    fact_type: str
    source_path: str
    reason: str
    line_start: int = 0
    line_end: int = 0
    extractor_id: str = ""
    baseline_fingerprint: str | None = None
    current_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "source_path": self.source_path,
            "reason": self.reason,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "extractor_id": self.extractor_id,
            "baseline_fingerprint": self.baseline_fingerprint,
            "current_fingerprint": self.current_fingerprint,
        }


@dataclass(frozen=True)
class DependentConclusion:
    """A memory entry whose cited evidence is no longer current.

    ``status`` is always ``PENDING_REVALIDATION`` and never anything stronger.
    Nothing here rewrites the entry: the conclusion is *named* as resting on
    evidence that moved, and re-establishing it is an explicit act through the
    existing memory routes.
    """

    memory_id: str
    kind: str
    statement: str
    status: str
    stale_evidence: tuple[str, ...] = ()
    invalidated_evidence: tuple[str, ...] = ()
    basis: tuple[BasisItem, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "memory_id": self.memory_id,
            "kind": self.kind,
            "statement": self.statement,
            "status": self.status,
            "stale_evidence": list(self.stale_evidence),
            "invalidated_evidence": list(self.invalidated_evidence),
        }
        payload.update(_basis_dict(self.basis))
        return payload


@dataclass(frozen=True)
class ChangeFinding:
    """One changed path, everything reached from it, and on what basis."""

    path: str
    kind: ChangeKind
    baseline_fingerprint: str | None
    current_fingerprint: str | None
    #: ``True`` for a modification or a deletion; a change that removes the
    #: baseline content is what makes a derived fact stop being current.
    stale: bool
    basis: tuple[BasisItem, ...] = ()
    affected_facts: tuple[Mapping[str, Any], ...] = ()
    stale_facts: tuple[StaleFact, ...] = ()
    dependents: tuple[DependencyLink, ...] = ()
    dependents_truncated: bool = False
    probable_rename_to: str = ""
    move_candidates: tuple[str, ...] = ()
    ambiguous_move: bool = False
    affected_requirements: tuple[Mapping[str, Any], ...] = ()
    affected_tests: tuple[Mapping[str, Any], ...] = ()
    stale_memory: tuple[Mapping[str, Any], ...] = ()
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "path": self.path,
            "kind": self.kind.value,
            "baseline_fingerprint": self.baseline_fingerprint,
            "current_fingerprint": self.current_fingerprint,
            "stale": self.stale,
            "affected_fact_ids": [
                str(fact.get("fact_id", "")) for fact in self.affected_facts
            ],
            "affected_facts": [dict(fact) for fact in self.affected_facts],
            "stale_facts": [item.to_dict() for item in self.stale_facts],
            "dependent_nodes": [link.to_dict() for link in self.dependents],
            "dependent_nodes_truncated": self.dependents_truncated,
            "probable_rename_to": self.probable_rename_to,
            "move_candidates": list(self.move_candidates),
            "ambiguous_move": self.ambiguous_move,
            "affected_requirements": [
                dict(item) for item in self.affected_requirements
            ],
            "affected_tests": [dict(item) for item in self.affected_tests],
            "stale_memory": [dict(item) for item in self.stale_memory],
            "note": self.note,
        }
        payload.update(_basis_dict(self.basis))
        return payload


@dataclass(frozen=True)
class ChangeImpactReport:
    """The whole report: findings, stale facts, dependent conclusions, limits."""

    findings: tuple[ChangeFinding, ...] = ()
    stale_facts: tuple[StaleFact, ...] = ()
    dependent_conclusions: tuple[DependentConclusion, ...] = ()
    unclassified_paths: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            "findings": len(self.findings),
            "confirmed": sum(
                1
                for finding in self.findings
                if classify_basis(finding.basis) is FindingClassification.CONFIRMED
            ),
            "probable": sum(
                1
                for finding in self.findings
                if classify_basis(finding.basis) is FindingClassification.PROBABLE
            ),
            "unknown": sum(
                1
                for finding in self.findings
                if classify_basis(finding.basis) is FindingClassification.UNKNOWN
            ),
            "stale_facts": len(self.stale_facts),
            "dependent_conclusions": len(self.dependent_conclusions),
            "ambiguous_moves": sum(
                1 for finding in self.findings if finding.ambiguous_move
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "stale_facts": [item.to_dict() for item in self.stale_facts],
            "dependent_conclusions": [
                item.to_dict() for item in self.dependent_conclusions
            ],
            "unclassified_paths": list(self.unclassified_paths),
            "counts": self.counts,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------


def _fact_dict(fact: Any) -> dict[str, Any]:
    if hasattr(fact, "to_dict"):
        return dict(fact.to_dict())
    return dict(fact) if isinstance(fact, Mapping) else {}


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1].lower()
    return (
        name.startswith("test_")
        or name.startswith("test-")
        or name.endswith("_test.py")
        or "/tests/" in f"/{path}"
        or path.startswith("tests/")
    )


class ChangeImpactEngine:
    """Derive findings from a baseline snapshot and the workspace as it is now.

    Constructed per call by :class:`app.api.operating_surface.OperatingSurface`
    with the two snapshots, the operator's declarations and the current memory
    document. It holds no state between calls and writes nothing.
    """

    def __init__(
        self,
        *,
        baseline: Any,
        current: Any,
        acceptance_specs: Mapping[str, Any] | None = None,
        memory: ProjectMemory | None = None,
    ) -> None:
        self._baseline = baseline
        self._current = current
        self._specs = dict(acceptance_specs or {})
        self._memory = memory

    # -- entry point ----------------------------------------------------
    def report(self, *, freshness: FreshnessIndex | None = None) -> ChangeImpactReport:
        base_map = {
            entry.relative_path: entry.fingerprint for entry in self._baseline.files
        }
        now_map = {
            entry.relative_path: entry.fingerprint for entry in self._current.files
        }

        # A move is only ever *probable*. Identical bytes are indistinguishable from
        # a rename, so candidates are collected and never ranked: one candidate is
        # reported as probable, several are reported as ambiguous and NOT resolved.
        added_by_fingerprint: dict[str, list[str]] = {}
        for path, fingerprint in now_map.items():
            if fingerprint and path not in base_map:
                added_by_fingerprint.setdefault(fingerprint, []).append(path)

        base_facts_by_path: dict[str, list[dict[str, Any]]] = {}
        for fact in self._baseline.facts:
            base_facts_by_path.setdefault(fact.source_path, []).append(_fact_dict(fact))
        now_facts_by_path: dict[str, list[dict[str, Any]]] = {}
        for fact in self._current.facts:
            now_facts_by_path.setdefault(fact.source_path, []).append(_fact_dict(fact))

        edges_by_fact: dict[str, list[Any]] = {}
        for edge in self._current.topology.edges:
            for fact_id in edge.fact_ids:
                edges_by_fact.setdefault(str(fact_id), []).append(edge)
        base_edges_by_fact: dict[str, list[Any]] = {}
        for edge in self._baseline.topology.edges:
            for fact_id in edge.fact_ids:
                base_edges_by_fact.setdefault(str(fact_id), []).append(edge)
        node_by_id = {node.node_id: node for node in self._current.topology.nodes}
        base_node_by_id = {node.node_id: node for node in self._baseline.topology.nodes}
        manifests = tuple(getattr(self._current, "manifests", ()) or ())
        memory = self._memory

        index = freshness if freshness is not None else FreshnessIndex.build(
            self._current.files
        )

        stale_facts = self._stale_facts(
            base_facts_by_path=base_facts_by_path,
            now_facts_by_path=now_facts_by_path,
            base_map=base_map,
            now_map=now_map,
            added_by_fingerprint=added_by_fingerprint,
        )
        stale_by_path: dict[str, list[StaleFact]] = {}
        for item in stale_facts:
            stale_by_path.setdefault(item.source_path, []).append(item)

        test_paths = sorted(path for path in now_map if _is_test_path(path))

        findings: list[ChangeFinding] = []
        unclassified: list[str] = []
        ambiguous_moves: list[tuple[str, tuple[str, ...]]] = []

        for path in sorted(set(base_map) | set(now_map)):
            was = base_map.get(path)
            now = now_map.get(path)
            if was == now:
                continue
            finding = self._finding(
                path=path,
                was=was,
                now=now,
                added_by_fingerprint=added_by_fingerprint,
                base_facts_by_path=base_facts_by_path,
                now_facts_by_path=now_facts_by_path,
                edges_by_fact=edges_by_fact,
                base_edges_by_fact=base_edges_by_fact,
                node_by_id=node_by_id,
                base_node_by_id=base_node_by_id,
                manifests=manifests,
                memory=memory,
                index=index,
                test_paths=test_paths,
                stale_for_path=tuple(stale_by_path.get(path, ())),
                ambiguous_moves=ambiguous_moves,
            )
            findings.append(finding)
            if not finding.basis:
                unclassified.append(path)

        dependent = self._dependent_conclusions(memory, stale_facts)

        notes: list[str] = []
        if not findings:
            notes.append(
                "no path differs from the baseline; that is a statement about the "
                "fingerprints compared, not a statement that no work was done"
            )
        for path, candidates in ambiguous_moves:
            notes.append(
                f"{path}: {len(candidates)} added file(s) carry identical bytes, so "
                "the destination of this deletion is ambiguous and is NOT resolved "
                "to any candidate: " + ", ".join(candidates)
            )
        if unclassified:
            notes.append(
                "a path with an empty basis is classified UNKNOWN; absence of "
                "evidence is never reported as a confirmed fact"
            )

        return ChangeImpactReport(
            findings=tuple(findings),
            stale_facts=stale_facts,
            dependent_conclusions=dependent,
            unclassified_paths=tuple(unclassified),
            notes=tuple(notes),
        )

    # -- one finding ----------------------------------------------------
    def _finding(
        self,
        *,
        path: str,
        was: str | None,
        now: str | None,
        added_by_fingerprint: Mapping[str, Sequence[str]],
        base_facts_by_path: Mapping[str, Sequence[Mapping[str, Any]]],
        now_facts_by_path: Mapping[str, Sequence[Mapping[str, Any]]],
        edges_by_fact: Mapping[str, Sequence[Any]],
        base_edges_by_fact: Mapping[str, Sequence[Any]],
        node_by_id: Mapping[str, Any],
        base_node_by_id: Mapping[str, Any],
        manifests: Sequence[Any],
        memory: ProjectMemory | None,
        index: FreshnessIndex,
        test_paths: Sequence[str],
        stale_for_path: Sequence[StaleFact],
        ambiguous_moves: list[tuple[str, tuple[str, ...]]],
    ) -> ChangeFinding:
        if was is None:
            kind = ChangeKind.ADDED
        elif now is None:
            kind = ChangeKind.DELETED
        else:
            kind = ChangeKind.MODIFIED

        basis: list[BasisItem] = []
        if kind is ChangeKind.ADDED:
            basis.append(
                BasisItem(
                    kind=BasisKind.FINGERPRINT_ABSENT_AT_BASELINE,
                    ref=path,
                    detail="the baseline snapshot held no fingerprint for this path",
                )
            )
        elif kind is ChangeKind.DELETED:
            basis.append(
                BasisItem(
                    kind=BasisKind.FINGERPRINT_ABSENT_NOW,
                    ref=path,
                    detail="the current scan holds no fingerprint for this path",
                )
            )
        else:
            basis.append(
                BasisItem(
                    kind=BasisKind.FINGERPRINT_MISMATCH,
                    ref=path,
                    detail=f"{str(was)[:16]}… -> {str(now)[:16]}…",
                )
            )

        affected_facts = tuple(
            dict(item)
            for item in (
                base_facts_by_path.get(path, ())
                if kind is ChangeKind.DELETED
                else now_facts_by_path.get(path, ())
            )
        )
        if affected_facts:
            basis.append(
                BasisItem(
                    kind=BasisKind.SCANNER_FACT_MATCHED_BY_PATH,
                    ref=path,
                    detail=(
                        f"{len(affected_facts)} structural fact(s) extracted from "
                        "this path"
                    ),
                )
            )

        for manifest in manifests:
            if str(getattr(manifest, "relative_path", "")) != path:
                continue
            basis.append(
                BasisItem(
                    kind=BasisKind.MANIFEST_DECLARED_FOR_PATH,
                    ref=path,
                    detail=(
                        "this path is itself a declared manifest "
                        f"({getattr(manifest, 'manifest_type', '')})"
                    ),
                )
            )

        dependents = self._dependents(
            affected_facts,
            edges_by_fact if kind is not ChangeKind.DELETED else base_edges_by_fact,
            node_by_id if kind is not ChangeKind.DELETED else base_node_by_id,
        )
        for link in dependents:
            basis.extend(link.basis)

        requirements = self._affected_requirements(path, kind)
        for link in requirements:
            basis.append(
                BasisItem(
                    kind=BasisKind.DECLARED_EXPECTATION_NAMES_PATH,
                    ref=str(link["criterion_id"]),
                    detail=f"declared expectation names {path}",
                )
            )

        tests = self._affected_tests(path, test_paths)
        for link in tests:
            basis.append(
                BasisItem(
                    kind=BasisKind.TEST_FILE_RELATED_BY_PATH,
                    ref=str(link["path"]),
                    detail=str(link["why"]),
                )
            )

        stale_memory, memory_basis = self._stale_memory(path, memory, index)
        basis.extend(memory_basis)

        probable_rename_to = ""
        move_candidates: tuple[str, ...] = ()
        ambiguous = False
        if kind is ChangeKind.DELETED and was:
            candidates = tuple(
                sorted(
                    item for item in added_by_fingerprint.get(was, ()) if item != path
                )
            )
            if len(candidates) == 1:
                probable_rename_to = candidates[0]
                basis.append(
                    BasisItem(
                        kind=BasisKind.FINGERPRINT_MISMATCH,
                        ref=candidates[0],
                        detail=(
                            "exactly one added path carries the baseline fingerprint "
                            "of the deleted path, so a rename is probable and is NOT "
                            "established"
                        ),
                    )
                )
            elif len(candidates) > 1:
                move_candidates = candidates
                ambiguous = True
                ambiguous_moves.append((path, candidates))

        note = ""
        if ambiguous:
            note = (
                "the destination of this deletion is ambiguous: "
                f"{len(move_candidates)} candidate(s) carry identical bytes. No "
                "candidate was chosen, and none may be treated as the destination."
            )
        elif probable_rename_to:
            note = (
                "a rename is probable, not established: identical bytes cannot "
                "distinguish a move from a copy followed by a delete."
            )

        truncated = len(dependents) > MAX_ITEMS_PER_CHANGE
        return ChangeFinding(
            path=path,
            kind=kind,
            baseline_fingerprint=was,
            current_fingerprint=now,
            stale=kind in {ChangeKind.MODIFIED, ChangeKind.DELETED},
            basis=tuple(basis),
            affected_facts=affected_facts,
            stale_facts=tuple(stale_for_path),
            dependents=tuple(dependents[:MAX_ITEMS_PER_CHANGE]),
            dependents_truncated=truncated,
            probable_rename_to=probable_rename_to,
            move_candidates=move_candidates,
            ambiguous_move=ambiguous,
            affected_requirements=tuple(requirements),
            affected_tests=tuple(tests),
            stale_memory=tuple(stale_memory),
            note=note,
        )

    # -- pieces ---------------------------------------------------------
    @staticmethod
    def _dependents(
        affected_facts: Sequence[Mapping[str, Any]],
        edges_by_fact: Mapping[str, Sequence[Any]],
        node_by_id: Mapping[str, Any],
    ) -> list[DependencyLink]:
        """Topology nodes reached from an affected fact, each with its edge.

        The evidence named is the edge and the fact: the edge *cites* the fact, so
        the relationship is real and cited -- but citing a fact is not by itself
        proof that the dependent node is affected. That is why the link carries a
        weak basis, and why a finding that rests only on such links is at most
        ``PROBABLE``.
        """

        seen: dict[str, DependencyLink] = {}
        for fact in affected_facts:
            fact_id = str(fact.get("fact_id", ""))
            if not fact_id:
                continue
            for edge in edges_by_fact.get(fact_id, ()):
                edge_id = str(getattr(edge, "edge_id", ""))
                edge_type = getattr(getattr(edge, "edge_type", ""), "value", "")
                for node_id in (
                    getattr(edge, "source_node_id", ""),
                    getattr(edge, "target_node_id", ""),
                ):
                    node_id = str(node_id)
                    if not node_id or node_id in seen:
                        continue
                    node = node_by_id.get(node_id)
                    if node is None:
                        continue
                    seen[node_id] = DependencyLink(
                        kind="topology_node",
                        ref=node_id,
                        name=str(getattr(node, "name", "")),
                        path=str(getattr(node, "relative_path", "")),
                        via=f"{edge_type}:{edge_id}",
                        basis=(
                            BasisItem(
                                kind=BasisKind.TOPOLOGY_EDGE_CITES_FACT,
                                ref=edge_id or fact_id,
                                detail=(
                                    f"edge {edge_type} cites fact {fact_id} extracted "
                                    "from the changed path"
                                ),
                            ),
                        ),
                    )
        return [seen[key] for key in sorted(seen)]

    def _affected_requirements(
        self, path: str, kind: ChangeKind
    ) -> list[dict[str, Any]]:
        """Requirements whose *declared* expectation names the changed path.

        A requirement is linked only on a literal path match between the
        operator's declared expectation and the changed path. Everything else is
        reported as unlinked elsewhere; nothing is inferred.
        """

        links: list[dict[str, Any]] = []
        for declaration_id, spec in sorted(self._specs.items()):
            expectations = getattr(spec, "expectations", {}) or {}
            for criterion in getattr(spec, "criteria", ()) or ():
                expectation = expectations.get(criterion.criterion_id)
                if expectation is None:
                    continue
                if str(getattr(expectation, "relative_path", "")) != path:
                    continue
                links.append(
                    {
                        "declaration_id": declaration_id,
                        "criterion_id": criterion.criterion_id,
                        "requirement_id": str(getattr(criterion, "requirement_id", "")),
                        "description": criterion.description,
                        "expectation_path": str(expectation.relative_path),
                        "must_exist": bool(getattr(expectation, "exists", False)),
                        "action": "re-run verification for this criterion",
                        "changed_kind": kind.value,
                    }
                )
        return links

    @staticmethod
    def _affected_tests(path: str, test_paths: Sequence[str]) -> list[dict[str, Any]]:
        """Existing test files whose *path* relates to the changed path.

        This is a path relation, not proof of coverage. The rule is deliberately
        narrow and deterministic -- same stem, or a test module whose text names
        the changed module's stem -- and it is reported with a weak basis, so an
        affected test is at most ``PROBABLE``. Naming a test that does not
        actually cover the change would be the false traceability this block
        forbids; naming none when the path relation exists would be a silent gap.
        """

        if _is_test_path(path):
            # A changed test file is itself the check; it is not "related to" another.
            return []
        stem = path.rsplit("/", 1)[-1]
        stem = stem[: -len(".py")] if stem.endswith(".py") else stem
        if not stem:
            return []
        links: list[dict[str, Any]] = []
        for candidate in test_paths:
            name = candidate.rsplit("/", 1)[-1]
            name_stem = name[: -len(".py")] if name.endswith(".py") else name
            haystack = name_stem.lower()
            if stem.lower() in haystack or haystack.replace("test_", "").replace(
                "test-", ""
            ) == stem.lower():
                links.append(
                    {
                        "path": candidate,
                        "why": (
                            f"test path {candidate!r} names {stem!r}; this is a path "
                            "relation, not proof that it covers the change"
                        ),
                    }
                )
        return links

    @staticmethod
    def _stale_memory(
        path: str,
        memory: ProjectMemory | None,
        index: FreshnessIndex,
    ) -> tuple[list[dict[str, Any]], list[BasisItem]]:
        if memory is None:
            return [], []
        entries: list[dict[str, Any]] = []
        basis: list[BasisItem] = []
        for record in memory.records:
            if record.pin.source_path != path:
                continue
            verdict = freshness_of(record, index)
            entries.append(
                {
                    "memory_id": record.memory_id,
                    "statement": record.statement,
                    "kind": record.kind.value,
                    "pinned_path": record.pin.source_path,
                    "freshness": verdict.to_dict(),
                }
            )
            basis.append(
                BasisItem(
                    kind=BasisKind.MEMORY_PINNED_TO_PATH,
                    ref=record.memory_id,
                    detail=(
                        f"{record.kind.value} entry is pinned to {path} "
                        f"(freshness {verdict.outcome.value})"
                    ),
                )
            )
        entries.sort(key=lambda item: item["memory_id"])
        return entries, basis

    def _stale_facts(
        self,
        *,
        base_facts_by_path: Mapping[str, Sequence[Mapping[str, Any]]],
        now_facts_by_path: Mapping[str, Sequence[Mapping[str, Any]]],
        base_map: Mapping[str, str | None],
        now_map: Mapping[str, str | None],
        added_by_fingerprint: Mapping[str, Sequence[str]],
    ) -> tuple[StaleFact, ...]:
        """Facts that were current at baseline and are not now, with the reason.

        A fact id is derived from its own content (``StructuralFact.create``), so
        "still current" is decidable exactly: the same fact id must still be
        present for the same source path in the current snapshot.
        """

        stale: list[StaleFact] = []
        for path, facts in sorted(base_facts_by_path.items()):
            still_present = {
                str(item.get("fact_id", "")) for item in now_facts_by_path.get(path, ())
            }
            reason = self._invalidation_reason(
                path, base_map, now_map, added_by_fingerprint
            )
            for fact in facts:
                fact_id = str(fact.get("fact_id", ""))
                if not fact_id or fact_id in still_present:
                    continue
                stale.append(
                    StaleFact(
                        fact_id=fact_id,
                        fact_type=str(fact.get("fact_type", "")),
                        source_path=path,
                        reason=reason,
                        line_start=int(fact.get("line_start", 0) or 0),
                        line_end=int(fact.get("line_end", 0) or 0),
                        extractor_id=str(fact.get("extractor_id", "")),
                        baseline_fingerprint=base_map.get(path),
                        current_fingerprint=now_map.get(path),
                    )
                )
        stale.sort(key=lambda item: (item.source_path, item.fact_id))
        return tuple(stale)

    @staticmethod
    def _invalidation_reason(
        path: str,
        base_map: Mapping[str, str | None],
        now_map: Mapping[str, str | None],
        added_by_fingerprint: Mapping[str, Sequence[str]],
    ) -> str:
        """The invalidation code, using ``memory.py``'s own vocabulary."""

        if path not in now_map:
            candidates = tuple(added_by_fingerprint.get(base_map.get(path) or "", ()))
            if len(candidates) > 1:
                return "source_deleted_with_ambiguous_move_candidates"
            if len(candidates) == 1:
                return "source_renamed"
            return "source_deleted"
        if base_map.get(path) != now_map.get(path):
            return "source_content_changed"
        return "fact_not_extracted_from_current_content"

    @staticmethod
    def _dependent_conclusions(
        memory: ProjectMemory | None,
        stale_facts: Sequence[StaleFact],
    ) -> tuple[DependentConclusion, ...]:
        """Entries whose cited evidence is no longer current, named not rewritten.

        A memory entry cites evidence through ``evidence_fact_ids``. When one of
        those fact ids is a stale fact, the entry's conclusion is no longer
        supported by the evidence it names. This module reports that; it does not
        edit the entry, does not mark it stale in the store, and promotes nothing.
        ``status`` is always ``PENDING_REVALIDATION``.
        """

        if memory is None or not stale_facts:
            return ()
        by_id = {fact.fact_id: fact for fact in stale_facts}
        conclusions: list[DependentConclusion] = []
        for record in memory.records:
            cited = tuple(str(fid) for fid in record.evidence_fact_ids)
            cited_stale = tuple(fid for fid in cited if fid in by_id)
            if not cited_stale:
                continue
            invalidated = tuple(
                fid
                for fid in cited_stale
                if record.status in {MemoryStatus.STALE, MemoryStatus.INVALIDATED}
            )
            conclusions.append(
                DependentConclusion(
                    memory_id=record.memory_id,
                    kind=record.kind.value,
                    statement=record.statement,
                    status="PENDING_REVALIDATION",
                    stale_evidence=cited_stale,
                    invalidated_evidence=invalidated,
                    basis=tuple(
                        BasisItem(
                            kind=BasisKind.MEMORY_CITES_INVALIDATED_FACT,
                            ref=fid,
                            detail=(
                                f"cites fact {fid} extracted from "
                                f"{by_id[fid].source_path}, which is no longer current "
                                f"({by_id[fid].reason})"
                            ),
                        )
                        for fid in cited_stale
                    ),
                )
            )
        conclusions.sort(key=lambda item: item.memory_id)
        return tuple(conclusions)
