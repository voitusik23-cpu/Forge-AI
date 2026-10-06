"""Deterministic topology graph builder constructing nodes and edges from structural facts."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Dict, List, Optional, Set

from app.snapshots import SnapshotFile
from app.understanding.models import (
    EdgeType,
    NodeType,
    ProjectTopology,
    StructuralFact,
    StructuralFactType,
    TopologyEdge,
    TopologyNode,
)


class ProjectTopologyBuilder:
    """Build a deterministic, queryable ProjectTopology from static structural facts."""

    def build(
        self,
        facts: Sequence[StructuralFact],
        files: Sequence[SnapshotFile] = (),
    ) -> ProjectTopology:
        nodes: Dict[str, TopologyNode] = {}
        edges: Dict[str, TopologyEdge] = {}

        # 1. Create File Nodes from SnapshotFiles
        for f in files:
            norm_path = f.relative_path.replace("\\", "/")
            node_id = f"node:file:{norm_path}"
            nodes[node_id] = TopologyNode(
                node_id=node_id,
                node_type=NodeType.FILE,
                name=norm_path.split("/")[-1],
                relative_path=norm_path,
                metadata={"exists": f.exists, "fingerprint": f.fingerprint},
            )

        # 2. Process Facts into Nodes and Edges
        for fact in facts:
            source_file_id = f"node:file:{fact.source_path}"
            # Ensure file node exists
            if source_file_id not in nodes:
                nodes[source_file_id] = TopologyNode(
                    node_id=source_file_id,
                    node_type=NodeType.FILE,
                    name=fact.source_path.split("/")[-1],
                    relative_path=fact.source_path,
                )

            # Module / Package
            if fact.fact_type in (StructuralFactType.MODULE_DEFINED, StructuralFactType.PACKAGE_DEFINED):
                mod_name = str(fact.details.get("module_name", ""))
                node_type = NodeType.PACKAGE if fact.fact_type == StructuralFactType.PACKAGE_DEFINED else NodeType.MODULE
                mod_node_id = f"node:module:{mod_name}" if mod_name else f"node:module:{fact.source_path}"
                nodes[mod_node_id] = TopologyNode(
                    node_id=mod_node_id,
                    node_type=node_type,
                    name=mod_name or fact.source_path,
                    relative_path=fact.source_path,
                    metadata=fact.details,
                )
                # File CONTAINS Module edge
                edge_id = f"edge:contains:{source_file_id}->{mod_node_id}"
                edges[edge_id] = TopologyEdge(
                    edge_id=edge_id,
                    edge_type=EdgeType.CONTAINS,
                    source_node_id=source_file_id,
                    target_node_id=mod_node_id,
                    fact_ids=(fact.fact_id,),
                )

            # Symbol
            elif fact.fact_type == StructuralFactType.SYMBOL_DECLARED:
                sym_name = str(fact.details.get("symbol_name", ""))
                parent_class = fact.details.get("parent_class")
                mod_name = fact.details.get("module_name")

                full_sym_name = f"{parent_class}.{sym_name}" if parent_class else sym_name
                canonical_sym_id = f"node:symbol:{fact.source_path}:{full_sym_name}"
                nodes[canonical_sym_id] = TopologyNode(
                    node_id=canonical_sym_id,
                    node_type=NodeType.SYMBOL,
                    name=full_sym_name,
                    relative_path=fact.source_path,
                    metadata=fact.details,
                )

                # File DEFINES Symbol edge
                edge_id = f"edge:defines:{source_file_id}->{canonical_sym_id}"
                edges[edge_id] = TopologyEdge(
                    edge_id=edge_id,
                    edge_type=EdgeType.DEFINES,
                    source_node_id=source_file_id,
                    target_node_id=canonical_sym_id,
                    fact_ids=(fact.fact_id,),
                )

                # If inside a class, Class Symbol CONTAINS Method Symbol edge
                if parent_class:
                    parent_sym_id = f"node:symbol:{fact.source_path}:{parent_class}"
                    edge_id_contains = f"edge:contains:{parent_sym_id}->{canonical_sym_id}"
                    edges[edge_id_contains] = TopologyEdge(
                        edge_id=edge_id_contains,
                        edge_type=EdgeType.CONTAINS,
                        source_node_id=parent_sym_id,
                        target_node_id=canonical_sym_id,
                        fact_ids=(fact.fact_id,),
                    )

            # Import
            elif fact.fact_type == StructuralFactType.IMPORT_DECLARED:
                imported_mod = str(fact.details.get("module", ""))
                if imported_mod:
                    target_mod_id = f"node:module:{imported_mod}"
                    if target_mod_id not in nodes:
                        nodes[target_mod_id] = TopologyNode(
                            node_id=target_mod_id,
                            node_type=NodeType.MODULE,
                            name=imported_mod,
                            relative_path="",
                            metadata={"is_external_or_unresolved": True},
                        )

                    edge_id = f"edge:imports:{source_file_id}->{target_mod_id}"
                    existing_edge = edges.get(edge_id)
                    fact_ids = tuple(sorted(set((existing_edge.fact_ids if existing_edge else ()) + (fact.fact_id,))))
                    edges[edge_id] = TopologyEdge(
                        edge_id=edge_id,
                        edge_type=EdgeType.IMPORTS,
                        source_node_id=source_file_id,
                        target_node_id=target_mod_id,
                        fact_ids=fact_ids,
                        metadata=fact.details,
                    )

            # Manifest / Dependency
            elif fact.fact_type == StructuralFactType.MANIFEST_INVENTORIED:
                manifest_node_id = f"node:manifest:{fact.source_path}"
                nodes[manifest_node_id] = TopologyNode(
                    node_id=manifest_node_id,
                    node_type=NodeType.MANIFEST,
                    name=fact.source_path.split("/")[-1],
                    relative_path=fact.source_path,
                    metadata=fact.details,
                )
                edge_id = f"edge:contains:{source_file_id}->{manifest_node_id}"
                edges[edge_id] = TopologyEdge(
                    edge_id=edge_id,
                    edge_type=EdgeType.CONTAINS,
                    source_node_id=source_file_id,
                    target_node_id=manifest_node_id,
                    fact_ids=(fact.fact_id,),
                )

            elif fact.fact_type == StructuralFactType.DEPENDENCY_DECLARED:
                dep_name = str(fact.details.get("dependency_name", ""))
                manifest_node_id = f"node:manifest:{fact.source_path}"
                dep_node_id = f"node:package:{dep_name}"

                if dep_node_id not in nodes:
                    nodes[dep_node_id] = TopologyNode(
                        node_id=dep_node_id,
                        node_type=NodeType.PACKAGE,
                        name=dep_name,
                        relative_path="",
                        metadata={"is_declared_dependency": True},
                    )

                edge_id = f"edge:declares_dep:{manifest_node_id}->{dep_node_id}"
                edges[edge_id] = TopologyEdge(
                    edge_id=edge_id,
                    edge_type=EdgeType.DECLARES_DEPENDENCY,
                    source_node_id=manifest_node_id,
                    target_node_id=dep_node_id,
                    fact_ids=(fact.fact_id,),
                    metadata=fact.details,
                )

        return ProjectTopology(
            nodes=tuple(nodes.values()),
            edges=tuple(edges.values()),
        )
