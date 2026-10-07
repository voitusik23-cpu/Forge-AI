"""Tests for UnderstandingSnapshot summary utilities."""

from __future__ import annotations

import unittest
from pathlib import Path
import tempfile

from app.tools.workspace import Workspace
from app.understanding.snapshotter import UnderstandingSnapshotter
from app.understanding.summary import format_snapshot_markdown, summarize_snapshot


class UnderstandingSummaryTests(unittest.TestCase):
    """Verify summarize_snapshot and format_snapshot_markdown."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.workspace = Workspace(self.workspace_root)

        src_dir = self.workspace_root / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "calculator.py").write_text(
            "import os\n\ndef multiply(a: int, b: int) -> int:\n    return a * b\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_summarize_snapshot(self) -> None:
        snapshotter = UnderstandingSnapshotter()
        snapshot = snapshotter.create_snapshot(self.workspace, project_id="test-summary")

        summary = summarize_snapshot(snapshot)
        self.assertEqual(summary["project_id"], "test-summary")
        self.assertEqual(summary["total_files"], 2)
        self.assertTrue(summary["total_symbols"] >= 1)
        self.assertTrue(summary["total_modules"] >= 1)
        self.assertTrue(summary["total_packages"] >= 1)
        self.assertTrue(summary["total_imports"] >= 1)

    def test_format_snapshot_markdown(self) -> None:
        snapshotter = UnderstandingSnapshotter()
        snapshot = snapshotter.create_snapshot(self.workspace, project_id="test-summary")

        md = format_snapshot_markdown(snapshot)
        self.assertIn("# Project Understanding Summary: test-summary", md)
        self.assertIn("Snapshot ID", md)
        self.assertIn("SYMBOL_DECLARED", md)


if __name__ == "__main__":
    unittest.main()
