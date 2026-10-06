"""Security and adversarial tests for Project Understanding subsystem."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from app.tools.workspace import Workspace
from app.understanding import (
    BoundedProjectScanner,
    ScanLimits,
    StructuralFactType,
    UnderstandingSnapshotter,
)


class UnderstandingSecurityTests(unittest.TestCase):
    """Adversarial tests ensuring isolation, secret safety, and resource bounding."""

    def test_secret_files_are_excluded_from_indexing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".env").write_text("SECRET_KEY=supersecret123\n", encoding="utf-8")
            (tmppath / ".env.production").write_text("DATABASE_URL=postgres://pwd@host/db\n", encoding="utf-8")
            (tmppath / "service_account.json").write_text('{"private_key": "-----BEGIN RSA PRIVATE KEY-----"}\n', encoding="utf-8")
            (tmppath / "server.key").write_text("-----BEGIN PRIVATE KEY-----\n", encoding="utf-8")
            (tmppath / "id_rsa").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n", encoding="utf-8")
            (tmppath / "main.py").write_text("def hello(): pass\n", encoding="utf-8")

            workspace = Workspace(tmppath)
            scanner = BoundedProjectScanner()
            files, facts, manifests, warnings = scanner.scan(workspace)

            # Facts must NOT contain any secret content or facts from secret files
            secret_facts = [
                f for f in facts
                if f.source_path in (".env", ".env.production", "service_account.json", "server.key", "id_rsa")
            ]
            self.assertEqual(len(secret_facts), 0)

            # Warning issued for excluded secret files
            secret_warnings = [w for w in warnings if w.warning_type == "SECRET_FILE_EXCLUDED"]
            self.assertEqual(len(secret_warnings), 5)

            # Serialized output must not contain private key strings
            snapshotter = UnderstandingSnapshotter()
            snapshot = snapshotter.create_snapshot(workspace)
            serialized = json.dumps(snapshot.to_dict())
            self.assertNotIn("supersecret123", serialized)
            self.assertNotIn("BEGIN RSA PRIVATE KEY", serialized)
            self.assertNotIn("BEGIN OPENSSH PRIVATE KEY", serialized)

    def test_static_analysis_never_executes_target_code(self) -> None:
        """Prove that static analysis never executes module-level side effects."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            side_effect_file = tmppath / "side_effect_marker.txt"

            # Malicious code that would create a file if executed
            malicious_code = f"""
import os
# If executed, this would create a file:
with open(r"{side_effect_file}", "w") as f:
    f.write("EXECUTED")

def regular_function():
    return 42
"""
            (tmppath / "malicious.py").write_text(malicious_code, encoding="utf-8")

            workspace = Workspace(tmppath)
            snapshotter = UnderstandingSnapshotter()
            snapshot = snapshotter.create_snapshot(workspace)

            # Verify side-effect file was NOT created (no execution occurred)
            self.assertFalse(side_effect_file.exists())

            # Verify AST extraction still succeeded
            sym_facts = [f for f in snapshot.facts if f.fact_type == StructuralFactType.SYMBOL_DECLARED]
            self.assertTrue(any(f.details["symbol_name"] == "regular_function" for f in sym_facts))

    def test_symlink_escape_is_safely_ignored(self) -> None:
        """Symlinks pointing outside workspace root must not be traversed."""
        with tempfile.TemporaryDirectory() as outside_dir, tempfile.TemporaryDirectory() as ws_dir:
            outside_path = Path(outside_dir)
            ws_path = Path(ws_dir)

            (outside_path / "outside_secret.txt").write_text("EXTERNAL_DATA", encoding="utf-8")
            (ws_path / "valid.py").write_text("x = 1\n", encoding="utf-8")

            # Try to create symlink to outside file if OS allows
            symlink_created = False
            try:
                (ws_path / "escape_symlink.txt").symlink_to(outside_path / "outside_secret.txt")
                symlink_created = True
            except (OSError, NotImplementedError):
                pass

            workspace = Workspace(ws_path)
            scanner = BoundedProjectScanner()
            files, facts, manifests, warnings = scanner.scan(workspace)

            if symlink_created:
                escape_warnings = [w for w in warnings if w.warning_type == "SYMLINK_ESCAPE_IGNORED"]
                self.assertTrue(len(escape_warnings) > 0)

            # External data never in facts
            self.assertFalse(any("outside_secret" in f.source_path for f in facts))

    def test_file_size_limit_and_resource_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create a 2MB file
            huge_content = "x = 1\n" * (400 * 1024)
            (tmppath / "huge_file.py").write_text(huge_content, encoding="utf-8")
            (tmppath / "small_file.py").write_text("y = 2\n", encoding="utf-8")

            workspace = Workspace(tmppath)
            # Limit max file size to 100 KB
            limits = ScanLimits(max_file_size_bytes=100 * 1024)
            scanner = BoundedProjectScanner(limits=limits)
            files, facts, manifests, warnings = scanner.scan(workspace)

            size_warnings = [w for w in warnings if w.warning_type == "FILE_EXCEEDS_MAX_SIZE"]
            self.assertEqual(len(size_warnings), 1)
            self.assertEqual(size_warnings[0].file_path, "huge_file.py")

            # huge_file.py was NOT parsed into AST symbols
            huge_symbols = [
                f for f in facts
                if f.source_path == "huge_file.py" and f.fact_type == StructuralFactType.SYMBOL_DECLARED
            ]
            self.assertEqual(len(huge_symbols), 0)

            # small_file.py WAS parsed
            small_symbols = [f for f in facts if f.source_path == "small_file.py"]
            self.assertTrue(len(small_symbols) > 0)

    def test_binary_files_and_null_bytes_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Write binary bytes
            (tmppath / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
            (tmppath / "fake_script.py").write_bytes(b"# python\x00malicious_binary_content")

            workspace = Workspace(tmppath)
            scanner = BoundedProjectScanner()
            files, facts, manifests, warnings = scanner.scan(workspace)

            # png is in files list with fingerprint
            self.assertTrue(any(f.relative_path == "image.png" for f in files))

            # but zero AST facts for binary / null byte files
            non_inventory_facts = [
                f for f in facts
                if f.source_path in ("image.png", "fake_script.py")
                and f.fact_type != StructuralFactType.FILE_INVENTORIED
            ]
            self.assertEqual(len(non_inventory_facts), 0)

    def test_syntax_error_in_source_does_not_crash_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "corrupted.py").write_text("def broken_func(:\n    return\n", encoding="utf-8")
            (tmppath / "good.py").write_text("def good_func():\n    return 1\n", encoding="utf-8")

            workspace = Workspace(tmppath)
            snapshotter = UnderstandingSnapshotter()
            snapshot = snapshotter.create_snapshot(workspace)

            syntax_warnings = [w for w in snapshot.warnings if w.warning_type == "PYTHON_SYNTAX_ERROR"]
            self.assertEqual(len(syntax_warnings), 1)
            self.assertEqual(syntax_warnings[0].file_path, "corrupted.py")

            # good.py was successfully extracted despite corrupted.py in workspace
            good_symbols = [
                f for f in snapshot.facts
                if f.source_path == "good.py" and f.fact_type == StructuralFactType.SYMBOL_DECLARED
            ]
            self.assertEqual(len(good_symbols), 1)
            self.assertEqual(good_symbols[0].details["symbol_name"], "good_func")


if __name__ == "__main__":
    unittest.main()
