"""Regression tests for the launcher's window identity (``tools/forge_launch.py``).

Measured on 2026-10-18: a launcher opened an Edge ``--app`` window at ``127.0.0.1:8020``, the window
was registered, the application stayed on screen -- and the launcher still concluded "window closed"
and stopped both servers, which left Edge showing a "cannot connect" page. The close was decided on a
window *title* scan, and Edge titles an ``--app`` window after the page, so the title the loop looked
for could disappear while the window did not.

The contract these tests pin down:

* the **registered handle is the primary identity** -- while ``IsWindow`` says the handle is still a
  window, the application is open, whatever the title says, and a missing enumeration cannot override it;
* a hidden or minimised window is still open;
* a destroyed handle only concludes the close once the browser process that owned it (pid *and*
  creation time) has exited too, and only after three consecutive agreeing checks;
* a handle that now belongs to another process -- stale or recycled -- is never the original window;
* only the servers this launcher provably owns are stopped when the window finally closes.

Nothing here starts Edge, terminates a real process or touches a real instance record: Win32 is mocked
at the boundary the launcher asks through, and every record lives under a temporary directory.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("forge_launch", ROOT / "tools" / "forge_launch.py")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules["forge_launch"] = launcher
SPEC.loader.exec_module(launcher)

BROWSER_PID = 5050
BROWSER_CREATED = "2026-10-18T14:35:13.0000000+03:00"
RECYCLED_CREATED = "2026-10-18T16:00:00.0000000+03:00"
OTHER_OWNER_PID = 7777
HWND = 9964364
OTHER_HWND = 44107920


class FakeJournal:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str) -> None:
        self.lines.append(str(message))

    def text(self) -> str:
        return "\n".join(self.lines)


class FakeProcessTable:
    """The process facts the launcher may consult, keyed by pid."""

    def __init__(self, processes=None) -> None:
        self.processes = {int(pid): created for pid, created in (processes or {}).items()}

    def created_at(self, pid, refresh: bool = False):
        try:
            return self.processes.get(int(pid))
        except (TypeError, ValueError):
            return None

    def exists(self, pid) -> bool:
        try:
            return int(pid) in self.processes
        except (TypeError, ValueError):
            return False


class ScriptedChecks:
    """What each successive check of the wait loop answers: ``(states, browsers)`` per scan."""

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


class FakeOwn:
    """A server process this launcher started: stopping it is allowed and recorded."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.stopped = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.stopped = True
        self.returncode = 0

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:
        self.returncode = -9


class ForeignProcess:
    """A process the launcher must never terminate."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None

    def poll(self):
        return None

    def terminate(self):
        raise AssertionError("a foreign process must never be terminated")

    def wait(self, timeout=None):
        raise AssertionError("a foreign process must never be waited on")

    def kill(self):
        raise AssertionError("a foreign process must never be killed")


class WindowIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.journal = FakeJournal()
        self.table = FakeProcessTable({BROWSER_PID: BROWSER_CREATED})
        for attribute, replacement in (
            ("process_created_at", self.table.created_at),
            ("pid_exists", self.table.exists),
        ):
            patcher = mock.patch.object(launcher, attribute, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        launcher.launcher_created_at.__dict__.pop("value", None)
        self.addCleanup(launcher.launcher_created_at.__dict__.pop, "value", None)
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.workspace = pathlib.Path(self.scratch.name)

    # ------------------------------------------------------------------ helpers
    def supervisor(self) -> "launcher.Supervisor":
        return launcher.Supervisor(self.journal, open_window=False, url="http://127.0.0.1:8020/")

    def registered(self, handles=(HWND,), pid=BROWSER_PID):
        supervisor = self.supervisor()
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "window_owner_pid", return_value=0
        ):
            supervisor.register_windows(list(handles), pid=pid)
        return supervisor

    def wait(self, supervisor, script):
        """Run the wait loop on a scripted check sequence, without real sleeping."""

        supervisor.window_check = script
        supervisor.recover_windows = lambda browsers: []
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            return supervisor.await_windows_closed()

    # ------------------------------------------------------------- the handle wins
    def test_01_a_registered_handle_keeps_the_session_open_while_its_title_changes(self) -> None:
        """The title is a page fact, not the identity of the window: the handle decides."""

        supervisor = self.registered()
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
            ]
        )
        with mock.patch.object(
            launcher, "window_title", side_effect=AssertionError("the title must not decide the close")
        ):
            misses = self.wait(supervisor, script)
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertEqual(
            script.scans_seen,
            7,
            "four checks with a live handle must not end the session; only the three empty ones may",
        )
        self.assertNotIn("stopping pid", self.journal.text())

    def test_02_missing_title_enumeration_does_not_override_a_valid_registered_handle(self) -> None:
        """A scan that finds no title proves nothing while the registered handle is still a window."""

        supervisor = self.registered((HWND, OTHER_HWND))
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                ([(HWND, launcher.WINDOW_OPEN), (OTHER_HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_OPEN), (OTHER_HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_GONE), (OTHER_HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_GONE), (OTHER_HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE), (OTHER_HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE), (OTHER_HWND, launcher.WINDOW_GONE)], []),
            ]
        )
        with mock.patch.object(launcher, "window_handles", side_effect=AssertionError(
            "the close must be decided on handles, not on a title enumeration"
        )):
            misses = self.wait(supervisor, script)
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertEqual(
            script.scans_seen,
            6,
            "the close is decided on handles; a failing title scan must not shorten the session",
        )

    def test_03_temporary_invisibility_and_minimisation_do_not_trigger_shutdown(self) -> None:
        """Hiding or minimising a window keeps it open; only a destroyed handle plus a dead browser ends it."""

        supervisor = self.registered()
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                ([(HWND, launcher.WINDOW_HIDDEN)], alive),
                ([(HWND, launcher.WINDOW_HIDDEN)], alive),
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_HIDDEN)], alive),
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
            ]
        )
        misses = self.wait(supervisor, script)
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertEqual(script.scans_seen, 8, "a hidden window must not shorten the wait")
        self.assertIn("still a window but not visible right now", self.journal.text())
        self.assertEqual(
            [entry["hwnd"] for entry in supervisor.registered_windows()],
            [HWND],
            "the registration must survive a hidden window",
        )

    # ------------------------------------------------------ destruction and browser
    def test_04_a_destroyed_handle_waits_for_the_browser_and_then_stops_only_ours(self) -> None:
        """Handle gone is not enough: the browser must have exited, three times in a row."""

        own_api = FakeOwn(23864)
        own_ui = FakeOwn(3920)
        foreign = ForeignProcess(99999)  # an unrelated process: named here only to assert it is untouched
        supervisor = self.supervisor()
        supervisor.own = [("core-api", own_api), ("shell-server", own_ui)]
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "window_owner_pid", return_value=0
        ):
            supervisor.register_windows([HWND], pid=BROWSER_PID)
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                ([(HWND, launcher.WINDOW_OPEN)], alive),
                # The handle is destroyed while the browser still runs: the window may be recreated.
                ([(HWND, launcher.WINDOW_GONE)], alive),
                ([(HWND, launcher.WINDOW_GONE)], alive),
                # The browser has exited now: three agreeing checks conclude the close.
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
            ]
        )
        misses = self.wait(supervisor, script)
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertEqual(script.scans_seen, 6, "one empty check must never be enough")
        self.assertIn("is still alive; continuing to wait", self.journal.text())
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "port_is_served", return_value=False
        ):
            supervisor.unregister_windows()
            launcher.Supervisor.stop_own(supervisor)
        self.assertTrue(own_api.stopped and own_ui.stopped, "the launcher's own servers must be stopped")
        self.assertIsNone(foreign.returncode, "an unrelated process must never be touched")
        self.assertIn("port 8011: free", self.journal.text())
        self.assertIn("port 8020: free", self.journal.text())
        stopped = [line for line in self.journal.lines if "stopping pid" in line]
        self.assertEqual(len(stopped), 2, f"exactly the two owned servers may be stopped: {stopped}")

    def test_05_a_recycled_pid_is_not_the_browser_behind_a_destroyed_window(self) -> None:
        """A pid that was recycled does not keep a dead window open, and does not end a live one."""

        supervisor = self.registered()
        # The recorded browser process is gone; the same pid now belongs to a later process.
        self.table.processes[BROWSER_PID] = RECYCLED_CREATED
        self.assertEqual(
            supervisor.registered_browser_processes(),
            [],
            "a recycled pid must not be mistaken for the browser that owned the window",
        )
        window = {"exists": True}

        def exists(handle):  # noqa: ANN001, ARG001 - one window is under test
            return window["exists"]

        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "window_exists", exists
        ), mock.patch.object(launcher, "window_is_visible", return_value=True), mock.patch.object(
            launcher, "window_owner_pid", return_value=BROWSER_PID
        ), mock.patch.object(launcher.time, "sleep", return_value=None):
            # The handle exists, but its owner is a later process: a stale registration, not open.
            self.assertEqual(supervisor.present_windows(), [])
            self.assertEqual(
                [state for _handle, state in supervisor.window_states()],
                [launcher.WINDOW_FOREIGN],
            )
            window["exists"] = False
            misses = supervisor.await_windows_closed()
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)

    # ------------------------------------------------------------ stale registrations
    def test_06_a_reused_handle_is_never_the_original_window(self) -> None:
        """The handle exists, but its owner is another process: the registration is stale, not open."""

        entry = {"hwnd": HWND, "pid": BROWSER_PID, "created": BROWSER_CREATED}
        with mock.patch.object(launcher, "window_exists", return_value=True), mock.patch.object(
            launcher, "window_is_visible", return_value=True
        ), mock.patch.object(launcher, "window_owner_pid", return_value=OTHER_OWNER_PID):
            self.assertEqual(launcher.registered_window_state(entry), launcher.WINDOW_FOREIGN)
        # The same pid, but a later process behind it, is stale too.
        with mock.patch.object(launcher, "window_exists", return_value=True), mock.patch.object(
            launcher, "window_is_visible", return_value=True
        ), mock.patch.object(launcher, "window_owner_pid", return_value=BROWSER_PID):
            self.table.processes[BROWSER_PID] = RECYCLED_CREATED
            self.assertEqual(launcher.registered_window_state(entry), launcher.WINDOW_FOREIGN)
        # The original process is still there: the handle is genuinely this window.
        self.table.processes[BROWSER_PID] = BROWSER_CREATED
        with mock.patch.object(launcher, "window_exists", return_value=True), mock.patch.object(
            launcher, "window_is_visible", return_value=True
        ), mock.patch.object(launcher, "window_owner_pid", return_value=BROWSER_PID):
            self.assertEqual(launcher.registered_window_state(entry), launcher.WINDOW_OPEN)
        # A handle nobody can vouch for never ends a session: the handle itself is the identity.
        with mock.patch.object(launcher, "window_exists", return_value=True), mock.patch.object(
            launcher, "window_is_visible", return_value=False
        ), mock.patch.object(launcher, "window_owner_pid", return_value=0):
            self.assertEqual(launcher.registered_window_state(entry), launcher.WINDOW_HIDDEN)

        # In the wait loop a stale handle counts as gone, so the session ends only after three checks.
        supervisor = self.registered()
        stale = ScriptedChecks(
            [
                ([(HWND, launcher.WINDOW_FOREIGN)], []),
                ([(HWND, launcher.WINDOW_FOREIGN)], []),
                ([(HWND, launcher.WINDOW_FOREIGN)], []),
            ]
        )
        misses = self.wait(supervisor, stale)
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertEqual(stale.scans_seen, 3, "a stale handle must not be counted as open")
        self.assertIn("a stale registration", self.journal.text())

    def test_10_an_ordinary_browser_window_is_never_the_application_window(self) -> None:
        """Measured on 2026-10-18: the host-title fallback adopted the owner's own Edge window.

        The launcher's window is an ``--app`` window and carries no browser suffix; a normal browser
        window does. Only the former may ever be treated as the application window.
        """

        for title in (
            # The measured title, with the zero-width space Edge puts between "Microsoft" and "Edge".
            "FORGE AI — Command Center и еще 1 страница — Личный: Microsoft\u200b Edge",
            "127.0.0.1 и еще 1 страница — Личный: Microsoft Edge",
            "127.0.0.1 - Google Chrome",
            "FORGE AI — Command Center - Google Chrome",
        ):
            with mock.patch.object(launcher, "window_title", return_value=title):
                self.assertTrue(
                    launcher.window_is_another_browser(HWND),
                    f"a normal browser window must never be adopted: {title!r}",
                )
        for title in ("FORGE AI — Command Center", "127.0.0.1_/"):
            with mock.patch.object(launcher, "window_title", return_value=title):
                self.assertFalse(
                    launcher.window_is_another_browser(HWND),
                    f"an --app window is the application window: {title!r}",
                )

    def test_07_the_window_owner_is_recorded_not_the_launched_relay_process(self) -> None:
        """A browser invocation can hand its window to an already running browser and exit."""

        supervisor = self.supervisor()
        self.table.processes[OTHER_OWNER_PID] = BROWSER_CREATED
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "window_owner_pid", return_value=OTHER_OWNER_PID
        ):
            registered = supervisor.register_windows([HWND], pid=4242)
        self.assertEqual(
            [(entry["pid"], entry["created"]) for entry in registered],
            [(OTHER_OWNER_PID, BROWSER_CREATED)],
            "the process that owns the handle is the identity, not the launched relay",
        )
        self.assertIn("belongs to pid 7777, not the launched pid 4242", self.journal.text())
        state_path, _ = launcher.instance_paths(self.workspace)
        self.assertEqual(launcher.load_state(state_path)["windows"][0]["pid"], OTHER_OWNER_PID)

    def test_08_a_replaced_handle_under_a_live_browser_is_registered_again(self) -> None:
        """The browser can replace its window handle while the window stays on screen."""

        supervisor = self.registered((HWND,))
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher, "windows_of_process", return_value=[OTHER_HWND]
        ), mock.patch.object(launcher, "window_owner_pid", return_value=BROWSER_PID):
            recovered = supervisor.recover_windows([(BROWSER_PID, BROWSER_CREATED)])
        self.assertEqual([entry["hwnd"] for entry in recovered], [OTHER_HWND])
        self.assertEqual(
            [entry["hwnd"] for entry in supervisor.registered_windows()],
            [OTHER_HWND],
            "the stale handle must be replaced by the handle the browser really owns",
        )
        self.assertIn("re-registered", self.journal.text())
        self.assertEqual(
            launcher.load_state(launcher.instance_paths(self.workspace)[0])["windows"][0]["hwnd"],
            OTHER_HWND,
        )

    def test_09_recovery_after_empty_checks_continues_the_wait(self) -> None:
        """Two empty checks followed by a recovered handle must not be called a close."""

        supervisor = self.registered((HWND,))
        alive = [(BROWSER_PID, BROWSER_CREATED)]
        script = ScriptedChecks(
            [
                ([(HWND, launcher.WINDOW_GONE)], []),
                ([(HWND, launcher.WINDOW_GONE)], []),
                # The browser turns out to be alive after all: the window is found again.
                ([(HWND, launcher.WINDOW_GONE)], alive),
                ([(OTHER_HWND, launcher.WINDOW_OPEN)], alive),
                ([(OTHER_HWND, launcher.WINDOW_GONE)], []),
                ([(OTHER_HWND, launcher.WINDOW_GONE)], []),
                ([(OTHER_HWND, launcher.WINDOW_GONE)], []),
            ]
        )
        supervisor.window_check = script
        supervisor.recover_windows = lambda browsers: [{"hwnd": OTHER_HWND}]
        with mock.patch.object(launcher, "REPO", self.workspace), mock.patch.object(
            launcher.time, "sleep", return_value=None
        ):
            misses = supervisor.await_windows_closed()
        self.assertEqual(misses, launcher.WINDOW_MISS_CHECKS)
        self.assertIn("visible again after 2 empty check(s)", self.journal.text())
        self.assertEqual(script.scans_seen, 7)


class WindowReloadTests(unittest.TestCase):
    """A window opened before a rebuild must load the new bundle, not keep the old one.

    Measured failure: the launcher only brought an already-open window forward, so after
    ``vite build`` the owner kept seeing the previous interface while the server on
    ``ui/dist`` was already serving the new, content-hashed assets. The fix is a reload
    step on the reuse path; these tests pin it without starting a browser.
    """

    def test_01_reusing_a_window_reloads_it_instead_of_only_bringing_it_forward(self) -> None:
        journal = FakeJournal()
        supervisor = mock.Mock()
        supervisor.journal = journal
        supervisor.register_windows = mock.Mock()
        supervisor.await_windows_closed = mock.Mock()
        supervisor.unregister_windows = mock.Mock()
        with mock.patch.object(launcher, "window_handles", return_value=[HWND]), \
                mock.patch.object(launcher, "window_is_another_browser", return_value=False), \
                mock.patch.object(launcher, "bring_to_front", return_value=True), \
                mock.patch.object(launcher, "window_title", return_value="FORGE AI"), \
                mock.patch.object(launcher, "_websocket_reload", return_value=True) as reloaded, \
                mock.patch.object(launcher.time, "sleep", return_value=None):
            code = launcher.Supervisor.close_window(supervisor)
        self.assertEqual(code, 0)
        reloaded.assert_called_once()
        supervisor.register_windows.assert_called_once()
        self.assertIn("hard-reloaded", journal.text())
        self.assertIn("already open", journal.text())

    def test_02_the_devtools_reload_is_preferred_and_names_the_build(self) -> None:
        journal = FakeJournal()
        with mock.patch.object(launcher, "window_handles", return_value=[HWND]), \
                mock.patch.object(launcher, "window_is_another_browser", return_value=False), \
                mock.patch.object(launcher, "bring_to_front", return_value=True), \
                mock.patch.object(launcher, "_websocket_reload", return_value=True), \
                mock.patch.object(launcher, "build_stamp", return_value="abc123abc123"):
            reloaded = launcher.hard_reload_window("FORGE AI", journal)
        self.assertTrue(reloaded)
        self.assertIn("DevTools endpoint", journal.text())
        self.assertIn("abc123abc123", journal.text())

    def test_03_without_devtools_the_keyboard_fallback_is_used_only_in_front(self) -> None:
        journal = FakeJournal()
        with mock.patch.object(launcher, "window_handles", return_value=[HWND]), \
                mock.patch.object(launcher, "window_is_another_browser", return_value=False), \
                mock.patch.object(launcher, "bring_to_front", return_value=True), \
                mock.patch.object(launcher, "_websocket_reload", return_value=False), \
                mock.patch.object(launcher, "_hard_reload_keys", return_value=True) as keys, \
                mock.patch.object(launcher, "build_stamp", return_value="deadbeefdead"):
            self.assertTrue(launcher.hard_reload_window("FORGE AI", journal))
            keys.assert_called_once_with(HWND)

        # When the window cannot be proven to be in front, no keystroke is sent: a
        # Ctrl+Shift+R typed into another application would be worse than a stale page.
        journal = FakeJournal()
        with mock.patch.object(launcher, "window_handles", return_value=[HWND]), \
                mock.patch.object(launcher, "window_is_another_browser", return_value=False), \
                mock.patch.object(launcher, "bring_to_front", return_value=True), \
                mock.patch.object(launcher, "_websocket_reload", return_value=False), \
                mock.patch.object(launcher, "_hard_reload_keys", return_value=False) as keys, \
                mock.patch.object(launcher, "build_stamp", return_value="deadbeefdead"):
            self.assertTrue(launcher.hard_reload_window("FORGE AI", journal))
            keys.assert_called_once_with(HWND)
        self.assertIn("could not be reloaded automatically", journal.text())
        self.assertIn("«Обновить»", journal.text())

    def test_04_the_open_window_gets_a_devtools_endpoint_to_reload_through(self) -> None:
        # The endpoint is loopback-only and exists for the reload step; without it the
        # reuse path could only send keystrokes to a window it does not own.
        source = (ROOT / "tools" / "forge_launch.py").read_text(encoding="utf-8")
        self.assertIn("--remote-debugging-port=", source)
        self.assertIn("DEVTOOLS_PORT", source)
        self.assertIn('"Page.reload"', source)
        self.assertIn("ignoreCache", source)


if __name__ == "__main__":
    unittest.main()
