"""A replaced workspace directory must be reported as a workspace-root refusal.

The invariant under test
------------------------
``RunScope`` freezes the workspace path *and* its filesystem identity.  Replacing
the directory at the same path therefore passes the path comparison and is caught
by the identity comparison.  Both refusals mean the same thing to an operator --
the executed tree and the frozen perimeter disagree -- so both must be reported
with the dedicated ``workspace_root_mismatch`` reason.

Before this fix only the path comparison produced a message containing the literal
``workspace root`` token the coordinator matches on, so a replaced directory was
reported as a generic ``run_scope_violation``: the denial was correct, the reason an
operator reads was not.  No test covered it.

RED BEFORE THE FIX: this test fails against the tree in which the identity refusal
reads "run scope cannot change the workspace identity".
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.workspace import Workspace

_INTERPRETER_DIR = str(Path(sys.executable).resolve().parent)


class ReplacedWorkspaceDirectoryReasonTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_reason_")
        self.workspace = Path(self._temp_dir.name).resolve()

    def tearDown(self) -> None:
        RunScope.release_all()
        self._temp_dir.cleanup()

    @staticmethod
    def _request() -> ExecutionRequest:
        return ExecutionRequest(
            command=("python", "-c", "pass"),
            profile=ProjectExecutionProfile(
                "reason-profile",
                allowed_commands=("python",),
                environment_variables={"PATH": _INTERPRETER_DIR},
            ),
        )

    @staticmethod
    def _scope(run_id: str, workspace: Path) -> RunScope:
        scope = RunScope(
            run_id=run_id,
            workspace=Workspace(str(workspace)),
            execution_profile=ProjectExecutionProfile(
                "reason-scope-profile",
                allowed_commands=("python",),
                environment_variables={"PATH": _INTERPRETER_DIR},
            ),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="reason-anchor", description="run scope anchor"),
            ),
        )
        scope.freeze()
        return scope

    def test_a_replaced_directory_is_a_workspace_root_mismatch(self) -> None:
        request = self._request()
        scope = self._scope("reason-run-1", self.workspace)

        # Same path string, different directory: move the frozen one away and put a
        # new, empty directory at the same path.
        moved = self.workspace.parent / f"{self.workspace.name}_moved"
        os.rename(self.workspace, moved)
        self.workspace.mkdir()
        try:
            result = ExecutionCoordinator(adapter=LocalExecutionAdapter()).execute(
                request,
                allowed_commands=frozenset({"python"}),
                workspace_root=self.workspace,
                run_id="reason-run-1",
                run_scope=scope,
                approval_policy=ApprovalPolicy(),
            )
        finally:
            shutil.rmtree(moved, ignore_errors=True)

        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(result.failure_reason, "workspace_root_mismatch")
        self.assertIsNone(result.exit_code)


if __name__ == "__main__":
    unittest.main()
