"""The workspace path validator must not silently rewrite the caller's path.

The invariant under test
------------------------
``normalize_workspace_relative_path`` is the single canonical validator behind the
tool boundary (:class:`app.tools.workspace.Workspace`), the execution plane
(``app.execution.policy``/``profile``/``intent``/``request``/``sandbox``) and the
step graph.  A path it accepts is a path those layers will open.

Windows itself normalizes trailing spaces and dots away when a file is created,
so ``"a.txt "`` and ``"a.txt"`` name the *same* file on disk.  Any layer that
screens names -- credential names, ``.env``, ``.git``, protected areas -- sees
two different strings for one file.  A validator that strips the difference
instead of refusing it therefore hands a bypass to every caller that screens the
name it was given, and aliases two inputs onto one target.

The declared invariant is in the validator's own docstring: "Rejects trailing
dot or space forms in path components."  These tests hold the implementation to
it.

RED BEFORE THE FIX: tests 01, 02, 03, 04, 05, 06 and 06b fail against the tree in
which ``normalize_workspace_relative_path`` starts with ``value.strip()``.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.tools.workspace import Workspace, WorkspacePathError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class NormalizerRefusesWhitespaceAliasingTests(unittest.TestCase):
    """The normalizer rejects the shapes Windows would silently rewrite."""

    def test_01_trailing_space_is_refused_not_trimmed(self) -> None:
        """``"a.txt "`` must not become ``"a.txt"``."""

        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path("a.txt ")

    def test_02_surrounding_whitespace_is_refused(self) -> None:
        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path("  a.txt  ")

    def test_03_trailing_space_in_a_nested_component_is_refused(self) -> None:
        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path("dir/inner.txt ")

    def test_04_a_protected_name_with_a_trailing_space_is_refused(self) -> None:
        """``".env "`` names the real ``.env`` on Windows: it must not normalize to it."""

        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path(".env ")
        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path(".git ")

    def test_05_leading_space_keeps_its_literal_name(self) -> None:
        """A leading space is a legal filename character: it must stay literal.

        Accepting it is correct; *rewriting* it to a different name is not.  The
        earlier tool boundary behaved the same way, so this also pins that the fix
        does not over-reject.
        """

        self.assertEqual(normalize_workspace_relative_path(" a.txt"), " a.txt")
        self.assertNotEqual(normalize_workspace_relative_path(" a.txt"), "a.txt")

    def test_06_the_tool_boundary_refuses_the_aliased_target(self) -> None:
        workspace = Workspace(str(REPOSITORY_ROOT))
        with self.assertRaises(WorkspacePathError):
            workspace.resolve_target("a.txt ")

    def test_06b_a_leading_space_target_is_a_different_file(self) -> None:
        """``" .env"`` is a legal name and must not be reported as the real ``.env``."""

        workspace = Workspace(str(REPOSITORY_ROOT))
        target, normalized, _parts = workspace.resolve_target(" .env")
        self.assertEqual(normalized, " .env")
        self.assertNotEqual(target, REPOSITORY_ROOT / ".env")


class NormalizerKeepsLegitimatePathsTests(unittest.TestCase):
    """Positive controls: the strictness above must not refuse real names."""

    def test_07_an_ordinary_path_still_normalizes(self) -> None:
        self.assertEqual(normalize_workspace_relative_path("a.txt"), "a.txt")
        self.assertEqual(normalize_workspace_relative_path("dir/a.txt"), "dir/a.txt")
        self.assertEqual(normalize_workspace_relative_path("dir\\a.txt"), "dir/a.txt")

    def test_08_interior_spaces_are_a_legal_file_name(self) -> None:
        self.assertEqual(normalize_workspace_relative_path("my file.txt"), "my file.txt")

    def test_09_the_tool_boundary_still_resolves_a_legal_target(self) -> None:
        workspace = Workspace(str(REPOSITORY_ROOT))
        target, normalized, parts = workspace.resolve_target("my file.txt")
        self.assertEqual(normalized, "my file.txt")
        self.assertEqual(parts, ("my file.txt",))
        self.assertEqual(target.name, "my file.txt")
        self.assertEqual(target.parent, REPOSITORY_ROOT)

    def test_10_root_still_normalizes_to_dot(self) -> None:
        self.assertEqual(normalize_workspace_relative_path("."), ".")
        # A trailing separator stays refused, exactly as before this change.
        with self.assertRaises(PathSecurityError):
            normalize_workspace_relative_path("./")


if __name__ == "__main__":
    unittest.main()
