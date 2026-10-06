"""Block 3.1 contracts: run identity and one-run/one-scope.

These tests encode two fixed architectural decisions. They are contracts, not
descriptions of incidental behaviour:

DECISION 1 - Run identity
    ``Run.run_id == RunScope.run_id``, and the identity is established *before*
    the scope is created. A scope never generates, substitutes, or recovers a run
    id, and an entry point that receives an empty, missing, or mismatched run id
    fails closed.

DECISION 2 - One Run, One Immutable Scope
    A run has exactly one immutable scope. Executors may only use a *subset* of
    that scope's permissions. They may not replace the scope, widen commands or
    tools, change the workspace, profile, or acceptance criteria, register a
    second scope for the same run id, or take a union with a caller's own set.
"""

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.identity import CommandIdentity
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.runtime.run_scope import (
    RunScope,
    RunScopeError,
    require_active_scope,
)
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace

CRITERION = AcceptanceCriterion(criterion_id="crit-1", description="acceptance")


class RunScopeContractTestCase(unittest.TestCase):
    """Shared fixtures. Every run id is established before its scope is created."""

    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.workspace = Workspace(self.root)
        self.other_workspace = Workspace(Path(tempfile.mkdtemp(prefix="scope_other_")))

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def make_scope(
        self,
        run_id: str,
        *,
        commands=(),
        tools=(),
        profile=None,
        workspace=None,
        criteria=(CRITERION,),
    ) -> RunScope:
        """Build a scope for an already-established run id."""
        entries = tuple(commands)
        if profile is None:
            # The profile must permit exactly what the test declares.
            declared = {getattr(e, "executable", e) for e in entries} or {"python"}
            profile = ProjectExecutionProfile(
                "contract-profile",
                allowed_commands=tuple(sorted(str(d) for d in declared)),
            )
        return RunScope(
            run_id=run_id,
            workspace=workspace or self.workspace,
            execution_profile=profile,
            allowed_tool_ids=frozenset(tools),
            allowed_execution_commands=frozenset(entries),
            acceptance_criteria=tuple(criteria),
        )


class Decision1RunIdentityTests(RunScopeContractTestCase):
    def test_scope_requires_a_run_id(self) -> None:
        with self.assertRaisesRegex(RunScopeError, "run_id must be a non-empty string"):
            self.make_scope("")
        with self.assertRaisesRegex(RunScopeError, "run_id must be a non-empty string"):
            self.make_scope("   ")

    def test_scope_never_supplies_a_run_id_for_the_caller(self) -> None:
        """A scope has no API that returns a run id to be used as a fallback."""
        for name in ("resolve_run_id", "ensure_run_id", "run_id_for", "generate_run_id"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(RunScope, name))
                self.assertFalse(hasattr(self.make_scope("run-1"), name))

    def test_entry_point_rejects_an_empty_run_id(self) -> None:
        scope = self.make_scope("run-identity", commands=(sys.executable,))
        scope.freeze()
        for bad in ("", "   "):
            with self.subTest(run_id=repr(bad)):
                with self.assertRaisesRegex(RunScopeError, "run_id must be a non-empty string"):
                    require_active_scope(bad, scope)

    def test_entry_point_rejects_a_mismatched_run_id(self) -> None:
        scope = self.make_scope("run-identity", commands=(sys.executable,))
        scope.freeze()
        with self.assertRaisesRegex(RunScopeError, "does not belong to this run"):
            require_active_scope("a-different-run", scope)

    def test_entry_point_rejects_a_missing_scope(self) -> None:
        with self.assertRaisesRegex(RunScopeError, "requires an active RunScope"):
            require_active_scope("run-identity", None)

    def test_coordinator_does_not_derive_run_id_from_the_scope(self) -> None:
        """An empty run id fails closed instead of adopting the scope's id."""
        scope = self.make_scope("run-from-scope", commands=(sys.executable,))
        scope.freeze()
        coordinator = ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=False)
        )
        result = coordinator.execute(
            ExecutionRequest(
                command=(sys.executable, "-c", "print(1)"),
                profile=ProjectExecutionProfile(
                    "contract-profile", allowed_commands=(sys.executable,)
                ),
            ),
            workspace_root=self.root,
            run_id="",
            allowed_commands=frozenset({sys.executable}),
            run_scope=scope,
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertEqual(result.metadata.get("denial_reason"), "run_scope_violation")


class Decision2OneScopeTests(RunScopeContractTestCase):
    def test_subset_command_set_is_allowed(self) -> None:
        scope = self.make_scope("run-subset", commands=("python", "pytest", "git", "npm"))
        scope.freeze()
        for subset in (("python",), ("python", "pytest"), ("git",), ("npm",)):
            with self.subTest(subset=subset):
                scope.validate_command_set(subset)

    def test_widening_command_set_is_rejected(self) -> None:
        scope = self.make_scope("run-widen", commands=("python", "pytest", "git", "npm"))
        scope.freeze()
        with self.assertRaisesRegex(RunScopeError, "additional execution commands"):
            scope.validate_command_set(("python", "powershell"))

    def test_subset_tool_set_is_allowed_and_widening_is_rejected(self) -> None:
        scope = self.make_scope("run-tools", tools=("read_project_file", "write_project_file"))
        scope.freeze()
        scope.validate_tool_set(("read_project_file",))
        with self.assertRaisesRegex(RunScopeError, "additional tools"):
            scope.validate_tool_set(("read_project_file", "delete_project_file"))

    def test_workspace_and_criteria_and_profile_cannot_change(self) -> None:
        profile = ProjectExecutionProfile("contract-profile", allowed_commands=("python",))
        scope = self.make_scope("run-fixed", commands=("python",), profile=profile)
        scope.freeze()
        with self.assertRaisesRegex(RunScopeError, "workspace root"):
            scope.validate_workspace(self.other_workspace)
        with self.assertRaisesRegex(RunScopeError, "acceptance criteria"):
            scope.validate_acceptance_criteria(
                (AcceptanceCriterion(criterion_id="crit-1", description="changed"),)
            )
        with self.assertRaisesRegex(RunScopeError, "network access"):
            scope.validate_execution_profile(
                ProjectExecutionProfile(
                    "contract-profile", allowed_commands=("python",), network_access=True
                )
            )

    def test_second_scope_for_the_same_run_id_is_rejected(self) -> None:
        first = self.make_scope("run-one", commands=("python",))
        first.freeze()
        second = self.make_scope("run-one", commands=("python", "git"))
        with self.assertRaisesRegex(RunScopeError, "already frozen"):
            second.freeze()

    def test_identical_scope_for_the_same_run_id_is_idempotent(self) -> None:
        first = self.make_scope("run-same", commands=("python",))
        first.freeze()
        twin = self.make_scope("run-same", commands=("python",))
        self.assertEqual(twin.freeze(), first.fingerprint)

    def test_replace_and_copy_cannot_register_a_wider_scope(self) -> None:
        import copy

        scope = self.make_scope("run-forge", commands=("python",))
        scope.freeze()
        # A genuinely wider scope: both the profile and the command set grow.
        wider = dataclasses.replace(
            scope,
            execution_profile=ProjectExecutionProfile(
                "contract-profile", allowed_commands=("python", "git")
            ),
            allowed_execution_commands=frozenset({"python", "git"}),
        )
        with self.assertRaisesRegex(RunScopeError, "already frozen"):
            wider.freeze()
        with self.assertRaises(RunScopeError):
            wider.assert_current()
        clone = copy.deepcopy(scope)
        self.assertEqual(clone.fingerprint, scope.fingerprint)
        clone.assert_current()

    def test_no_union_of_caller_scopes(self) -> None:
        """A caller's own set must not be merged into the frozen perimeter."""
        scope = self.make_scope("run-no-union", commands=("python",))
        scope.freeze()
        with self.assertRaisesRegex(RunScopeError, "additional execution commands"):
            scope.validate_command_set(("python", "git"))
        # The perimeter is unchanged by the rejected attempt.
        self.assertEqual(scope.allowed_execution_commands, frozenset({"python"}))


class CaseNGatingTest(RunScopeContractTestCase):
    """Case N: in-scope execution is ALLOWED, out-of-scope is DENIED."""

    def _coordinator(self) -> ExecutionCoordinator:
        return ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=False)
        )

    def _request(self, command, profile) -> ExecutionRequest:
        return ExecutionRequest(command=command, profile=profile)

    def test_in_scope_execution_is_allowed(self) -> None:
        command = (sys.executable, "-c", "print(1)")
        identity = CommandIdentity(command[0], command[1:])
        profile = ProjectExecutionProfile("case-n", allowed_commands=(identity,))
        scope = self.make_scope(
            "run-case-n",
            commands=(identity,),
            profile=profile,
        )
        scope.freeze()

        result = self._coordinator().execute(
            self._request(command, profile),
            workspace_root=self.root,
            run_id="run-case-n",
            allowed_commands=frozenset({identity}),
            run_scope=scope,
        )
        self.assertEqual(
            result.outcome_status,
            ExecutionOutcomeStatus.EXECUTION_SUCCESS,
            msg=f"denial={result.metadata.get('denial_reason')}",
        )

    def test_out_of_scope_execution_is_denied(self) -> None:
        command = (sys.executable, "-c", "print(1)")
        identity = CommandIdentity(command[0], command[1:])
        profile = ProjectExecutionProfile("case-n", allowed_commands=(identity,))
        scope = self.make_scope("run-case-n-deny", commands=(identity,), profile=profile)
        scope.freeze()

        result = self._coordinator().execute(
            self._request(command, profile),
            workspace_root=self.root,
            run_id="run-case-n-deny",
            allowed_commands=frozenset({identity}),
            run_scope=self.make_scope(
                "run-case-n-deny",
                commands=(identity,),
                profile=profile,
                tools=("read_project_file",),
            ),
        )
        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)


if __name__ == "__main__":
    unittest.main()
