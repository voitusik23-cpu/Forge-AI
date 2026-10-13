"""The tool boundary must stay bound to the directory it was opened on.

The invariant under test
------------------------
``Workspace`` is the boundary every workspace-scoped tool read and write goes
through, and ``RunScope.validate_workspace`` is the check the agent harness applies
before it hands a workspace to a run.  Both used to compare only the *path*.

A path is not an identity.  Renaming the bound directory away and creating a
different directory at the same path leaves the path string identical, so a
boundary that compares only the path happily serves the replacement: measured
before this fix, ``Workspace.resolve_target("payload.txt")`` returned a target
inside the replacing directory and its content was readable.

RED BEFORE THE FIX: tests 01, 02, 03 and 04 fail against the tree in which
``Workspace`` captures no identity and ``validate_workspace`` compares only
``workspace.root``.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.execution.profile import ProjectExecutionProfile
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace, WorkspacePathError


class ReplacedWorkspaceIsRefusedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_wsid_")
        self.base = Path(self._temp_dir.name).resolve()
        self.workspace = self.base / "project"
        self.workspace.mkdir()
        (self.workspace / "payload.txt").write_text("original", encoding="utf-8")

    def tearDown(self) -> None:
        RunScope.release_all()
        self._temp_dir.cleanup()

    def _replace_directory(self) -> Path:
        """Move the bound directory away, then put a different one at the same path."""

        moved = self.base / "project_moved"
        os.rename(self.workspace, moved)
        self.workspace.mkdir()
        (self.workspace / "payload.txt").write_text("replacing", encoding="utf-8")
        return moved

    def _scope(self) -> RunScope:
        scope = RunScope(
            run_id="wsid-run-1",
            workspace=Workspace(str(self.workspace)),
            execution_profile=ProjectExecutionProfile(
                "wsid-profile", allowed_commands=("python",)
            ),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="wsid-anchor", description="run scope anchor"),
            ),
        )
        scope.freeze()
        return scope

    def test_01_a_bound_workspace_refuses_a_replaced_directory(self) -> None:
        workspace = Workspace(str(self.workspace))
        self._replace_directory()

        with self.assertRaises(WorkspacePathError):
            workspace.assert_available()

    def test_02_the_tool_boundary_does_not_resolve_into_the_replacement(self) -> None:
        workspace = Workspace(str(self.workspace))
        self._replace_directory()

        with self.assertRaises(WorkspacePathError):
            workspace.resolve_target("payload.txt")

    def test_03_the_replacement_content_is_never_served(self) -> None:
        workspace = Workspace(str(self.workspace))
        self._replace_directory()

        try:
            target, _normalized, _parts = workspace.resolve_target("payload.txt")
            content = target.read_text(encoding="utf-8")
        except WorkspacePathError:
            return  # the boundary refused, so nothing was read
        self.fail(f"the boundary served the replacing directory: {content!r}")

    def test_04_a_run_scope_refuses_a_workspace_replaced_at_the_same_path(self) -> None:
        scope = self._scope()
        self._replace_directory()

        with self.assertRaises(RunScopeError):
            scope.validate_workspace(Workspace(str(self.workspace)))


class UnchangedWorkspaceStillWorksTests(unittest.TestCase):
    """Positive controls: binding an identity must not refuse normal operation."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_wsid_ok_")
        self.workspace = Path(self._temp_dir.name).resolve()
        (self.workspace / "payload.txt").write_text("original", encoding="utf-8")

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_05_an_untouched_workspace_resolves_and_reads(self) -> None:
        workspace = Workspace(str(self.workspace))

        target, normalized, parts = workspace.resolve_target("payload.txt")

        self.assertEqual(normalized, "payload.txt")
        self.assertEqual(parts, ("payload.txt",))
        self.assertEqual(target.read_text(encoding="utf-8"), "original")

    def test_06_binding_happens_at_construction_not_before(self) -> None:
        """A boundary built after a replacement binds to what is there now.

        That is correct, not a hole: the identity is captured when the boundary is
        created, exactly as the run scope freezes it when the scope is frozen.
        """

        workspace = Workspace(str(self.workspace))

        self.assertTrue(workspace.assert_available() is None)
        self.assertTrue(
            workspace.resolve_target("payload.txt")[0].read_text(encoding="utf-8")
            == "original"
        )


if __name__ == "__main__":
    unittest.main()
