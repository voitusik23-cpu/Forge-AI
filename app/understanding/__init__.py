"""Forge Project Understanding & Static Topology Subsystem v0.1."""

from __future__ import annotations

from app.understanding.extractors.base import BaseExtractor
from app.understanding.extractors.generic_extractor import GenericExtractor
from app.understanding.extractors.manifest_extractor import ManifestExtractor
from app.understanding.extractors.python_extractor import PythonAstExtractor
from app.understanding.models import (
    DeclaredDependency,
    EdgeType,
    InferredFact,
    ManifestDescriptor,
    NodeType,
    ProjectTopology,
    ScanLimits,
    ScanWarning,
    StructuralFact,
    StructuralFactType,
    TopologyEdge,
    TopologyNode,
    UnderstandingSnapshot,
)
from app.understanding.scanner import BoundedProjectScanner
from app.understanding.snapshotter import UnderstandingSnapshotter
from app.understanding.topology import ProjectTopologyBuilder

__all__ = [
    "BaseExtractor",
    "BoundedProjectScanner",
    "DeclaredDependency",
    "EdgeType",
    "GenericExtractor",
    "InferredFact",
    "ManifestDescriptor",
    "ManifestExtractor",
    "NodeType",
    "ProjectTopology",
    "ProjectTopologyBuilder",
    "PythonAstExtractor",
    "ScanLimits",
    "ScanWarning",
    "StructuralFact",
    "StructuralFactType",
    "TopologyEdge",
    "TopologyNode",
    "UnderstandingSnapshot",
    "UnderstandingSnapshotter",
]
