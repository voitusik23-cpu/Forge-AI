"""Regression tests for safe instance ownership in ``tools/forge_launch.py``.

Closing the last window must stop only the servers this launcher instance can *prove* it owns,
including servers orphaned by a dead launcher of the same workspace -- and it must never adopt a
server while another launcher of that workspace is alive.

A launcher that finds a server it cannot prove it owns still records what it observed, so the loop
closes: the next launcher of the workspace can adopt that server once its launcher is gone, and the
ports are released when the window closes. An observation never grants a stop by itself.

Nothing here starts a Forge server, terminates a process or touches a real record: every path lives
under a temporary directory and every process fact comes from a fake table. The record, the lock, the
identity proof and the ownership decisions are exercised directly, because those are the parts that
decide whether a process may be stopped.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("forge_launch", ROOT / "tools" / "forge_launch.py")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules["forge_launch"] = launcher
SPEC.loader.exec_module(launcher)

SELF_CREATED = "2026-10-04T09:00:00.0000000+03:00"
API_CREATED = "2026-10-04T09:00:05.0000000+03:00"
UI_CREATED = "2026-10-04T09:00:07.0000000+03:00"
OLDER_CREATED = "2026-10-03T08:00:00.0000000+03:00"
RECYCLED_CREATED = "2026-10-05T23:59:59.0000000+03:00"
OTHER_LAUNCHER_CREATED = "2026-10-04T10:00:00.0000000+03:00"
#: The browser behind the registered window, and one that has already exited (absent from the fake table).
BROWSER_PID = 5050
BROWSER_CREATED = "2026-10-04T09:30:00.0000000+03:00"
DEAD_BROWSER_PID = 5151


class FakeJournal:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str) -> None:
        self.lines.append(str(message))

    def text(self) -> str:
        return "\n".join(self.lines)


class FakeProcessTable:
    """A process table keyed by pid: the only source of identity the code under test may consult."""

    def __init__(self, processes=None) -> None:
        self.processes = {int(pid): created for pid, created in (processes or {}).items()}

    def created_at(self, pid, refresh: bool = False):
        return self.processes.get(int(pid))

    def exists(self, pid) -> bool:
        try:
            return int(pid) in self.processes
        except (TypeError, ValueError):
            return False


class FakeKernel32:
    """A ``kernel32`` substitute: exactly the Win32 calls the pinned stop path makes.

    The stop path is asserted at the boundary the operating system is asked through -- ``OpenProcess``,
    ``GetProcessTimes``, ``TerminateProcess``, ``WaitForSingleObject`` and ``CloseHandle`` -- because that
    is the boundary the recycled-pid defect lived on.
    """

    def __init__(self, table) -> None:  # noqa: ANN001 - the fake process table
        self.table = table
        self.handles = {}  # handle -> pid
        self.next_handle = 700
        self.terminated = []
        self.closed = []
        self.error = 0
        self.refuse = set()  # pids OpenProcess must refuse (access denied)
        self.exited = set()  # pids whose handle already reports exit
        self.start_ticks = {}  # pid -> creation ticks the handle reports
        self.on_terminate = None

    def OpenProcess(self, access, inherit, pid):  # noqa: N802 - the Win32 name is the point
        self.requested_access = access
        if pid in self.refuse:
            self.error = launcher.ERROR_ACCESS_DENIED
            return 0
        if pid not in self.table.processes:
            self.error = launcher.ERROR_INVALID_PARAMETER
            return 0
        self.next_handle += 1
        self.handles[self.next_handle] = pid
        return self.next_handle

    def GetProcessTimes(self, handle, created, exited, kernel, user):  # noqa: N802
        value = self.start_ticks.get(self.handles.get(handle))
        if value is None:
            return 0
        created._obj.value = value
        return 1

    def WaitForSingleObject(self, handle, timeout):  # noqa: N802
        pid = self.handles.get(handle)
        return launcher.WAIT_OBJECT_0 if pid in self.exited else 0x00000102

    def TerminateProcess(self, handle, code):  # noqa: N802
        pid = self.handles.get(handle)
        self.terminated.append(pid)
        self.table.processes.pop(pid, None)
        self.exited.add(pid)
        if self.on_terminate is not None:
            self.on_terminate(pid)
        return 1

    def CloseHandle(self, handle):  # noqa: N802
        self.closed.append(handle)
        self.handles.pop(handle, None)
        return 1


class ScriptedChecks:
    """What each successive check of the window wait loop answers.

    :meth:`launcher.Supervisor.window_check` is asked once per scan, so a script of
    ``(states, browsers)`` pairs describes exactly what the loop sees: a minimised window, a handle the
    browser replaced, a destroyed window whose browser still runs, or a browser that has exited. Asking
    for more checks than the script describes fails the test instead of looping forever.
    """

    def __init__(self, scans) -> None:
        self.scans = list(scans)
        self.calls = 0

    def __call__(self):
        if self.calls >= len(self.scans):
            raise AssertionError("the wait loop asked for more checks than the script describes")
        states, browsers = self.scans[self.calls]
        self.calls += 1
        return [tuple(item) for item in states], [tuple(item) for item in browsers]

    @property
    def scans_seen(self) -> int:
        return self.calls


def own_command_line(pid):  # noqa: ANN001, ARG001 - the proof only needs the value
    """A command line that names this repository's interpreter, as a real Forge server's does."""

    return f'"{launcher.REPO}\\.venv\\Scripts\\pythonw.exe" -B -X utf8 -m app.api.main'


class InstanceOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.journal = FakeJournal()
        self.table = FakeProcessTable({os.getpid(): SELF_CREATED})
        for attribute, replacement in (
            ("process_created_at", self.table.created_at),
            ("pid_exists", self.table.exists),
            # The last check before a stop: the servers of this workspace run from its own venv.
            ("process_command_line", own_command_line),
        ):
            patcher = mock.patch.object(launcher, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        # The launcher's own creation time is cached module-wide; each test starts from a clean read.
        launcher.launcher_created_at.__dict__.pop("value", None)
        self.addCleanup(launcher.launcher_created_at.__dict__.pop, "value", None)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.workspace = pathlib.Path(self.scratch.name)

    # ------------------------------------------------------------------ helpers
    def state(self, launcher_pid, launcher_created, api=None, ui=None, owned=()):
        state = launcher.new_state(self.workspace, launcher_pid, launcher_created)
        state["api"] = api
        state["ui"] = ui
        state["owned"] = list(owned)
        return state

    @staticmethod
    def observed_entry(pid, created, owner="reused"):
        """The entry a launcher leaves when it finds a server it cannot prove it owns."""

        return {"pid": pid, "created": created, "adopted": False, "owner": owner}

    def supervisor(self):
        return launcher.Supervisor(self.journal, open_window=False, url="http://127.0.0.1:8020/")

    def observed_record(self, role="api", pid=4141, created=API_CREATED, owner="reused"):
        """Write the observation through the real writer and read the record back."""

        with mock.patch.object(launcher, "REPO", self.workspace):
            self.assertTrue(
                self.supervisor().record_observation(
                    "core-api", role, pid, created, owner=owner
                ),
                "an observation must be written",
            )
        state_path, _ = launcher.instance_paths(self.workspace)
        return launcher.load_state(state_path)

    def pinned_stop(self, **options):
        """A fake ``kernel32`` plus the patches that route the stop path through it."""

        fake = FakeKernel32(self.table)
        for attribute, value in options.items():
            setattr(fake, attribute, value)
        for attribute, replacement in (
            ("kernel32_library", lambda: fake),
            ("last_error", lambda: fake.error),
        ):
            patcher = mock.patch.object(launcher, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        return fake

    # -------------------------------------------------------------------- tests
    def test_00_the_record_is_named_after_the_resolved_workspace(self) -> None:
        expected = hashlib.sha256(
            str(self.workspace.resolve()).lower().encode("utf-8")
        ).hexdigest()[:16]
        self.assertEqual(launcher.workspace_instance_id(self.workspace), expected)
        state_path, lock_path = launcher.instance_paths(self.workspace)
        self.assertEqual(state_path, self.workspace / ".forge" / f"instance-{expected}.json")
        self.assertEqual(lock_path, self.workspace / ".forge" / f"instance-{expected}.lock")

    def test_01_a_second_launch_cannot_own_while_the_record_is_locked(self) -> None:
        state_path, lock_path = launcher.instance_paths(self.workspace)
        launcher.save_state(
            state_path,
            self.state(1111, OLDER_CREATED, api={"pid": 4141, "created": API_CREATED}, owned=["api"]),
        )
        self.table.processes[4141] = API_CREATED
        supervisor = self.supervisor()
        held = launcher.acquire_instance_lock(lock_path, timeout=1.0)
        self.assertIsNotNone(held, "a fresh record must be lockable")
        try:
            with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
                launcher, "server_identity", return_value=(4141, API_CREATED)
            ):
                adopted = supervisor.adopt_owned("core-api", "api", launcher.API_PORT)
        finally:
            launcher.release_instance_lock(held)
        self.assertFalse(adopted, "a contended record must never be taken over")
        self.assertEqual(supervisor.adopted, [], "nothing may be stopped without the record")
        self.assertIn("contended", self.journal.text())
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["pid"], 1111, "the record must be left untouched")
        self.assertEqual(stored["api"], {"pid": 4141, "created": API_CREATED})

    def test_02_a_recycled_pid_is_not_the_recorded_process(self) -> None:
        self.table.processes[4141] = API_CREATED
        self.assertTrue(launcher.process_alive(4141, API_CREATED))
        with mock.patch.object(launcher, "process_created_at", return_value=RECYCLED_CREATED):
            self.assertFalse(
                launcher.process_alive(4141, API_CREATED),
                "a different creation time behind the same pid is a different process",
            )
        self.assertFalse(launcher.process_alive(4141, None), "an unknown creation time is never proof")
        self.assertFalse(launcher.process_alive(999999, API_CREATED), "a dead pid is not a process")

        state = self.state(
            1111, OLDER_CREATED, api={"pid": 4141, "created": API_CREATED}, owned=["api"]
        )
        decision = launcher.decide_ownership(
            state, "api", 4141, RECYCLED_CREATED, workspace=self.workspace
        )
        self.assertFalse(decision.adopt)
        self.assertIn("different creation time", decision.reason)

    def test_03_a_dead_launchers_orphan_is_adopted_and_recorded(self) -> None:
        state_path, lock_path = launcher.instance_paths(self.workspace)
        launcher.save_state(
            state_path,
            self.state(1111, OLDER_CREATED, api={"pid": 4141, "created": API_CREATED}, owned=["api"]),
        )
        self.table.processes[4141] = API_CREATED
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertTrue(
                supervisor.adopt_owned("core-api", "api", launcher.API_PORT),
                "an orphan of a dead launcher of this workspace must be adoptable",
            )
        self.assertEqual(supervisor.adopted, [("core-api", 4141, API_CREATED)])
        self.assertIn("api", supervisor.owned_roles)
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["pid"], os.getpid())
        self.assertIn("api", stored["owned"])
        self.assertEqual(stored["api"]["pid"], 4141)
        self.assertEqual(stored["api"]["created"], API_CREATED)
        self.assertIs(stored["api"]["adopted"], True, "an adoption is recorded as adopted")
        self.assertFalse(lock_path.exists(), "the lock must not outlive the transition")
        self.assertIn("adopting pid 4141", self.journal.text())

    def test_04_a_live_foreign_launcher_blocks_adoption(self) -> None:
        self.table.processes[1111] = OLDER_CREATED  # that launcher is still running
        state = self.state(
            1111, OLDER_CREATED, api={"pid": 4141, "created": API_CREATED}, owned=["api"]
        )
        self.table.processes[4141] = API_CREATED
        decision = launcher.decide_ownership(
            state, "api", 4141, API_CREATED, workspace=self.workspace
        )
        self.assertFalse(decision.adopt)
        self.assertIn("live launcher", decision.reason)

        # The same pid, but no longer provably that launcher: still no adoption.
        self.table.processes[1111] = "another process behind the same pid"
        decision = launcher.decide_ownership(
            state, "api", 4141, API_CREATED, workspace=self.workspace
        )
        self.assertFalse(decision.adopt)
        self.assertIn("proven gone", decision.reason)

        # And the whole path: a live launcher's server is reused and recorded as not ours.
        state_path, _ = launcher.instance_paths(self.workspace)
        launcher.save_state(state_path, state)
        self.table.processes[1111] = OLDER_CREATED
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertFalse(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
        self.assertEqual(supervisor.adopted, [])
        self.assertEqual(launcher.load_state(state_path)["launcher"]["pid"], 1111)

    def test_05_a_server_without_a_record_is_reused_recorded_and_not_owned(self) -> None:
        """The reported defect: reuse wrote nothing, so ownership was never adopted later."""

        state_path, _ = launcher.instance_paths(self.workspace)
        self.table.processes[4141] = API_CREATED
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertFalse(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
        self.assertEqual(supervisor.adopted, [], "an observation is not an adoption")
        self.assertIn("reused without ownership", self.journal.text())

        self.assertTrue(state_path.exists(), "the observation must be written to the record")
        stored = launcher.load_state(state_path)
        self.assertEqual(
            stored["api"],
            {"pid": 4141, "created": API_CREATED, "adopted": False, "owner": "reused"},
        )
        self.assertNotIn("api", stored["owned"], "a reused server is never claimed")
        self.assertIsNone(
            stored["launcher"],
            "an empty launcher slot is not ownership: the observing launcher does not claim it",
        )
        self.assertIn("not owned", self.journal.text())

    def test_05b_an_unidentifiable_listener_is_recorded_as_unknown(self) -> None:
        state_path, _ = launcher.instance_paths(self.workspace)
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=None
        ):
            self.assertFalse(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["api"]["owner"], "unknown")
        self.assertIs(stored["api"]["adopted"], False)
        self.assertIsNone(stored["api"]["pid"])
        self.assertIn("ownership unknown", self.journal.text())

    def test_06_stopping_skips_an_adopted_pid_whose_identity_fails(self) -> None:
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        # The pid was recycled after adoption, so the process now behind it is not ours.
        self.table.processes[4141] = "a different process with the same pid"
        fake = self.pinned_stop()
        with mock.patch.object(launcher, "port_is_served", return_value=False), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [], "a process that is not the adopted one must not be killed")
        self.assertNotIn("stopping adopted", self.journal.text())
        self.assertIn("left untouched", self.journal.text())
        self.assertEqual(supervisor.adopted, [])
        self.assertEqual(fake.handles, {}, "the pinning handle must be closed again")

    def test_07_an_identity_that_still_holds_is_stopped_through_a_pinned_handle(self) -> None:
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        self.table.processes[4141] = API_CREATED
        fake = self.pinned_stop()
        with mock.patch.object(launcher, "port_is_served", return_value=False), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [4141], "the pinned process must be terminated through its handle")
        self.assertEqual(
            fake.requested_access,
            launcher.PROCESS_TERMINATE
            | launcher.PROCESS_QUERY_LIMITED_INFORMATION
            | launcher.SYNCHRONIZE,
            "the handle must be opened for termination and for identity queries at once",
        )
        self.assertIn("stopping adopted pid 4141", self.journal.text())
        self.assertIn("adopted pid 4141 stopped", self.journal.text())
        self.assertEqual(fake.handles, {}, "the handle must not outlive the stop")

    def test_08_a_started_server_is_recorded_and_released_when_it_is_stopped(self) -> None:
        self.table.processes[5151] = API_CREATED

        class FakePopen:
            pid = 5151

            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

            def kill(self):
                pass

        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace):
            supervisor.record_own("core-api", "api", FakePopen())
            state_path, lock_path = launcher.instance_paths(self.workspace)
            stored = launcher.load_state(state_path)
            self.assertEqual(stored["api"]["pid"], 5151)
            self.assertEqual(stored["api"]["created"], API_CREATED)
            self.assertIn("api", stored["owned"])
            self.assertEqual(stored["launcher"]["pid"], os.getpid())
            self.assertFalse(lock_path.exists())
            launcher.forget_owned_roles(["api"], self.journal)
            self.assertFalse(state_path.exists(), "nothing left to own means no record is left")
            self.assertFalse(lock_path.exists(), "the lock must never outlive the launcher")

    def test_09_a_lock_left_by_a_dead_launcher_is_reclaimed_only_when_it_is_old(self) -> None:
        state_path, lock_path = launcher.instance_paths(self.workspace)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("1111|" + OLDER_CREATED, encoding="ascii")
        self.assertIsNone(
            launcher.acquire_instance_lock(lock_path, timeout=0.2),
            "a fresh lock is never broken, even when its recorded owner is gone",
        )

        old = time.time() - 3600
        os.utime(lock_path, (old, old))
        taken = launcher.acquire_instance_lock(lock_path, timeout=0.2)
        self.assertIsNotNone(taken, "an abandoned lock must not block adoption forever")
        launcher.release_instance_lock(taken)
        self.assertFalse(lock_path.exists())

        self.table.processes[2222] = "a live process"
        lock_path.write_text("2222|a live process", encoding="ascii")
        os.utime(lock_path, (old, old))
        self.assertIsNone(
            launcher.acquire_instance_lock(lock_path, timeout=0.2),
            "an old lock whose owner is alive must never be taken",
        )
        self.assertFalse(state_path.exists())

        # A launcher that died between creating the lock and writing its identity leaves nothing
        # to wait for; the age is the proof that nobody is holding it.
        lock_path.write_text("", encoding="ascii")
        os.utime(lock_path, (old, old))
        taken = launcher.acquire_instance_lock(lock_path, timeout=0.2)
        self.assertIsNotNone(taken, "an unreadable old lock must not block adoption forever")
        launcher.release_instance_lock(taken)

    # ------------------------------------------------- the loop this task closes
    def test_10_a_later_launch_adopts_the_observation_of_a_dead_launcher(self) -> None:
        """The observation left behind is what makes the second launch able to take over."""

        self.table.processes[4141] = API_CREATED
        observed = self.observed_record()
        self.assertIsNone(observed["launcher"])

        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertTrue(
                supervisor.adopt_owned("core-api", "api", launcher.API_PORT),
                "a recorded observation with no live launcher must be adoptable",
            )
        self.assertEqual(supervisor.adopted, [("core-api", 4141, API_CREATED)])
        self.assertIn("api", supervisor.owned_roles)

        state_path, lock_path = launcher.instance_paths(self.workspace)
        stored = launcher.load_state(state_path)
        self.assertIs(stored["api"]["adopted"], True)
        self.assertEqual(stored["api"]["owner"], "adopted")
        self.assertIn("api", stored["owned"])
        self.assertEqual(stored["launcher"]["pid"], os.getpid())
        self.assertFalse(lock_path.exists(), "the adoption must be atomic and release its lock")
        self.assertIn("recorded observation", self.journal.text())

    def test_11_an_observation_is_adopted_when_its_launcher_slot_holds_a_dead_process(self) -> None:
        state_path, _ = launcher.instance_paths(self.workspace)
        state = self.state(
            1111,
            OLDER_CREATED,
            api=self.observed_entry(4141, API_CREATED),
            owned=[],
        )
        launcher.save_state(state_path, state)
        self.table.processes[4141] = API_CREATED
        # 1111 is not in the table: the launcher that wrote the record is dead.
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertTrue(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["pid"], os.getpid())
        self.assertIn("api", stored["owned"])

    def test_12_no_adoption_while_another_launcher_is_alive(self) -> None:
        state_path, _ = launcher.instance_paths(self.workspace)
        state = self.state(
            1111,
            OTHER_LAUNCHER_CREATED,
            api=self.observed_entry(4141, API_CREATED),
            owned=[],
        )
        launcher.save_state(state_path, state)
        self.table.processes[1111] = OTHER_LAUNCHER_CREATED  # the observing launcher is alive
        self.table.processes[4141] = API_CREATED

        self.assertFalse(
            launcher.decide_ownership(
                state, "api", 4141, API_CREATED, workspace=self.workspace
            ).adopt,
            "a live launcher's observation must not be taken over",
        )

        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertFalse(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
        self.assertEqual(supervisor.adopted, [], "nothing may be stopped while that launcher lives")
        self.assertIn("live launcher", self.journal.text())

        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["pid"], 1111, "the live launcher's record is untouched")
        self.assertNotIn("api", stored["owned"])
        self.assertIs(stored["api"]["adopted"], False)

    def test_13_two_parallel_captures_cannot_both_adopt(self) -> None:
        """The lock serialises the capture, so a race cannot hand one server to two launchers."""

        state_path, _ = launcher.instance_paths(self.workspace)
        launcher.save_state(
            state_path,
            {
                "workspace": str(self.workspace.resolve()),
                "launcher": None,
                "api": self.observed_entry(4141, API_CREATED),
                "ui": None,
                "owned": [],
            },
        )
        self.table.processes[4141] = API_CREATED
        racing = {
            7001: OTHER_LAUNCHER_CREATED,
            7002: "2026-10-04T10:00:01.0000000+03:00",
        }
        self.table.processes.update(racing)

        # Two launchers, each a live process of its own: the fake table and a thread-local pid stand
        # in for two real processes, because the rule turns on which of them is alive.
        current = threading.local()
        real_getpid = os.getpid

        def thread_getpid():
            return getattr(current, "pid", real_getpid())

        supervisors = {}
        adopted = {}
        barrier = threading.Barrier(len(racing))

        def capture(pid):
            current.pid = pid
            supervisor = self.supervisor()
            supervisors[pid] = supervisor
            barrier.wait()
            adopted[pid] = supervisor.adopt_owned("core-api", "api", launcher.API_PORT)

        with mock.patch.object(launcher.os, "getpid", thread_getpid), mock.patch.object(
            launcher, "REPO", self.workspace
        ), mock.patch.object(
            launcher, "launcher_created_at", lambda: racing[thread_getpid()]
        ), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            threads = [threading.Thread(target=capture, args=(pid,)) for pid in racing]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        winners = [pid for pid, won in adopted.items() if won]
        self.assertEqual(len(winners), 1, f"exactly one capture may adopt, got {adopted}")
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["pid"], winners[0])
        self.assertEqual(stored["owned"], ["api"])
        self.assertIs(stored["api"]["adopted"], True)
        self.assertFalse(
            launcher.instance_paths(self.workspace)[1].exists(),
            "the lock must not outlive the capture",
        )

    def test_14_ambiguous_ownership_never_kills(self) -> None:
        """The pid and creation time can match while the process is no longer this repository's."""

        self.table.processes[4141] = API_CREATED

        def foreign_command_line(pid):  # noqa: ANN001, ARG001 - the value is what matters
            return '"C:\\Somewhere\\Else\\.venv\\Scripts\\pythonw.exe" -m app.api.main'

        for command_line in (foreign_command_line, lambda pid: None):
            journal = FakeJournal()
            supervisor = launcher.Supervisor(journal, open_window=False, url="http://127.0.0.1:8020/")
            supervisor.adopted = [("core-api", 4141, API_CREATED)]
            fake = self.pinned_stop()
            with mock.patch.object(launcher, "port_is_served", return_value=True), mock.patch.object(
                launcher, "process_command_line", command_line
            ), mock.patch.object(launcher.time, "sleep", return_value=None):
                launcher.Supervisor.stop_own(supervisor)
            self.assertEqual(fake.terminated, [], "an unproven process must never be terminated")
            self.assertIn("ownership unknown, left untouched", journal.text())
            self.assertNotIn("stopping adopted", journal.text())
            self.assertEqual(supervisor.adopted, [])

    def test_15_ports_are_reported_free_only_after_owned_servers_stop(self) -> None:
        self.table.processes[4141] = API_CREATED
        served = {launcher.API_PORT: True, launcher.UI_PORT: True}
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        fake = self.pinned_stop(
            on_terminate=lambda pid: served.__setitem__(launcher.API_PORT, False)
        )

        with mock.patch.object(launcher, "port_is_served", lambda port: served[port]), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)

        text = self.journal.text()
        self.assertEqual(fake.terminated, [4141])
        self.assertIn("stopping adopted pid 4141", text)
        self.assertLess(
            text.index("stopping adopted pid 4141"),
            text.index(f"port {launcher.API_PORT}: free"),
            "a port may only be reported free after the server that held it was stopped",
        )
        self.assertIn(f"port {launcher.UI_PORT}: still listening", text)

    def test_16_a_port_held_by_a_server_that_was_not_stopped_stays_occupied(self) -> None:
        self.table.processes[4141] = API_CREATED
        served = {launcher.API_PORT: True, launcher.UI_PORT: True}
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        fake = self.pinned_stop()

        with mock.patch.object(launcher, "port_is_served", lambda port: served[port]), mock.patch.object(
            launcher, "process_command_line", lambda pid: None
        ), mock.patch.object(launcher.time, "sleep", return_value=None):
            launcher.Supervisor.stop_own(supervisor)

        self.assertEqual(fake.terminated, [])
        self.assertIn(f"port {launcher.API_PORT}: still listening", self.journal.text())
        self.assertIn("ownership unknown, left untouched", self.journal.text())


    # ------------------------------------------------- identity of the current launcher
    def test_17_both_roles_of_the_current_launcher_are_adopted_in_one_run(self) -> None:
        """The measured defect: adopting ``api`` rewrote the launcher slot, so ``ui`` was no longer ``self``."""

        state_path, _ = launcher.instance_paths(self.workspace)
        self.table.processes[4141] = API_CREATED
        self.table.processes[4242] = UI_CREATED
        launcher.save_state(
            state_path,
            {
                "workspace": str(self.workspace.resolve()),
                "launcher": {"pid": os.getpid(), "created": SELF_CREATED},
                "api": self.observed_entry(4141, API_CREATED),
                "ui": self.observed_entry(4242, UI_CREATED),
                "owned": [],
                "windows": [],
            },
        )
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher,
            "server_identity",
            side_effect=[(4141, API_CREATED), (4242, UI_CREATED)],
        ):
            self.assertTrue(
                supervisor.adopt_owned("core-api", "api", launcher.API_PORT),
                "the api server of this very launcher must be adoptable",
            )
            self.assertTrue(
                supervisor.adopt_owned("shell-server", "ui", launcher.UI_PORT),
                "the ui server of this very launcher must stay adoptable after the api adoption rewrote "
                "the launcher slot",
            )
        self.assertEqual(
            supervisor.adopted,
            [("core-api", 4141, API_CREATED), ("shell-server", 4242, UI_CREATED)],
        )
        self.assertEqual(supervisor.owned_roles, ["api", "ui"])
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["owned"], ["api", "ui"])
        self.assertEqual(stored["launcher"], {"pid": os.getpid(), "created": SELF_CREATED})
        self.assertIs(stored["api"]["adopted"], True)
        self.assertIs(stored["ui"]["adopted"], True)
        self.assertNotIn("cannot be proven gone", self.journal.text())
        self.assertIn("this launcher left behind", self.journal.text())

    def test_18_a_record_with_this_pid_and_another_creation_time_is_not_self(self) -> None:
        state_path, _ = launcher.instance_paths(self.workspace)
        state = {
            "workspace": str(self.workspace.resolve()),
            "launcher": {"pid": os.getpid(), "created": RECYCLED_CREATED},
            "api": self.observed_entry(4141, API_CREATED),
            "ui": None,
            "owned": [],
            "windows": [],
        }
        launcher.save_state(state_path, state)
        self.table.processes[4141] = API_CREATED
        self.assertEqual(
            launcher.recorded_launcher_status(state, me=os.getpid(), me_created=SELF_CREATED),
            "unknown",
            "a pid that matches with another creation time is a different, possibly live launcher",
        )
        decision = launcher.decide_ownership(
            state,
            "api",
            4141,
            API_CREATED,
            workspace=self.workspace,
            me=os.getpid(),
            me_created=SELF_CREATED,
        )
        self.assertFalse(decision.adopt)
        self.assertIn("proven gone", decision.reason)

        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "server_identity", return_value=(4141, API_CREATED)
        ):
            self.assertFalse(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
        self.assertEqual(supervisor.adopted, [], "nothing may be stopped on a matching pid alone")
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["created"], RECYCLED_CREATED)

    def test_19_a_live_foreign_launcher_blocks_adoption_for_both_roles(self) -> None:
        state_path, _ = launcher.instance_paths(self.workspace)
        launcher.save_state(
            state_path,
            {
                "workspace": str(self.workspace.resolve()),
                "launcher": {"pid": 1111, "created": OTHER_LAUNCHER_CREATED},
                "api": self.observed_entry(4141, API_CREATED),
                "ui": self.observed_entry(4242, UI_CREATED),
                "owned": [],
                "windows": [],
            },
        )
        self.table.processes[1111] = OTHER_LAUNCHER_CREATED
        self.table.processes[4141] = API_CREATED
        self.table.processes[4242] = UI_CREATED
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher,
            "server_identity",
            side_effect=[(4141, API_CREATED), (4242, UI_CREATED)],
        ):
            self.assertFalse(supervisor.adopt_owned("core-api", "api", launcher.API_PORT))
            self.assertFalse(supervisor.adopt_owned("shell-server", "ui", launcher.UI_PORT))
        self.assertEqual(supervisor.adopted, [])
        self.assertIn("live launcher", self.journal.text())
        stored = launcher.load_state(state_path)
        self.assertEqual(stored["launcher"]["pid"], 1111, "the live launcher's record is untouched")

    # ------------------------------------------------------------- window registration
    def test_20_a_window_closed_before_it_is_registered_is_still_seen_closed(self) -> None:
        """The registration is written first, so a window gone before the wait starts is not lost."""

        supervisor = self.supervisor()
        state_path, lock_path = launcher.instance_paths(self.workspace)
        with mock.patch.object(launcher, "REPO", self.workspace):
            registered = supervisor.register_windows([9001], pid=DEAD_BROWSER_PID)
        self.assertEqual([entry["hwnd"] for entry in registered], [9001])
        stored = launcher.load_state(state_path)
        self.assertEqual(
            stored["windows"],
            [
                {
                    "hwnd": 9001,
                    "pid": DEAD_BROWSER_PID,
                    "created": None,
                    "registered": mock.ANY,
                }
            ],
            "the window identity must be in the instance record",
        )
        self.assertFalse(lock_path.exists(), "registration holds the instance lock only for the write")

        checks = {"value": 0}

        def gone(handle):  # noqa: ANN001, ARG001 - the window is already gone
            checks["value"] += 1
            return False

        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "window_exists", gone
        ), mock.patch.object(launcher.time, "sleep", return_value=None):
            misses = supervisor.await_windows_closed()
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertEqual(
            checks["value"],
            launcher.WINDOW_MISS_CHECKS,
            "the close must be concluded after exactly the bounded number of checks",
        )
        self.assertIn(
            f"check {launcher.WINDOW_MISS_CHECKS} of {launcher.WINDOW_MISS_CHECKS}",
            self.journal.text(),
        )
        with mock.patch.object(launcher, "REPO", self.workspace):
            supervisor.unregister_windows()
        self.assertEqual(launcher.load_state(state_path)["windows"], [])

    def test_21_a_window_is_registered_before_it_is_reported_usable(self) -> None:
        """A close that happens between "found" and "usable" used to be missed entirely."""

        supervisor = self.supervisor()
        state_path, _ = launcher.instance_paths(self.workspace)

        class FakeBrowser:
            pid = DEAD_BROWSER_PID
            returncode = None

            def poll(self):
                return None

        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher.subprocess, "Popen", return_value=FakeBrowser()
        ), mock.patch.object(launcher, "window_handles", return_value=[9001]), mock.patch.object(
            launcher, "bring_to_front", return_value=True
        ):
            restored, conflicted = supervisor.open_window_with_profile(
                self.workspace / "webview-profile"
            )
        self.assertTrue(restored)
        self.assertFalse(conflicted)
        self.assertEqual(
            [entry["hwnd"] for entry in launcher.load_state(state_path)["windows"]],
            [9001],
            "the window must be registered before the caller is told it is usable",
        )
        # The window is closed immediately after it appeared: the wait must still conclude, and quickly.
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "window_exists", lambda handle: False
        ), mock.patch.object(launcher.time, "sleep", return_value=None):
            misses = supervisor.await_windows_closed()
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertIn("window registered", self.journal.text())

    def test_22_a_transient_disappearance_does_not_end_the_wait(self) -> None:
        """A hidden window and a handle that is gone under a live browser prove nothing on their own."""

        supervisor = self.supervisor()
        self.table.processes[BROWSER_PID] = BROWSER_CREATED
        with mock.patch.object(launcher, "REPO", self.workspace):
            supervisor.register_windows([9001], pid=BROWSER_PID)
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                # Minimised or briefly hidden: still a window, still open.
                ([(9001, launcher.WINDOW_HIDDEN)], alive),
                ([(9001, launcher.WINDOW_HIDDEN)], alive),
                # Visible again.
                ([(9001, launcher.WINDOW_OPEN)], []),
                # The handle is gone, but the browser that owns the window is still running.
                ([(9001, launcher.WINDOW_GONE)], alive),
                ([(9001, launcher.WINDOW_GONE)], alive),
                # Only a gone handle and an exited browser count, and only three times in a row.
                ([(9001, launcher.WINDOW_GONE)], []),
                ([(9001, launcher.WINDOW_GONE)], []),
                ([(9001, launcher.WINDOW_GONE)], []),
            ]
        )
        supervisor.window_check = script
        supervisor.recover_windows = lambda browsers: []
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            misses = supervisor.await_windows_closed()
        self.assertEqual(
            script.scans_seen,
            8,
            "the wait must survive hidden and replaced-window checks and only stop on three empty ones",
        )
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertIn("still a window but not visible", self.journal.text())
        self.assertIn("is still alive; continuing to wait", self.journal.text())
        self.assertEqual(
            supervisor.adopted,
            [("core-api", 4141, API_CREATED)],
            "nothing may be stopped while a registered window is still there",
        )
        self.assertNotIn("stopping adopted", self.journal.text())

    def test_23_the_last_of_two_registered_windows_ends_the_wait(self) -> None:
        supervisor = self.supervisor()
        self.table.processes[BROWSER_PID] = BROWSER_CREATED
        with mock.patch.object(launcher, "REPO", self.workspace):
            supervisor.register_windows([9001, 9002], pid=BROWSER_PID)
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                ([(9001, launcher.WINDOW_OPEN), (9002, launcher.WINDOW_OPEN)], alive),
                ([(9001, launcher.WINDOW_OPEN), (9002, launcher.WINDOW_OPEN)], alive),
                # One of two windows closed: the other one keeps the session open.
                ([(9001, launcher.WINDOW_GONE), (9002, launcher.WINDOW_OPEN)], alive),
                ([(9001, launcher.WINDOW_GONE), (9002, launcher.WINDOW_GONE)], alive),
                ([(9001, launcher.WINDOW_GONE), (9002, launcher.WINDOW_GONE)], []),
                ([(9001, launcher.WINDOW_GONE), (9002, launcher.WINDOW_GONE)], []),
                ([(9001, launcher.WINDOW_GONE), (9002, launcher.WINDOW_GONE)], []),
            ]
        )
        supervisor.window_check = script
        supervisor.recover_windows = lambda browsers: []
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            misses = supervisor.await_windows_closed()
        self.assertEqual(
            script.scans_seen,
            7,
            "closing one of two windows must not end the wait; only the last one may, and only "
            "after three empty checks",
        )
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)

        supervisor.window_states = lambda: [
            (9001, launcher.WINDOW_GONE),
            (9002, launcher.WINDOW_OPEN),
        ]
        self.assertEqual(supervisor.present_windows(), [9002])

    # ------------------------------------------------------- pinned process termination
    def test_24_the_creation_time_is_rechecked_through_the_pinned_handle(self) -> None:
        self.table.processes[4141] = API_CREATED
        started = launcher.recorded_creation_ticks(API_CREATED)
        self.assertIsNotNone(started, "the recorded creation time must be comparable with a FILETIME")
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        # The pid and the provider both still say API_CREATED; only the handle reports another process.
        fake = self.pinned_stop(start_ticks={4141: started + 10_000_000})
        with mock.patch.object(launcher, "port_is_served", return_value=True), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [], "a creation time read from the handle outweighs the pid")
        self.assertIn("different creation time", self.journal.text())
        self.assertIn("ownership unknown, left untouched", self.journal.text())
        self.assertEqual(fake.handles, {}, "the handle must be released even when the stop is refused")

    def test_25_a_command_line_that_does_not_name_the_repo_is_refused(self) -> None:
        self.table.processes[4141] = API_CREATED
        supervisor = self.supervisor()
        supervisor.adopted = [("shell-server", 4141, API_CREATED)]
        fake = self.pinned_stop()
        with mock.patch.object(launcher, "port_is_served", return_value=True), mock.patch.object(
            launcher,
            "process_command_line",
            lambda pid: '"C:\\Somewhere\\Else\\.venv\\Scripts\\pythonw.exe" -B -X utf8 -m app.api.main',
        ), mock.patch.object(launcher.time, "sleep", return_value=None):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [])
        self.assertIn("does not name this repository", self.journal.text())
        self.assertIn("ownership unknown, left untouched", self.journal.text())

    def test_26_an_already_exited_process_is_a_normal_outcome(self) -> None:
        # The pid still exists, but the pinned process has already finished.
        self.table.processes[4141] = API_CREATED
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        fake = self.pinned_stop(exited={4141})
        with mock.patch.object(launcher, "port_is_served", return_value=False), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [], "an exited process is not terminated again")
        self.assertIn("had already exited", self.journal.text())
        self.assertNotIn("did not stop", self.journal.text())
        self.assertEqual(supervisor.adopted, [])

        # The pid no longer exists at all: OpenProcess answers ERROR_INVALID_PARAMETER.
        self.journal = FakeJournal()
        supervisor = self.supervisor()
        supervisor.adopted = [("shell-server", 4242, UI_CREATED)]
        fake = self.pinned_stop()
        with mock.patch.object(launcher, "port_is_served", return_value=False), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [])
        self.assertIn("had already exited", self.journal.text())
        self.assertEqual(supervisor.adopted, [])

    def test_27_a_process_that_cannot_be_pinned_is_left_untouched(self) -> None:
        self.table.processes[4141] = API_CREATED
        supervisor = self.supervisor()
        supervisor.adopted = [("core-api", 4141, API_CREATED)]
        fake = self.pinned_stop(refuse={4141})
        with mock.patch.object(launcher, "port_is_served", return_value=True), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            launcher.Supervisor.stop_own(supervisor)
        self.assertEqual(fake.terminated, [])
        self.assertIn("cannot be pinned", self.journal.text())
        self.assertIn("ownership unknown, left untouched", self.journal.text())


if __name__ == "__main__":
    unittest.main()
