"""Production tests for the execution filesystem boundary and network policy.

These tests exercise the real production plane: a real ``RunScope``, the real
``ExecutionCoordinator``, and the real ``LocalExecutionAdapter``. Where a case
cannot be honestly enforced by this architecture, the test asserts the *actual*
behaviour and records the residual risk instead of pretending the boundary is
stronger than it is.

Filesystem boundary: traversal, absolute paths outside the workspace, junctions
and symlinks that leave the workspace, an external working directory, and
caller-supplied metadata all resolve inside the frozen workspace or are refused.

Network: the policy is deny-by-default and authority-derived. The suite proves
the policy cannot be enabled by a caller, and documents - by testing it - that
the child is *not* kernel-isolated, so creating a socket still works.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from app.execution.adapter import (
    EXECUTION_RESIDUAL_RISK,
    ExecutionAuthorizationError,
    LocalExecutionAdapter,
    _RootContract,
)
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.intent import IntentBuilder
from app.execution.paths import PathSecurityError
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionRequest,
    ExecutionStatus,
)
from app.execution.sandbox import (
    RESIDUAL_RISK,
    WorkspaceBoundary,
    WorkspaceContainmentError,
)
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.acceptance import AcceptanceCriterion
from app.tools.workspace import Workspace

RUN_ID = "run-sandbox-1"
CRITERION = AcceptanceCriterion(criterion_id="c1", description="d")


def profile(**overrides) -> ProjectExecutionProfile:
    payload = {
        "profile_id": "p",
        "allowed_commands": (sys.executable, "python", "python.exe"),
        "capabilities": frozenset({ExecutionCapability.INTERPRET_TEXT}),
        "network_access": False,
        "working_directory": ".",
    }
    payload.update(overrides)
    return ProjectExecutionProfile(**payload)


class SandboxTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "workspace"
        self.root.mkdir()
        (self.root / "inside.txt").write_text("inside", encoding="utf-8")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
        self.outside = self.base / "outside.txt"
        self.outside.write_text("outside", encoding="utf-8")

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def boundary(self) -> WorkspaceBoundary:
        return WorkspaceBoundary.for_workspace(Workspace(self.root))

    def scope(self, *, network_access: bool = False, run_id: str = RUN_ID) -> RunScope:
        scope = RunScope(
            run_id=run_id,
            workspace=Workspace(self.root),
            execution_profile=profile(network_access=network_access),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=(sys.executable,),
            acceptance_criteria=(CRITERION,),
        )
        scope.freeze()
        return scope

    def request(self, working_directory: str, **overrides) -> ExecutionRequest:
        payload = {
            "command": (sys.executable, "-c", "print('ok')"),
            "working_directory": working_directory,
            "profile": profile(),
        }
        payload.update(overrides)
        return ExecutionRequest(**payload)

    def adapter(self) -> LocalExecutionAdapter:
        return LocalExecutionAdapter(
            workspace_root=self.root, isolate_workspace=False
        )

    def run_through_coordinator(
        self, request: ExecutionRequest, *, run_id: str = RUN_ID, **kw
    ):
        """Drive the real authorization chain and the real adapter.

        The frozen scope is passed explicitly, mirroring production where the
        orchestration layer supplies the perimeter before any dispatch.
        """
        scope = self.scope(run_id=run_id)
        return ExecutionCoordinator(self.adapter()).execute(
            request,
            workspace_root=self.root,
            run_id=run_id,
            # The declared command set is the scope's own derived set, so the
            # coordinator re-checks exactly the authority the scope froze.
            allowed_commands=frozenset(scope.allowed_execution_commands),
            run_scope=scope,
            **kw,
        )


# --------------------------------------------------------------------------- #
# FILESYSTEM: what is allowed
# --------------------------------------------------------------------------- #


class FilesystemAllowTests(SandboxTestCase):
    def test_workspace_root_is_allowed(self) -> None:
        self.assertTrue(self.boundary().contains(self.root))

    def test_regular_file_inside_workspace_is_allowed(self) -> None:
        resolved = self.boundary().resolve_relative("inside.txt")
        self.assertEqual(resolved.name, "inside.txt")
        self.assertTrue(resolved.exists())

    def test_nested_file_inside_workspace_is_allowed(self) -> None:
        resolved = self.boundary().resolve_relative("sub/nested.txt")
        self.assertTrue(resolved.exists())
        self.assertEqual(resolved.parent.name, "sub")

    def test_subdirectory_and_dot_are_allowed(self) -> None:
        self.assertEqual(self.boundary().resolve_relative("sub").name, "sub")
        self.assertEqual(self.boundary().resolve_relative("."), self.root)

    def test_read_and_write_inside_workspace_are_allowed(self) -> None:
        resolved = self.boundary().resolve_relative("inside.txt")
        self.assertEqual(resolved.read_text(encoding="utf-8"), "inside")
        target = self.root / "written.txt"
        target.write_text("data", encoding="utf-8")
        self.assertTrue(self.boundary().contains(target))

    def test_scope_accepts_a_contained_working_directory(self) -> None:
        scope = self.scope()
        scope.validate_execution_request(self.request("."))
        scope.validate_execution_request(self.request("sub"))

    def test_coordinator_runs_a_contained_command(self) -> None:
        result = self.run_through_coordinator(self.request("."))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "ok")


# --------------------------------------------------------------------------- #
# FILESYSTEM: what is refused
# --------------------------------------------------------------------------- #


class FilesystemEscapeTests(SandboxTestCase):
    def _refused(self, value: str) -> None:
        with self.assertRaises(WorkspaceContainmentError):
            self.boundary().resolve_relative(value)

    def test_parent_traversal_is_refused(self) -> None:
        self._refused("../outside.txt")
        self._refused("..")

    def test_deep_traversal_is_refused(self) -> None:
        self._refused("sub/../../outside.txt")

    def test_inner_traversal_is_refused(self) -> None:
        # A traversal that ends inside the workspace is still a traversal, so the
        # boundary never depends on a lexical comparison alone.
        self._refused("sub/../inside.txt")

    def test_absolute_paths_outside_are_refused(self) -> None:
        self._refused(str(self.outside))
        self._refused(str(self.base))

    def test_windows_and_unc_paths_are_refused(self) -> None:
        self._refused(r"C:\Windows\win.ini")
        self._refused(r"\\server\share\file.txt")

    def test_empty_and_null_are_refused(self) -> None:
        self._refused("")
        self._refused("   ")
        self._refused("a\x00b")

    def test_scope_refuses_traversal_working_directory(self) -> None:
        with self.assertRaises(RunScopeError) as ctx:
            self.scope().validate_execution_request(self.request("../outside"))
        self.assertIn("workspace boundary", str(ctx.exception))

    def test_scope_refuses_traversal_after_normalization(self) -> None:
        with self.assertRaises(RunScopeError):
            self.scope().validate_execution_request(
                self.request("sub/deeper/../../../outside")
            )

    def test_scope_refuses_absolute_working_directory(self) -> None:
        with self.assertRaises(RunScopeError):
            self.scope().validate_execution_request(self.request(str(self.outside)))

    def test_scope_refuses_escaping_artifact_target(self) -> None:
        with self.assertRaises(RunScopeError):
            self.scope().validate_execution_request(
                self.request(".", artifact_targets=("../escape.txt",))
            )

    def test_traversal_is_refused_with_a_containment_reason(self) -> None:
        result = self.run_through_coordinator(self.request("../outside"))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("workspace boundary", result.stderr)
        self.assertIn("traversal", result.stderr)

    def test_absolute_path_outside_is_refused(self) -> None:
        result = self.run_through_coordinator(self.request(str(self.outside)))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("workspace boundary", result.stderr)

    def test_escaping_working_directory_is_terminal_permission_denial(self) -> None:
        result = self.run_through_coordinator(self.request("../outside"))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn(
            result.outcome_status,
            (ExecutionOutcomeStatus.PERMISSION_DENIED,
             ExecutionOutcomeStatus.POLICY_DENIED),
        )
        self.assertFalse(result.metadata.get("executed", False))

    def test_refusal_happens_before_the_child_exists(self) -> None:
        """The authority boundary refuses the escape, not the running process.

        The coordinator's recording backend proves no token was ever handed to an
        execution backend, so an escaping request cannot reach a child process.
        """
        captured: dict[str, object] = {}

        class _Capture:
            def execute(self, token):
                captured["token"] = token
                return None

        scope = self.scope()
        result = ExecutionCoordinator(_Capture()).execute(
            self.request("../outside"),
            workspace_root=self.root,
            run_id=RUN_ID,
            allowed_commands=frozenset(scope.allowed_execution_commands),
            run_scope=scope,
        )
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertNotIn("token", captured)


# --------------------------------------------------------------------------- #
# FILESYSTEM: link-based escape
# --------------------------------------------------------------------------- #


def make_directory_link(link: Path, target: Path) -> bool:
    """Create a directory link, returning False when the platform refuses."""
    try:
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
            )
            return completed.returncode == 0 and link.exists()
        os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, NotImplementedError):
        return False


class LinkEscapeTests(SandboxTestCase):
    def test_link_leaving_the_workspace_is_refused(self) -> None:
        link = self.root / "link_out"
        if not make_directory_link(link, self.base):
            self.skipTest("this platform could not create a directory link")
        with self.assertRaises(WorkspaceContainmentError):
            self.boundary().resolve_relative("link_out/outside.txt")
        with self.assertRaises(WorkspaceContainmentError):
            self.boundary().resolve_relative("link_out")

    def test_scope_refuses_a_working_directory_through_an_escaping_link(self) -> None:
        link = self.root / "link_out"
        if not make_directory_link(link, self.base):
            self.skipTest("this platform could not create a directory link")
        with self.assertRaises(RunScopeError):
            self.scope().validate_execution_request(self.request("link_out"))

    def test_link_staying_inside_the_workspace_is_allowed(self) -> None:
        link = self.root / "link_in"
        if not make_directory_link(link, self.root / "sub"):
            self.skipTest("this platform could not create a directory link")
        resolved = self.boundary().resolve_relative("link_in/nested.txt")
        self.assertTrue(resolved.exists())


# --------------------------------------------------------------------------- #
# FILESYSTEM: subprocess and metadata
# --------------------------------------------------------------------------- #


class SubprocessBoundaryTests(SandboxTestCase):
    def test_child_process_runs_inside_the_workspace(self) -> None:
        result = self.run_through_coordinator(
            ExecutionRequest(
                command=(
                    sys.executable,
                    "-c",
                    "import pathlib; print(pathlib.Path.cwd())",
                ),
                working_directory=".",
                profile=profile(),
            )
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertIn("workspace", result.stdout)

    def test_child_process_can_still_read_outside_the_workspace(self) -> None:
        """The honest residual risk, asserted rather than merely described.

        The workspace boundary constrains the paths the execution plane itself
        resolves. It does not confine the child's syscalls, so a process that
        opens an absolute path outside the workspace can still do so. This test
        makes the limitation executable: if a real sandbox is ever added, this
        test must be replaced by one that proves the denial.
        """
        result = self.run_through_coordinator(
            ExecutionRequest(
                command=(
                    sys.executable,
                    "-c",
                    "import pathlib, sys; print(pathlib.Path(sys.argv[1]).exists())",
                    str(self.outside),
                ),
                working_directory=".",
                profile=profile(),
            )
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "True")
        self.assertFalse(self.boundary().contains(self.outside))

    def test_residual_risk_is_recorded_in_code(self) -> None:
        self.assertIn("No OS-level filesystem sandbox", RESIDUAL_RISK)
        self.assertEqual(EXECUTION_RESIDUAL_RISK, RESIDUAL_RISK)
        described = self.boundary().describe()
        self.assertFalse(described["os_level_sandbox"])
        self.assertIn("residual_risk", described)

    def test_metadata_cannot_widen_the_workspace(self) -> None:
        request = ExecutionRequest(
            command=(sys.executable, "-c", "print('ok')"),
            working_directory=".",
            profile=profile(),
            metadata={
                "workspace_root": str(self.base),
                "workspace": str(self.base),
                "cwd": str(self.base),
            },
        )
        # Metadata is data, so the same request still runs inside the workspace...
        result = self.run_through_coordinator(request)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        # ...and an escape is still refused.
        with self.assertRaises(RunScopeError):
            self.scope().validate_execution_request(self.request(str(self.base)))

    def test_root_contract_uses_the_token_root(self) -> None:
        self.assertEqual(
            WorkspaceBoundary.for_workspace(_RootContract(self.root)).root,
            WorkspaceBoundary.for_workspace(Workspace(self.root)).root,
        )


# --------------------------------------------------------------------------- #
# F-25-01: the adapter-level live check against the trusted root
# --------------------------------------------------------------------------- #


class LiveCwdCheckTests(SandboxTestCase):
    """The live check must be able to refuse, unlike a self-comparison.

    Each test drives the real ``LocalExecutionAdapter.execute`` path. The
    working directory handed to ``_run_process`` is chosen so that the authority
    check inside ``_resolve_working_directory`` has already passed and only the
    adapter's last-moment check can still refuse - exactly the shape the block 25
    review asked to be proved.
    """

    def _adapter_with_stale_cwd(self, stale_cwd: Path) -> LocalExecutionAdapter:
        adapter = LocalExecutionAdapter(
            workspace_root=self.root, isolate_workspace=False
        )
        real = adapter._resolve_working_directory

        def _stale(working_directory, root, request_id, metadata):
            # Run the genuine authority-side resolution first: it must pass, so the
            # refusal under test can only come from the live check.
            resolved, error = real(working_directory, root, request_id, metadata)
            self.assertIsNone(error, "the authority-side check should have allowed this")
            return stale_cwd, None

        adapter._resolve_working_directory = _stale
        return adapter

    def _execute_through_coordinator(
        self, adapter: LocalExecutionAdapter, request: ExecutionRequest
    ):
        """Drive the real coordinator, then the real adapter under test.

        The coordinator performs the authority validation and produces the
        ``AuthorizedExecution``; the backend delegates to ``adapter`` so the result
        under test is the adapter's own.
        """
        scope = self.scope()

        class _Delegating:
            def execute(self, token):
                return adapter.execute(token)

        return ExecutionCoordinator(_Delegating()).execute(
            request,
            workspace_root=self.root,
            run_id=RUN_ID,
            allowed_commands=frozenset(scope.allowed_execution_commands),
            run_scope=scope,
        )

    def test_live_check_refuses_a_cwd_that_became_external(self) -> None:
        """A cwd that is outside the trusted root after validation is refused."""
        adapter = self._adapter_with_stale_cwd(self.base)
        result = self._execute_through_coordinator(adapter, self.request("."))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("trusted workspace root", result.stderr)
        self.assertEqual(result.exit_code, None)
        self.assertEqual(result.stdout, "")

    def test_live_check_refuses_a_cwd_swapped_for_an_escaping_junction(self) -> None:
        """A cwd that became a junction pointing outside is refused.

        The link's *name* is inside the workspace and the authority-side check
        examines a real internal directory first; what refuses is the live check
        re-canonicalizing the directory and finding that it now resolves outside
        the trusted root.
        """
        hook = self.root / "swap"
        hook.mkdir()
        external = self.base / "swapped_outside"
        external.mkdir()
        # Replace the internal directory with a junction to the outside directory.
        hook.rmdir()
        if not make_directory_link(hook, external):
            self.skipTest("this platform could not create a directory link")
        self.assertFalse(
            WorkspaceBoundary.for_workspace(Workspace(self.root)).contains(hook)
        )
        adapter = LocalExecutionAdapter(
            workspace_root=self.root, isolate_workspace=False
        )
        real = adapter._resolve_working_directory

        def _stale(working_directory, root, request_id, metadata):
            resolved, error = real(".", root, request_id, metadata)
            self.assertIsNone(error)
            return hook, None

        adapter._resolve_working_directory = _stale
        result = self._execute_through_coordinator(adapter, self.request("."))
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("trusted workspace root", result.stderr)

    def test_live_check_still_allows_a_contained_cwd(self) -> None:
        """The same adapter path still runs a legitimately contained command."""
        adapter = self._adapter_with_stale_cwd(self.root / "sub")
        result = self._execute_through_coordinator(adapter, self.request("."))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "ok")

    def test_boundary_is_derived_from_the_root_not_from_the_cwd(self) -> None:
        """The check compares the live cwd with the trusted root, not with itself.

        The regression this pins: the previous implementation built the boundary
        from ``cwd_path`` and asked it whether ``cwd_path`` was inside itself,
        which is always true. Building a boundary from an external path must not
        report that path as contained by the workspace.
        """
        external_boundary = WorkspaceBoundary(self.base)
        self.assertEqual(external_boundary.root, self.base)
        trusted = WorkspaceBoundary.for_workspace(Workspace(self.root))
        self.assertFalse(trusted.contains(external_boundary.root))
        self.assertTrue(trusted.contains(trusted.root))
        # And the self-comparison the old code performed is trivially true, which
        # is precisely why it could never refuse.
        self.assertTrue(external_boundary.contains(external_boundary.root))

    def test_run_process_refuses_without_a_boundary(self) -> None:
        """No trusted boundary means no spawn, fail closed."""
        adapter = LocalExecutionAdapter(
            workspace_root=self.root, isolate_workspace=False
        )
        result = adapter._run_process(
            self._intent_for_spawn(),
            self.root,
            {},
            "req-1",
            {},
            boundary=None,  # type: ignore[arg-type]
        )
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn("no trusted workspace boundary", result.stderr)

    def test_isolated_branch_also_binds_the_live_check(self) -> None:
        """With workspace isolation on, the boundary is the scratch execution root.

        The isolation copy is a directory *inside* the temporary tree, so it is
        not contained by the source workspace. The boundary must therefore be
        derived from the root the child actually runs in, or every isolated
        execution would be wrongly refused.
        """
        adapter = LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=True)
        result = self._execute_through_coordinator(adapter, self.request("."))
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertEqual(result.stdout.strip(), "ok")

    def _intent_for_spawn(self):
        builder = IntentBuilder()
        builder.with_command((sys.executable, "-c", "print('ok')"))
        builder.with_profile_id("p")
        builder.with_working_directory(".")
        builder.with_network_access(False)
        return builder.build()


# --------------------------------------------------------------------------- #
# NETWORK policy
# --------------------------------------------------------------------------- #


class NetworkPolicyTests(SandboxTestCase):
    def test_default_profile_denies_network(self) -> None:
        self.assertFalse(profile().network_access)

    def test_scope_freezes_deny_by_default(self) -> None:
        self.assertFalse(self.scope().execution_profile.network_access)

    def test_request_profile_cannot_enable_network(self) -> None:
        with self.assertRaises(RunScopeError) as ctx:
            self.scope().validate_execution_request(
                self.request(".", profile=profile(network_access=True))
            )
        self.assertIn("cannot enable network access", str(ctx.exception))

    def test_coordinator_denies_a_request_that_enables_network(self) -> None:
        result = self.run_through_coordinator(
            self.request(".", profile=profile(network_access=True))
        )
        self.assertEqual(result.status, ExecutionStatus.DENIED)
        self.assertIn(
            result.outcome_status,
            (ExecutionOutcomeStatus.POLICY_DENIED,
             ExecutionOutcomeStatus.PERMISSION_DENIED),
        )

    def test_operator_frozen_network_choice_is_respected(self) -> None:
        scope = self.scope(network_access=True, run_id="run-net")
        self.assertTrue(scope.execution_profile.network_access)
        scope.validate_execution_request(
            self.request(".", profile=profile(network_access=True))
        )

    def _intent(self, *, network_access: bool):
        builder = IntentBuilder()
        builder.with_command((sys.executable, "-c", "print('ok')"))
        builder.with_profile_id("p")
        builder.with_working_directory(".")
        builder.with_network_access(network_access)
        return builder.build()

    def test_adapter_sets_proxy_denial_when_network_is_denied(self) -> None:
        env = self.adapter()._build_scoped_environment(
            self._intent(network_access=False)
        )
        for key in ("http_proxy", "https_proxy", "all_proxy",
                    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            self.assertEqual(env[key], "http://127.0.0.1:0", key)
        self.assertEqual(env["NO_PROXY"], "")

    def test_adapter_does_not_set_proxy_denial_when_network_is_allowed(self) -> None:
        env = self.adapter()._build_scoped_environment(
            self._intent(network_access=True)
        )
        for key in ("http_proxy", "https_proxy", "all_proxy"):
            self.assertNotEqual(env.get(key), "http://127.0.0.1:0", key)

    def test_proxy_denial_is_not_a_kernel_sandbox(self) -> None:
        """The honest limitation: creating a socket still works.

        The denial is a policy boundary. It redirects the proxy variables that
        well-behaved HTTP clients honour and does nothing to a process that opens
        a socket itself. This asserts the real behaviour so the documentation
        cannot drift into claiming a kernel-enforced network sandbox.
        """
        result = self.run_through_coordinator(
            ExecutionRequest(
                command=(
                    sys.executable,
                    "-c",
                    "import socket; s=socket.socket(); print('socket_created')",
                ),
                working_directory=".",
                profile=profile(),
            )
        )
        self.assertEqual(result.status, ExecutionStatus.SUCCESS)
        self.assertIn("socket_created", result.stdout)

    def test_network_denial_is_never_described_as_a_sandbox(self) -> None:
        source = inspect.getsource(
            LocalExecutionAdapter._build_scoped_environment
        ).lower()
        self.assertIn("policy boundary", source)
        self.assertNotIn("network disabled", source)
        # The only legitimate mention is the one that denies being a sandbox.
        self.assertIn("not a kernel sandbox", source)


# --------------------------------------------------------------------------- #
# SECURITY invariants
# --------------------------------------------------------------------------- #


class SandboxSecurityTests(SandboxTestCase):
    def test_adapter_still_requires_authorized_execution(self) -> None:
        with self.assertRaises(ExecutionAuthorizationError):
            self.adapter().execute(object())

    def test_authority_ceiling_is_unchanged(self) -> None:
        """The boundary adds no capability to the profile."""
        scope = self.scope()
        frozen = scope.execution_profile
        self.assertFalse(frozen.network_access)
        self.assertIn(sys.executable, frozen.allowed_commands)
        self.assertIn(ExecutionCapability.INTERPRET_TEXT, frozen.capabilities)
        boundary = scope.filesystem_boundary()
        self.assertEqual(boundary.root, Workspace(self.root).root)
        self.assertFalse(boundary.describe()["os_level_sandbox"])

    def test_boundary_does_not_execute_anything(self) -> None:
        repo = Path(__file__).resolve().parents[1]
        source = (repo / "app/execution/sandbox.py").read_text(encoding="utf-8")
        for banned in ("subprocess", "Popen", "os.system", "shell=True",
                       "AuthorizedExecution", "ExecutionCoordinator",
                       "LocalExecutionAdapter", "eval(", "exec("):
            self.assertNotIn(banned, source, banned)

    def test_boundary_refuses_a_contract_without_a_root(self) -> None:
        for bad in (object(), None, "root", 7):
            with self.assertRaises(WorkspaceContainmentError):
                WorkspaceBoundary.for_workspace(bad)

    def test_containment_error_is_a_path_security_error(self) -> None:
        self.assertTrue(issubclass(WorkspaceContainmentError, PathSecurityError))

    def test_workspace_root_comes_from_the_scope_not_the_request(self) -> None:
        """A request cannot restate the perimeter it must stay inside."""
        scope = self.scope()
        scope.validate_execution_request(self.request("."))
        self.assertEqual(scope.filesystem_boundary().root, Workspace(self.root).root)
        with self.assertRaises(RunScopeError):
            scope.validate_workspace(Workspace(self.base))


if __name__ == "__main__":
    unittest.main()
