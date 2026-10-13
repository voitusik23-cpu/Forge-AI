"""A bare executable must be pinned to a real path, or it must not be authorized.

The invariant under test
------------------------
``ExecutionIntent.resolved_executable`` is part of the immutable fingerprint an
approver signs.  The adapter launches that pinned path.  The property that makes
the pin worth anything is: **the image named at authorization is the image
launched**.  A bare name cannot carry that property, because the name is resolved
by the operating system at spawn time against the *child's* environment, which is
built from a whitelist and need not be the environment the pin was resolved in.

So an unresolvable bare executable must fail closed at intent construction, before
any approval can be bound to it.  Before this fix the builder fell back to the
bare logical name, ``adapter`` launched it through its ``or intent.executable``
fallback, and one approved fingerprint could execute two different images
depending on PATH.  An explicitly declared empty ``PATH`` is treated the same way:
it means "search nowhere", not "search the process PATH".

RED BEFORE THE FIX: tests 02, 03 and 05 fail against the tree in which
``_resolve_executable`` ends with ``... if resolved else self._executable`` and the
adapter launches ``intent.resolved_executable or intent.executable``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.intent import IntentBuilder
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.workspace import Workspace

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# The interpreter running these tests: a real, existing, resolvable executable.
_INTERPRETER_DIR = Path(sys.executable).resolve().parent
# A directory that exists and certainly contains no executable we would look for.
_EMPTY_SEARCH_DIR = (REPOSITORY_ROOT / "docs").resolve()

_UNPINNABLE = "forge-not-an-installed-executable"


def _request(executable: str, *, path_value: str) -> ExecutionRequest:
    profile = ProjectExecutionProfile(
        "pin-profile",
        allowed_commands=(executable,),
        environment_variables={"PATH": path_value},
    )
    return ExecutionRequest(command=(executable,), profile=profile)


def _scope(run_id: str, executable: str, *, path_value: str, workspace_root: Path) -> RunScope:
    scope = RunScope(
        run_id=run_id,
        workspace=Workspace(str(workspace_root)),
        execution_profile=ProjectExecutionProfile(
            "pin-scope-profile",
            allowed_commands=(executable,),
            environment_variables={"PATH": path_value},
        ),
        allowed_execution_commands=frozenset({executable}),
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="pin-anchor", description="run scope anchor"),
        ),
    )
    scope.freeze()
    return scope


class BareExecutableIsPinnedTests(unittest.TestCase):
    def test_01_a_resolvable_bare_executable_is_pinned_to_an_absolute_path(self) -> None:
        request = _request("python", path_value=str(_INTERPRETER_DIR))
        intent = IntentBuilder.from_request(request, request.profile).build()

        self.assertTrue(os.path.isabs(intent.resolved_executable))
        self.assertIn("python", intent.resolved_executable.lower())
        # The logical identity stays available for policy matching.
        self.assertEqual(intent.executable, "python")

    def test_02_an_unresolvable_bare_executable_is_left_unpinned(self) -> None:
        request = _request(_UNPINNABLE, path_value=str(_EMPTY_SEARCH_DIR))
        intent = IntentBuilder.from_request(request, request.profile).build()

        # No pin: the intent still *describes* the command, so a policy decision can
        # still refuse it by name, but nothing may launch it (see the end-to-end test).
        self.assertEqual(intent.resolved_executable, "")
        self.assertEqual(intent.executable, _UNPINNABLE)

    def test_03_an_explicitly_empty_path_is_not_replaced_by_the_process_path(self) -> None:
        """``PATH=""`` means "search nowhere": the process PATH must not be substituted."""

        request = _request("python", path_value="")
        intent = IntentBuilder.from_request(request, request.profile).build()
        self.assertEqual(
            intent.resolved_executable,
            "",
            "an empty declared PATH must not resolve through the process PATH",
        )

    def test_04_an_absolute_executable_needs_no_pin(self) -> None:
        absolute = str((_INTERPRETER_DIR / "python.exe").resolve())
        if not Path(absolute).exists():
            absolute = str(Path(sys.executable).resolve())
        request = _request(absolute, path_value=str(_EMPTY_SEARCH_DIR))
        intent = IntentBuilder.from_request(request, request.profile).build()
        # ``canonical_executable`` normalizes case the way the platform does.
        self.assertEqual(
            os.path.normcase(intent.resolved_executable), os.path.normcase(absolute)
        )


class UnpinnableCommandIsDeniedEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        # A tiny workspace of its own: the coordinator stages whatever root it is
        # given, so pointing it at the repository would copy the whole project.
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_pin_")
        self.workspace = Path(self._temp_dir.name).resolve()

    def tearDown(self) -> None:
        RunScope.release_all()
        self._temp_dir.cleanup()

    def test_05_the_coordinator_denies_it_and_starts_nothing(self) -> None:
        request = _request(_UNPINNABLE, path_value=str(_EMPTY_SEARCH_DIR))
        coordinator = ExecutionCoordinator(adapter=LocalExecutionAdapter())

        result = coordinator.execute(
            request,
            allowed_commands=frozenset({_UNPINNABLE}),
            workspace_root=self.workspace,
            run_id="pin-run-1",
            run_scope=_scope(
                "pin-run-1",
                _UNPINNABLE,
                path_value=str(_EMPTY_SEARCH_DIR),
                workspace_root=self.workspace,
            ),
            approval_policy=ApprovalPolicy(),
        )

        self.assertEqual(result.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED)
        self.assertIsNone(result.exit_code)
        self.assertEqual(result.stdout, "")
        self.assertIn("pin", (result.failure_reason or "").lower())


if __name__ == "__main__":
    unittest.main()
