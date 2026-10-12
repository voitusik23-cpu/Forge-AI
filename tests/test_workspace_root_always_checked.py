"""Regression tests: the frozen workspace root is checked regardless of adapter isolation.

Before the fix, ``ExecutionCoordinator`` skipped ``scope.validate_workspace_root(...)``
whenever the adapter reported ``_isolate_workspace = True``:

```python
if not bool(getattr(self._adapter, "_isolate_workspace", False)):
    scope.validate_workspace_root(workspace_root)
```

The production composition is the isolating one, so the single check tying the *executed
tree* to the *frozen perimeter* did not run on the path that actually matters. A caller
could name any absolute directory, and the adapter would copy it into a scratch tree and
run there -- a fail-open in the one place whose absence the contract calls fatal.

Isolation is not a substitute for the comparison: it says the run happens in a private
copy, not that the copy came from the frozen root. These tests assert the comparison now
holds in both adapter modes, and that the refusal names the condition.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.execution.adapter import LocalExecutionAdapter
from app.execution.workspace_manager import EphemeralWorkspaceManager
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.request import ExecutionStatus
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest
from app.runtime.run_scope import RunScope, RunScopeError
from app.tasks.specification import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.workspace import Workspace


def _profile(workspace: Path) -> ProjectExecutionProfile:
    # `INTERPRET_TEXT` is declared explicitly because `python -c` carries an
    # interpretive payload: the classifier derives INTERPRET_TEXT for it, and the policy
    # refuses an invocation whose profile does not grant every capability the intent
    # carries. Declaring the capability is the documented way to authorize this
    # invocation, and it keeps the legitimate-path test honest instead of weakening the
    # policy to make the test pass.
    return ProjectExecutionProfile(
        profile_id="sec10-probe",
        working_directory=".",
        allowed_commands=("python",),
        capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
    )


class WorkspaceRootIsAlwaysCheckedTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.frozen_root = self.base / "frozen"
        self.frozen_root.mkdir()
        self.other_root = self.base / "other"
        self.other_root.mkdir()
        (self.other_root / "other.txt").write_text("other\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def _scope(self, run_id: str) -> RunScope:
        scope = RunScope(
            run_id=run_id,
            workspace=Workspace(self.frozen_root),
            execution_profile=_profile(self.frozen_root),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(
                AcceptanceCriterion(
                    criterion_id="sec10-anchor", description="run scope anchor"
                ),
            ),
        )
        # The coordinator refuses a scope that was never frozen: an unfrozen scope is
        # not yet an authority boundary.
        scope.freeze()
        return scope

    def _request(self, run_id: str) -> ExecutionRequest:
        # The profile must be on the request as well as in the scope: the coordinator
        # refuses a request with no profile (`profile_missing`) before it reaches the
        # workspace-root comparison, which is what made this test look like a regression
        # in the first place.
        return ExecutionRequest(
            request_id="sec10-request",
            command=("python", "-c", "print('ok')"),
            working_directory=".",
            profile=_profile(self.frozen_root),
        )

    def _coordinator(self, *, isolate: bool) -> ExecutionCoordinator:
        adapter = LocalExecutionAdapter(
            workspace_manager=EphemeralWorkspaceManager(
                base_scratch_dir=self.base / "scratch"
            ),
            isolate_workspace=isolate,
        )
        return ExecutionCoordinator(adapter)

    def test_01_a_mismatched_root_is_denied_with_an_isolating_adapter(self):
        """The exact fail-open: isolation must not waive the comparison."""

        run_id = "sec10-isolated"
        coordinator = self._coordinator(isolate=True)
        result = coordinator.execute(
            self._request(run_id),
            workspace_root=self.other_root,
            run_id=run_id,
            allowed_commands=("python",),
            run_scope=self._scope(run_id),
        )
        self.assertNotEqual(result.status.value, "success")
        self.assertEqual(result.failure_reason, "workspace_root_mismatch")

    def test_02_a_mismatched_root_is_denied_with_a_non_isolating_adapter(self):
        run_id = "sec10-direct"
        coordinator = self._coordinator(isolate=False)
        result = coordinator.execute(
            self._request(run_id),
            workspace_root=self.other_root,
            run_id=run_id,
            allowed_commands=("python",),
            run_scope=self._scope(run_id),
        )
        self.assertNotEqual(result.status.value, "success")
        self.assertEqual(result.failure_reason, "workspace_root_mismatch")

    def test_03_nothing_is_staged_when_the_root_mismatches(self):
        """A denial must leave no scratch tree: the refusal precedes the copy."""

        run_id = "sec10-no-scratch"
        base_scratch = self.base / "scratch"
        coordinator = self._coordinator(isolate=True)
        result = coordinator.execute(
            self._request(run_id),
            workspace_root=self.other_root,
            run_id=run_id,
            allowed_commands=("python",),
            run_scope=self._scope(run_id),
        )
        self.assertNotEqual(result.status.value, "success")
        leftovers = list(base_scratch.glob("forge_scratch_*")) if base_scratch.exists() else []
        self.assertEqual(leftovers, [], f"unexpected scratch trees: {leftovers}")

    def test_04_the_matching_root_still_executes(self):
        """The fix must not break the legitimate path it was protecting.

        A POLICY IS STATED EXPLICITLY, as the mandatory-policy contract requires. The
        workspace-root change and the approval-policy change are independent, but they
        meet here: `ExecutionCoordinator` now denies a dispatch with no
        `ApprovalPolicy`, so a fixture that omitted one could no longer reach the
        legitimate path at all -- it was denied with
        `approval_policy_required` before the root comparison mattered. Stating
        `ApprovalPolicy()` (the default: no tool requires approval) keeps this test
        about the workspace root, which is its subject, and grants nothing extra --
        the root comparison still runs first, which is exactly why the denial cases
        above still report `workspace_root_mismatch` rather than a policy reason.
        """

        run_id = "sec10-ok"
        coordinator = self._coordinator(isolate=True)
        result = coordinator.execute(
            self._request(run_id),
            workspace_root=self.frozen_root,
            run_id=run_id,
            allowed_commands=("python",),
            approval_policy=ApprovalPolicy(),
            run_scope=self._scope(run_id),
        )
        self.assertEqual(
            result.status.value, ExecutionStatus.SUCCESS.value, result.stderr
        )

    def test_05_an_unrelated_scope_violation_keeps_its_own_reason(self):
        """Only a workspace-root refusal is reclassified, not every scope failure."""

        run_id = "sec10-other"
        coordinator = self._coordinator(isolate=True)
        result = coordinator.execute(
            self._request(run_id),
            workspace_root=self.frozen_root,
            run_id=run_id,
            # A command outside the frozen perimeter is a different refusal.
            allowed_commands=("definitely-not-python",),
            run_scope=self._scope(run_id),
        )
        self.assertNotEqual(result.status.value, "success")
        self.assertEqual(result.failure_reason, "run_scope_violation")

    def test_06_the_scope_helper_still_rejects_a_different_root(self):
        """The underlying check is unchanged; only its reachability was the bug."""

        scope = self._scope("sec10-unit")
        scope.validate_workspace_root(self.frozen_root)
        with self.assertRaises(RunScopeError):
            scope.validate_workspace_root(self.other_root)

    def test_07_resolved_path_escape_is_denied_without_absolute_path(self):
        manager = EphemeralWorkspaceManager(source_workspace_root=self.frozen_root)
        with manager:
            with self.assertRaisesRegex(ValueError, "Path traversal detected") as caught:
                manager.resolve_path("..\\other\\outside.txt", root=self.frozen_root)
        self.assertNotIn(str(self.frozen_root), str(caught.exception))
        self.assertNotIn(str(self.other_root), str(caught.exception))

    def test_08_reparse_point_is_denied_before_resolve(self):
        manager = EphemeralWorkspaceManager(source_workspace_root=self.frozen_root)
        with patch.object(
            EphemeralWorkspaceManager,
            "_is_reparse_point",
            staticmethod(lambda path: path.name == "junction"),
        ):
            with self.assertRaisesRegex(ValueError, "reparse point") as caught:
                manager.resolve_path("junction\\outside.txt", root=self.frozen_root)
        self.assertNotIn(str(self.frozen_root), str(caught.exception))
        self.assertNotIn(str(self.other_root), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
