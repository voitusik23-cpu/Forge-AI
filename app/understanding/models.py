"""Domain contracts and data models for Project Understanding v0.1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Any, Optional
from uuid import uuid4

from app.context.models import ContextSourceType, ContextTrustLevel
from app.snapshots import SnapshotFile


class NodeType(str, Enum):
    """Types of structural entities in the project topology graph."""

    FILE = "FILE"
    MODULE = "MODULE"
    PACKAGE = "PACKAGE"
    SYMBOL = "SYMBOL"
    MANIFEST = "MANIFEST"


class EdgeType(str, Enum):
    """Types of relationships connecting structural entities in project topology."""

    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    DEFINES = "DEFINES"
    DECLARES_DEPENDENCY = "DECLARES_DEPENDENCY"
    REFERENCES = "REFERENCES"


class StructuralFactType(str, Enum):
    """Discrete, deterministic observation types extracted statically from source."""

    MODULE_DEFINED = "MODULE_DEFINED"
    PACKAGE_DEFINED = "PACKAGE_DEFINED"
    SYMBOL_DECLARED = "SYMBOL_DECLARED"
    IMPORT_DECLARED = "IMPORT_DECLARED"
    DEPENDENCY_DECLARED = "DEPENDENCY_DECLARED"
    FILE_INVENTORIED = "FILE_INVENTORIED"
    MANIFEST_INVENTORIED = "MANIFEST_INVENTORIED"


@dataclass(frozen=True)
class ScanLimits:
    """Configurable boundaries protecting static analysis from resource exhaustion."""

    max_file_size_bytes: int = 512 * 1024       # 512 KB
    max_total_bytes: int = 50 * 1024 * 1024     # 50 MB
    max_file_count: int = 5000
    parse_timeout_seconds: float = 2.0


@dataclass(frozen=True)
class ScanWarning:
    """Non-fatal warning encountered during project scanning or extraction."""

    warning_type: str
    message: str
    file_path: Optional[str] = None
    line: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "warning_type": self.warning_type,
            "message": self.message,
            "file_path": self.file_path,
            "line": self.line,
        }


@dataclass(frozen=True)
class StructuralFact:
    """A deterministic, verifiable structural fact extracted from code or configuration.

    Confidence is strictly binary (1.0 implied); probabilistic confidence scores
    are forbidden for structural facts.
    """

    fact_id: str
    fact_type: StructuralFactType
    source_path: str
    line_start: int
    line_end: int
    extractor_id: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.fact_id:
            raise ValueError("fact_id must be a non-empty string")
        if not self.source_path:
            raise ValueError("source_path must be a non-empty string")
        if self.line_start < 0 or self.line_end < 0:
            raise ValueError("line numbers must be non-negative integers")
        if isinstance(self.details, dict):
            object.__setattr__(self, "details", dict(self.details))

    @classmethod
    def create(
        cls,
        fact_type: StructuralFactType,
        source_path: str,
        line_start: int,
        line_end: int,
        extractor_id: str,
        details: Mapping[str, Any],
    ) -> StructuralFact:
        """Create a StructuralFact with a deterministic, reproducible fact_id."""
        normalized_path = source_path.replace("\\", "/")
        identity_payload = f"{fact_type.value}:{normalized_path}:{line_start}:{line_end}:{extractor_id}"
        details_repr = repr(sorted(details.items()))
        details_digest = hashlib.sha256(details_repr.encode("utf-8")).hexdigest()[:12]
        fact_id = f"fact-{hashlib.sha256(identity_payload.encode('utf-8')).hexdigest()[:16]}-{details_digest}"
        return cls(
            fact_id=fact_id,
            fact_type=fact_type,
            source_path=normalized_path,
            line_start=line_start,
            line_end=line_end,
            extractor_id=extractor_id,
            details=details,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type.value,
            "source_path": self.source_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "extractor_id": self.extractor_id,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class InferredFact:
    """An inference, classification, or summary derived by an LLM or heuristic.

    Must explicitly cite the deterministic StructuralFact IDs supporting it.
    Cannot mutate or masquerade as a StructuralFact.
    """

    inference_id: str
    category: str
    statement: str
    evidence_fact_ids: tuple[str, ...]
    model_id: str
    trust_level: ContextTrustLevel = ContextTrustLevel.INFERRED
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.inference_id:
            raise ValueError("inference_id must be a non-empty string")
        if not self.statement.strip():
            raise ValueError("statement must be non-empty")
        if isinstance(self.evidence_fact_ids, (list, tuple)):
            object.__setattr__(self, "evidence_fact_ids", tuple(self.evidence_fact_ids))
        if isinstance(self.details, dict):
            object.__setattr__(self, "details", dict(self.details))


@dataclass(frozen=True)
class TopologyNode:
    """A discrete structural entity in the project topology."""

    node_id: str
    node_type: NodeType
    name: str
    relative_path: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id:
            raise ValueError("node_id must be a non-empty string")
        if not self.name:
            raise ValueError("name must be a non-empty string")
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type.value,
            "name": self.name,
            "relative_path": self.relative_path,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class TopologyEdge:
    """A directed structural relationship between two topology nodes with provenance."""

    edge_id: str
    edge_type: EdgeType
    source_node_id: str
    target_node_id: str
    fact_ids: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.edge_id:
            raise ValueError("edge_id must be a non-empty string")
        if isinstance(self.fact_ids, (list, tuple)):
            object.__setattr__(self, "fact_ids", tuple(self.fact_ids))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "edge_type": self.edge_type.value,
            "source_node_id": self.source_node_id,
            "target_node_id": self.target_node_id,
            "fact_ids": list(self.fact_ids),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DeclaredDependency:
    """An external dependency declared in a package manifest."""

    name: str
    version_spec: str
    manifest_path: str
    dependency_type: str = "production"  # production, dev, build, peer
    is_optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version_spec": self.version_spec,
            "manifest_path": self.manifest_path,
            "dependency_type": self.dependency_type,
            "is_optional": self.is_optional,
        }


@dataclass(frozen=True)
class ManifestDescriptor:
    """Descriptor of a detected and statically parsed package manifest."""

    relative_path: str
    manifest_type: str  # python_pip, python_pyproject, npm_package_json, rust_cargo, etc.
    declared_dependencies: tuple[DeclaredDependency, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.declared_dependencies, (list, tuple)):
            object.__setattr__(self, "declared_dependencies", tuple(self.declared_dependencies))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def dependencies(self) -> tuple[DeclaredDependency, ...]:
        return self.declared_dependencies

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "manifest_type": self.manifest_type,
            "declared_dependencies": [d.to_dict() for d in self.declared_dependencies],
            "dependencies": [d.to_dict() for d in self.declared_dependencies],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ProjectTopology:
    """Immutable property graph representing project structure."""

    nodes: tuple[TopologyNode, ...] = ()
    edges: tuple[TopologyEdge, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.nodes, (list, tuple)):
            # Deterministic sorting
            sorted_nodes = sorted(
                self.nodes,
                key=lambda n: (n.relative_path, n.node_type.value, n.name, n.node_id),
            )
            object.__setattr__(self, "nodes", tuple(sorted_nodes))
        if isinstance(self.edges, (list, tuple)):
            # Deterministic sorting
            sorted_edges = sorted(
                self.edges,
                key=lambda e: (e.source_node_id, e.edge_type.value, e.target_node_id, e.edge_id),
            )
            object.__setattr__(self, "edges", tuple(sorted_edges))

    def get_node(self, node_id: str) -> Optional[TopologyNode]:
        for n in self.nodes:
            if n.node_id == node_id:
                return n
        return None

    def get_outgoing_edges(self, node_id: str, edge_type: Optional[EdgeType] = None) -> tuple[TopologyEdge, ...]:
        return tuple(
            e for e in self.edges
            if e.source_node_id == node_id and (edge_type is None or e.edge_type == edge_type)
        )

    def get_incoming_edges(self, node_id: str, edge_type: Optional[EdgeType] = None) -> tuple[TopologyEdge, ...]:
        return tuple(
            e for e in self.edges
            if e.target_node_id == node_id and (edge_type is None or e.edge_type == edge_type)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }


@dataclass(frozen=True)
class UnderstandingSnapshot:
    """Immutable, reproducible snapshot of project understanding at a point in time."""

    snapshot_id: str
    project_id: str
    workspace_fingerprint: str
    files: tuple[SnapshotFile, ...]
    manifests: tuple[ManifestDescriptor, ...]
    topology: ProjectTopology
    facts: tuple[StructuralFact, ...]
    warnings: tuple[ScanWarning, ...] = ()
    extractor_versions: Mapping[str, str] = field(default_factory=dict)
    parent_snapshot_id: Optional[str] = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.files, (list, tuple)):
            sorted_files = sorted(self.files, key=lambda f: f.relative_path)
            object.__setattr__(self, "files", tuple(sorted_files))
        if isinstance(self.manifests, (list, tuple)):
            sorted_manifests = sorted(self.manifests, key=lambda m: m.relative_path)
            object.__setattr__(self, "manifests", tuple(sorted_manifests))
        if isinstance(self.facts, (list, tuple)):
            sorted_facts = sorted(
                self.facts,
                key=lambda f: (f.source_path, f.line_start, f.line_end, f.fact_id),
            )
            object.__setattr__(self, "facts", tuple(sorted_facts))
        if isinstance(self.warnings, (list, tuple)):
            object.__setattr__(self, "warnings", tuple(self.warnings))
        if isinstance(self.extractor_versions, dict):
            object.__setattr__(self, "extractor_versions", dict(self.extractor_versions))

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "project_id": self.project_id,
            "workspace_fingerprint": self.workspace_fingerprint,
            "files": [
                {
                    "relative_path": f.relative_path,
                    "exists": f.exists,
                    "fingerprint": f.fingerprint,
                }
                for f in self.files
            ],
            "manifests": [m.to_dict() for m in self.manifests],
            "topology": self.topology.to_dict(),
            "facts": [f.to_dict() for f in self.facts],
            "warnings": [w.to_dict() for w in self.warnings],
            "extractor_versions": dict(self.extractor_versions),
            "parent_snapshot_id": self.parent_snapshot_id,
            "created_at": self.created_at,
        }
