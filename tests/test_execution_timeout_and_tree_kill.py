"""Regression tests: the timeout must bound wall-clock time and kill the whole tree.

Two defects, both measured before the fix:

1. ``timeout_seconds`` did not bound wall-clock time. The adapter blocked in
   ``proc.communicate(timeout=...)``, which returns on pipe EOF, and a pipe stays open
   while *any* process holds the write end -- including a descendant that inherited it.
   A probe with ``timeout_seconds = 0.5`` returned after **6.25 s**.
2. ``_terminate_process_tree`` began with ``if proc.poll() is not None: return``, so when
   the direct child had already exited (the daemonizing case: child spawns a descendant
   and returns) the tree was never killed, and a ``TIMEOUT`` result could coexist with a
   live descendant.

These tests assert **consequences**, not status strings: elapsed wall-clock in seconds,
whether a descendant is still alive, and whether the scratch tree is gone. The existing
``test_timeout_kills_process_tree`` asserted only the status, so a surviving grandchild
passed it.

The scope is built with ``tests/run_scope_support.freeze_scope``, the helper the
execution-plane tests use, so the perimeter is declared exactly once and the command
identity matches the request.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from app.execution.adapter import LocalExecutionAdapter
from app.execution.authorizer import ExecutionCoordinator
from app.execution.capabilities import ExecutionCapability
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest, ExecutionStatus
from app.execution.workspace_manager import EphemeralWorkspaceManager
from app.tools.approval import ApprovalPolicy
from app.tools.workspace import Workspace
from run_scope_support import freeze_scope


def _process_is_alive(pid: int) -> bool:
    """True when ``pid`` still names a running process.

    ``os.kill(pid, 0)`` is the portable probe: it raises ``ProcessLookupError`` for a dead
    process and ``PermissionError`` for one owned by somebody else (which is alive). On
    Windows a dead process can surface as a bare ``OSError``, so both are inspected
    rather than assumed.
    """

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _kill_quietly(pid: int) -> None:
    """Best-effort cleanup so a failed assertion cannot leave a sleeper behind."""

    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def _profile_for(command: tuple[str, ...]) -> ProjectExecutionProfile:
    """A profile that authorizes exactly ``command``.

    ``INTERPRET_TEXT`` is declared because ``python -c`` carries an interpretive payload:
    the classifier derives that capability and the policy refuses an invocation whose
    profile does not grant every capability the intent carries. Declaring it is the
    documented way to authorize the invocation, not a weakening of the policy.
    """

    return ProjectExecutionProfile(
        profile_id="timeout-probe",
        working_directory=".",
        allowed_commands=command,
        capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
    )


def _execute(
    workspace: Path,
    scratch_base: Path,
    command: tuple[str, ...],
    *,
    timeout: float,
    run_id: str,
) -> tuple[object, float]:
    """Run ``command`` through the coordinator; return ``(result, elapsed_seconds)``.

    The coordinator, not the adapter, is the entry point: ``adapter.execute`` takes a
    coordinator-minted ``AuthorizedExecution``, and the coordinator is also the path the
    audit measured.
    """

    profile = _profile_for(command)
    scope = freeze_scope(
        run_id,
        commands=command,
        workspace=Workspace(workspace),
        profile=profile,
    )
    adapter = LocalExecutionAdapter(
        workspace_manager=EphemeralWorkspaceManager(base_scratch_dir=scratch_base)
    )
    request = ExecutionRequest(
        command=command,
        working_directory=".",
        timeout_seconds=timeout,
        profile=profile,
    )
    started = time.monotonic()
    result = ExecutionCoordinator(adapter).execute(
        request,
        workspace_root=workspace,
        run_id=run_id,
        allowed_commands=command,
        # A POLICY IS STATED EXPLICITLY. `ExecutionCoordinator` denies a dispatch with
        # no `ApprovalPolicy` (the Block #8 D6 fix), so a fixture that omitted one never
        # reached the process these tests measure -- measured: all seven of them failed
        # with `approval_policy_required` instead of the timeout/cleanup outcome they
        # assert. `ApprovalPolicy()` is the default and requires approval for no tool, so
        # it grants nothing and leaves each test measuring exactly its own subject.
        approval_policy=ApprovalPolicy(),
        run_scope=scope,
    )
    return result, time.monotonic() - started


class WallClockTimeoutTests(unittest.TestCase):
    """The deadline bounds the adapter's own wall-clock, descendants included."""

    _counter = 0

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.scratch_base = self.base / "scratch"
        self._spawned: list[int] = []

    def tearDown(self):
        for pid in self._spawned:
            _kill_quietly(pid)
        self.temp_dir.cleanup()

    def _run(self, code: str, *, timeout: float) -> tuple[object, float]:
        WallClockTimeoutTests._counter += 1
        command = (sys.executable, "-c", code)
        return _execute(
            self.workspace,
            self.scratch_base,
            command,
            timeout=timeout,
            run_id=f"timeout-run-{WallClockTimeoutTests._counter}",
        )

    def _write_script(self, name: str, source: str) -> str:
        """Stage a small program in the workspace and return its relative name.

        A script file rather than a long ``-c`` payload: the profile validates every
        element of an allowed command, and a multi-line program is not a valid command
        identity. The script shape is also what a real run executes.
        """

        (self.workspace / name).write_text(source, encoding="utf-8")
        return name

    def _run_script(self, script: str, *, timeout: float) -> tuple[object, float]:
        WallClockTimeoutTests._counter += 1
        command = ("python", script)
        return _execute(
            self.workspace,
            self.scratch_base,
            command,
            timeout=timeout,
            run_id=f"timeout-run-{WallClockTimeoutTests._counter}",
        )

    def _await_death(self, pid: int, seconds: float = 10.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and _process_is_alive(pid):
            time.sleep(0.25)
        return not _process_is_alive(pid)

    def test_01_a_prompt_child_finishes_normally(self):
        """The fix must not break the ordinary path."""

        result, elapsed = self._run("print('done')", timeout=30)
        self.assertEqual(result.status, ExecutionStatus.SUCCESS, result.stderr)
        self.assertIn("done", result.stdout)
        self.assertLess(elapsed, 10)

    def test_02_a_sleeper_is_timed_out_and_bounded(self):
        """A plain long process: the deadline is the configured timeout."""

        result, elapsed = self._run("import time; time.sleep(60)", timeout=2)
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT, result.stderr)
        self.assertLess(elapsed, 8.0, f"timeout=2s but wall-clock was {elapsed:.2f}s")

    def test_03_a_descendant_holding_the_pipe_does_not_extend_the_wall_clock(self):
        """The measured defect: a grandchild inherits the pipes and outlives the timeout.

        The child spawns a grandchild that sleeps far longer than the timeout, records its
        pid, then exits. The grandchild holds stdout open, which is what used to keep
        ``communicate`` blocked past the deadline.
        """

        pid_file = self.base / "grandchild.pid"
        script = self._write_script(
            "spawn_holder.py",
            "import subprocess, sys, pathlib, time\n"
            f"out = pathlib.Path(r'{pid_file}')\n"
            "p = subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(90)'],\n"
            "    stdout=sys.stdout, stderr=sys.stderr,\n"
            ")\n"
            "out.write_text(str(p.pid), encoding='utf-8')\n"
            "print('spawned', p.pid, flush=True)\n"
            # The direct child must outlive the deadline, otherwise the run ends normally
            # and the timeout path is never entered.
            "time.sleep(60)\n",
        )
        result, elapsed = self._run_script(script, timeout=2)

        self.assertEqual(result.status, ExecutionStatus.TIMEOUT, result.stderr)
        self.assertLess(
            elapsed,
            25.0,
            f"timeout=2s but wall-clock was {elapsed:.2f}s -- a held-open pipe still "
            "blocks the adapter",
        )

        self.assertTrue(pid_file.exists(), "the child never recorded its descendant")
        pid = int(pid_file.read_text(encoding="utf-8").strip() or 0)
        self.assertNotEqual(pid, 0)
        self._spawned.append(pid)
        self.assertTrue(
            self._await_death(pid),
            f"descendant {pid} survived the timeout kill",
        )

    def test_04_a_daemonizing_child_still_gets_its_tree_killed(self):
        """The early-return defect: the direct child exits, so the tree was skipped.

        The child starts a detached sleeper, records its pid, and exits before the timeout
        fires. The old code saw ``proc.poll() is not None`` and returned without killing
        anything.
        """

        pid_file = self.base / "detached.pid"
        script = self._write_script(
            "spawn_detached.py",
            "import subprocess, sys, pathlib\n"
            f"out = pathlib.Path(r'{pid_file}')\n"
            "flags = (0x00000008 | 0x00000200) if sys.platform == 'win32' else 0\n"
            "p = subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(90)'],\n"
            "    stdout=sys.stdout, stderr=sys.stderr, creationflags=flags,\n"
            "    start_new_session=(sys.platform != 'win32'),\n"
            ")\n"
            "out.write_text(str(p.pid), encoding='utf-8')\n",
        )
        result, _ = self._run_script(script, timeout=3)

        # The direct child exits at once, so the run itself succeeds. What must hold is
        # that the detached descendant does not survive it: the old early return left the
        # descendant running after the adapter had already reported its outcome.
        self.assertEqual(result.status, ExecutionStatus.SUCCESS, result.stderr)
        self.assertTrue(pid_file.exists(), "the child never recorded its descendant")
        pid = int(pid_file.read_text(encoding="utf-8").strip() or 0)
        self.assertNotEqual(pid, 0)
        self._spawned.append(pid)
        self.assertTrue(
            self._await_death(pid),
            f"descendant {pid} survived: the tree kill was skipped because the direct "
            "child had already exited",
        )


class ScratchCleanupTests(unittest.TestCase):
    """The scratch tree must not survive the run, whatever the outcome."""

    _counter = 0

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base = Path(self.temp_dir.name)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        (self.workspace / "keep.py").write_text("k = 1\n", encoding="utf-8")
        self.scratch_base = self.base / "scratch"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _leftovers(self) -> list[Path]:
        if not self.scratch_base.exists():
            return []
        return [
            path
            for path in self.scratch_base.iterdir()
            if path.name.startswith("forge_scratch_")
        ]

    def _run(self, code: str, *, timeout: float = 30):
        ScratchCleanupTests._counter += 1
        command = (sys.executable, "-c", code)
        result, _ = _execute(
            self.workspace,
            self.scratch_base,
            command,
            timeout=timeout,
            run_id=f"scratch-run-{ScratchCleanupTests._counter}",
        )
        return result

    def test_20_scratch_is_removed_after_success(self):
        result = self._run("print('ok')")
        self.assertEqual(result.status, ExecutionStatus.SUCCESS, result.stderr)
        self.assertEqual(self._leftovers(), [])

    def test_21_scratch_is_removed_after_a_failure(self):
        result = self._run("raise SystemExit(3)")
        self.assertEqual(result.status, ExecutionStatus.FAILURE, result.stderr)
        self.assertEqual(self._leftovers(), [])

    def test_22_scratch_is_removed_after_a_timeout(self):
        result = self._run("import time; time.sleep(60)", timeout=2)
        self.assertEqual(result.status, ExecutionStatus.TIMEOUT, result.stderr)
        self.assertEqual(self._leftovers(), [])

    def test_23_scratch_is_removed_after_a_refusal(self):
        """A command outside the frozen perimeter is refused before any staged tree."""

        ScratchCleanupTests._counter += 1
        run_id = f"scratch-run-{ScratchCleanupTests._counter}"
        bogus = ("definitely-not-a-real-executable-9f3a",)
        profile = ProjectExecutionProfile(
            profile_id="timeout-probe",
            working_directory=".",
            allowed_commands=bogus,
        )
        scope = freeze_scope(
            run_id,
            commands=bogus,
            workspace=Workspace(self.workspace),
            profile=profile,
        )
        adapter = LocalExecutionAdapter(
            workspace_manager=EphemeralWorkspaceManager(
                base_scratch_dir=self.scratch_base
            )
        )
        result = ExecutionCoordinator(adapter).execute(
            ExecutionRequest(
                command=bogus,
                working_directory=".",
                timeout_seconds=10,
                profile=profile,
            ),
            workspace_root=self.workspace,
            run_id=run_id,
            allowed_commands=bogus,
            # Stated for the same reason as `_run`: a missing policy is now an
            # unconditional denial, so without one this test could not reach the
            # adapter at all and would prove nothing about the staged tree.
            approval_policy=ApprovalPolicy(),
            run_scope=scope,
        )
        # The perimeter authorizes the name, so the coordinator admits it and the adapter
        # then fails to start a nonexistent executable. Either outcome is acceptable; the
        # assertion that matters is that neither leaves a staged tree.
        self.assertIn(
            result.status,
            (ExecutionStatus.ERROR, ExecutionStatus.DENIED),
            result.stderr,
        )
        self.assertEqual(self._leftovers(), [])


if __name__ == "__main__":
    unittest.main()
