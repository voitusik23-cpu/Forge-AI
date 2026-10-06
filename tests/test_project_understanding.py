"""Unit and functional tests for Project Understanding & Static Topology v0.1."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.tools.workspace import Workspace
from app.understanding import (
    EdgeType,
    GenericExtractor,
    InferredFact,
    ManifestExtractor,
    NodeType,
    ProjectTopologyBuilder,
    PythonAstExtractor,
    StructuralFact,
    StructuralFactType,
    UnderstandingSnapshotter,
)


class ProjectUnderstandingModelTests(unittest.TestCase):
    """Test immutability and contract invariants of domain models."""

    def test_structural_fact_creation_and_immutability(self) -> None:
        fact = StructuralFact.create(
            fact_type=StructuralFactType.MODULE_DEFINED,
            source_path="app/test_mod.py",
            line_start=1,
            line_end=50,
            extractor_id="python_ast_v1",
            details={"module_name": "app.test_mod"},
        )
        self.assertTrue(fact.fact_id.startswith("fact-"))
        self.assertEqual(fact.source_path, "app/test_mod.py")
        self.assertEqual(fact.details["module_name"], "app.test_mod")

        # Immutability
        with self.assertRaises(AttributeError):
            fact.source_path = "other.py"  # type: ignore

    def test_inferred_fact_requires_structural_evidence(self) -> None:
        inferred = InferredFact(
            inference_id="inf-001",
            category="ARCHITECTURAL_ROLE",
            statement="Handles user authentication",
            evidence_fact_ids=("fact-1234", "fact-5678"),
            model_id="gemini-2.0-flash",
        )
        self.assertEqual(inferred.inference_id, "inf-001")
        self.assertEqual(len(inferred.evidence_fact_ids), 2)
        self.assertEqual(inferred.trust_level.value, "INFERRED")


class PythonAstExtractorTests(unittest.TestCase):
    """Test AST extraction of Python modules, classes, methods, functions, and imports."""

    def setUp(self) -> None:
        self.extractor = PythonAstExtractor()

    def test_extract_python_file(self) -> None:
        code = '''"""Sample module docstring."""

import os
from sys import path as sys_path, version_info

MAX_RETRIES = 5

class BaseService:
    """Base class doc."""
    def __init__(self, name: str) -> None:
        self.name = name

    async def execute(self) -> bool:
        return True

def standalone_helper(x: int) -> int:
    return x * 2
'''
        lines = code.splitlines()
        facts, warnings = self.extractor.extract("app/services/base.py", code, lines)

        self.assertEqual(len(warnings), 0)
        self.assertTrue(len(facts) > 0)

        # Check Module Fact
        mod_facts = [f for f in facts if f.fact_type == StructuralFactType.MODULE_DEFINED]
        self.assertEqual(len(mod_facts), 1)
        self.assertEqual(mod_facts[0].details["module_name"], "app.services.base")
        self.assertTrue(mod_facts[0].details["has_docstring"])

        # Check Import Facts
        imp_facts = [f for f in facts if f.fact_type == StructuralFactType.IMPORT_DECLARED]
        self.assertEqual(len(imp_facts), 2)
        direct_imp = next(f for f in imp_facts if f.details["import_type"] == "direct")
        self.assertEqual(direct_imp.details["module"], "os")
        from_imp = next(f for f in imp_facts if f.details["import_type"] == "from")
        self.assertEqual(from_imp.details["module"], "sys")

        # Check Symbol Facts
        sym_facts = [f for f in facts if f.fact_type == StructuralFactType.SYMBOL_DECLARED]
        sym_names = {f.details["symbol_name"]: f for f in sym_facts}

        self.assertIn("MAX_RETRIES", sym_names)
        self.assertEqual(sym_names["MAX_RETRIES"].details["symbol_type"], "constant")

        self.assertIn("BaseService", sym_names)
        self.assertEqual(sym_names["BaseService"].details["symbol_type"], "class")

        self.assertIn("__init__", sym_names)
        self.assertEqual(sym_names["__init__"].details["symbol_type"], "method")
        self.assertEqual(sym_names["__init__"].details["parent_class"], "BaseService")

        self.assertIn("execute", sym_names)
        self.assertTrue(sym_names["execute"].details["is_async"])

        self.assertIn("standalone_helper", sym_names)
        self.assertEqual(sym_names["standalone_helper"].details["symbol_type"], "function")


class ManifestExtractorTests(unittest.TestCase):
    """Test static extraction from package manifests."""

    def setUp(self) -> None:
        self.extractor = ManifestExtractor()

    def test_extract_requirements_txt(self) -> None:
        content = """# Core requirements
requests>=2.28.0
fastapi==0.100.0
pydantic~=2.0
"""
        facts, warnings = self.extractor.extract("requirements.txt", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        dep_facts = [f for f in facts if f.fact_type == StructuralFactType.DEPENDENCY_DECLARED]
        self.assertEqual(len(dep_facts), 3)
        dep_map = {f.details["dependency_name"]: f.details["version_spec"] for f in dep_facts}
        self.assertEqual(dep_map["requests"], ">=2.28.0")
        self.assertEqual(dep_map["fastapi"], "==0.100.0")

    def test_extract_pyproject_toml(self) -> None:
        content = """[project]
name = "forge-sample"
version = "0.1.0"
dependencies = [
    "httpx>=0.24.0",
    "click>=8.0",
]

[project.optional-dependencies]
test = [
    "pytest>=7.0",
]
"""
        facts, warnings = self.extractor.extract("pyproject.toml", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        dep_facts = [f for f in facts if f.fact_type == StructuralFactType.DEPENDENCY_DECLARED]
        dep_map = {f.details["dependency_name"]: f.details["version_spec"] for f in dep_facts}
        self.assertIn("httpx", dep_map)
        self.assertIn("click", dep_map)
        self.assertIn("pytest", dep_map)

    def test_extract_package_json(self) -> None:
        content = json.dumps({
            "name": "sample-ui",
            "version": "1.0.0",
            "dependencies": {
                "react": "^18.2.0",
                "react-dom": "^18.2.0"
            },
            "devDependencies": {
                "typescript": "^5.0.0"
            }
        })
        facts, warnings = self.extractor.extract("package.json", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        dep_facts = [f for f in facts if f.fact_type == StructuralFactType.DEPENDENCY_DECLARED]
        dep_map = {f.details["dependency_name"]: f.details["dependency_type"] for f in dep_facts}
        self.assertEqual(dep_map["react"], "production")
        self.assertEqual(dep_map["typescript"], "dev")

    def test_extract_cargo_toml(self) -> None:
        content = """[package]
name = "sample-rust"
version = "0.1.0"

[dependencies]
serde = "1.0"
tokio = { version = "1.28", features = ["full"] }

[dev-dependencies]
tempfile = "3.5"
"""
        facts, warnings = self.extractor.extract("Cargo.toml", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        dep_facts = [f for f in facts if f.fact_type == StructuralFactType.DEPENDENCY_DECLARED]
        self.assertEqual(len(dep_facts), 3)

    def test_extract_go_mod(self) -> None:
        content = """module github.com/example/sample

go 1.21

require (
    github.com/gin-gonic/gin v1.9.1
    golang.org/x/crypto v0.14.0
)
"""
        facts, warnings = self.extractor.extract("go.mod", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        dep_facts = [f for f in facts if f.fact_type == StructuralFactType.DEPENDENCY_DECLARED]
        self.assertEqual(len(dep_facts), 2)


class GenericExtractorTests(unittest.TestCase):
    """Test generic line/regex extraction for polyglot languages."""

    def setUp(self) -> None:
        self.extractor = GenericExtractor()

    def test_extract_typescript(self) -> None:
        content = """import { useState } from 'react';
import axios from 'axios';

export interface UserProps {
    id: string;
}

export class UserService {
    getUser() {}
}
"""
        facts, warnings = self.extractor.extract("src/user.ts", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        imp_facts = [f for f in facts if f.fact_type == StructuralFactType.IMPORT_DECLARED]
        self.assertEqual(len(imp_facts), 2)

        sym_facts = [f for f in facts if f.fact_type == StructuralFactType.SYMBOL_DECLARED]
        sym_names = {f.details["symbol_name"] for f in sym_facts}
        self.assertIn("UserProps", sym_names)
        self.assertIn("UserService", sym_names)

    def test_extract_golang(self) -> None:
        content = """package auth

import (
    "fmt"
    "net/http"
)

type AuthService struct {}

func HandleLogin() {}
"""
        facts, warnings = self.extractor.extract("pkg/auth/auth.go", content, content.splitlines())
        self.assertEqual(len(warnings), 0)

        pkg_facts = [f for f in facts if f.fact_type == StructuralFactType.PACKAGE_DEFINED]
        self.assertEqual(len(pkg_facts), 1)
        self.assertEqual(pkg_facts[0].details["package_name"], "auth")


class UnderstandingSnapshotterIntegrationTests(unittest.TestCase):
    """Test full workspace scanning, topology graph generation, and drift detection."""

    def test_end_to_end_snapshot_and_reproducibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create sample files
            (tmppath / "app").mkdir()
            (tmppath / "app" / "__init__.py").write_text("# init", encoding="utf-8")
            (tmppath / "app" / "main.py").write_text(
                "import os\nfrom app.utils import add\n\ndef run():\n    return add(1, 2)\n",
                encoding="utf-8",
            )
            (tmppath / "app" / "utils.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a + b\n",
                encoding="utf-8",
            )
            (tmppath / "requirements.txt").write_text("pytest>=7.0\n", encoding="utf-8")

            workspace = Workspace(tmppath)
            snapshotter = UnderstandingSnapshotter()

            # 1. Create Snapshot
            snapshot1 = snapshotter.create_snapshot(workspace, project_id="test_proj")
            self.assertEqual(snapshot1.project_id, "test_proj")
            self.assertTrue(len(snapshot1.files) >= 4)
            self.assertTrue(len(snapshot1.facts) > 0)
            self.assertTrue(len(snapshot1.topology.nodes) > 0)
            self.assertTrue(len(snapshot1.topology.edges) > 0)

            # 2. Check Topology Nodes & Edges
            node_names = {n.name for n in snapshot1.topology.nodes}
            self.assertIn("main.py", node_names)
            self.assertIn("utils.py", node_names)
            self.assertIn("run", node_names)
            self.assertIn("add", node_names)

            # 3. Test Reproducibility
            snapshot2 = snapshotter.create_snapshot(workspace, project_id="test_proj")
            self.assertEqual(snapshot1.workspace_fingerprint, snapshot2.workspace_fingerprint)
            self.assertEqual(len(snapshot1.facts), len(snapshot2.facts))
            self.assertEqual(len(snapshot1.topology.nodes), len(snapshot2.topology.nodes))
            self.assertEqual(len(snapshot1.topology.edges), len(snapshot2.topology.edges))

            # 4. Test Drift Detection (Untouched -> Fresh)
            is_fresh, drifted = snapshotter.check_drift(snapshot1, workspace)
            self.assertTrue(is_fresh)
            self.assertEqual(len(drifted), 0)

            # 5. Modify a file -> Drift detected
            (tmppath / "app" / "utils.py").write_text(
                "def add(a: int, b: int) -> int:\n    # changed\n    return a + b\n",
                encoding="utf-8",
            )
            is_fresh2, drifted2 = snapshotter.check_drift(snapshot1, workspace)
            self.assertFalse(is_fresh2)
            self.assertIn("app/utils.py", drifted2)


if __name__ == "__main__":
    unittest.main()
