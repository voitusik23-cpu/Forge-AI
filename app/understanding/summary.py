"""Summary and aggregation utilities for UnderstandingSnapshot."""

from __future__ import annotations

from typing import Any, Dict
from app.understanding.models import StructuralFactType, UnderstandingSnapshot


def summarize_snapshot(snapshot: UnderstandingSnapshot) -> Dict[str, Any]:
    """Compute aggregate topology and fact statistics from an UnderstandingSnapshot."""
    fact_counts: Dict[str, int] = {}
    for fact in snapshot.facts:
        f_type = fact.fact_type.value
        fact_counts[f_type] = fact_counts.get(f_type, 0) + 1

    total_symbols = fact_counts.get(StructuralFactType.SYMBOL_DECLARED.value, 0)
    total_modules = fact_counts.get(StructuralFactType.MODULE_DEFINED.value, 0)
    total_packages = fact_counts.get(StructuralFactType.PACKAGE_DEFINED.value, 0)
    total_imports = fact_counts.get(StructuralFactType.IMPORT_DECLARED.value, 0)
    total_dependencies = sum(len(m.dependencies) for m in snapshot.manifests)

    return {
        "snapshot_id": snapshot.snapshot_id,
        "project_id": snapshot.project_id,
        "total_files": len(snapshot.files),
        "total_manifests": len(snapshot.manifests),
        "total_facts": len(snapshot.facts),
        "total_nodes": len(snapshot.topology.nodes),
        "total_edges": len(snapshot.topology.edges),
        "total_symbols": total_symbols,
        "total_modules": total_modules,
        "total_packages": total_packages,
        "total_imports": total_imports,
        "total_dependencies": total_dependencies,
        "total_warnings": len(snapshot.warnings),
        "facts_by_type": fact_counts,
    }


def format_snapshot_markdown(snapshot: UnderstandingSnapshot) -> str:
    """Format UnderstandingSnapshot summary as a Markdown report."""
    summary = summarize_snapshot(snapshot)
    lines = [
        f"# Project Understanding Summary: {summary['project_id']}",
        "",
        f"- **Snapshot ID**: `{summary['snapshot_id']}`",
        f"- **Indexed Files**: {summary['total_files']}",
        f"- **Manifests**: {summary['total_manifests']}",
        f"- **Topology Nodes**: {summary['total_nodes']}",
        f"- **Topology Edges**: {summary['total_edges']}",
        f"- **Declared Modules**: {summary['total_modules']}",
        f"- **Declared Packages**: {summary['total_packages']}",
        f"- **Declared Symbols**: {summary['total_symbols']}",
        f"- **Declared Imports**: {summary['total_imports']}",
        f"- **Declared Dependencies**: {summary['total_dependencies']}",
        f"- **Scan Warnings**: {summary['total_warnings']}",
        "",
        "## Facts Breakdown",
        "",
    ]
    for fact_type, count in sorted(summary["facts_by_type"].items()):
        lines.append(f"- `{fact_type}`: {count}")
    return "\n".join(lines)
