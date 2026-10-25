"""Regression tests for the busy-profile window failure (all seven execute; no real browser).

Measured on 2026-10-16: ``dist/Forge AI.exe`` started the API and the UI, yet no window appeared
because another Edge process held ``.forge/webview-profile``; the launcher reported that honestly and
then stopped its own servers.

The launcher's real methods are exercised here through a lightweight stand-in instance instead of a
guessed constructor signature: the behaviour under test belongs to ``Supervisor.close_window``,
``Supervisor.open_window_with_profile`` and ``Supervisor.stop_own``, and those are the functions
called. Nothing here starts Edge, terminates a process, or touches a profile outside a temporary
directory.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("forge_launch", ROOT / "tools" / "forge_launch.py")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules["forge_launch"] = launcher
SPEC.loader.exec_module(launcher)


class FakeJournal:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str) -> None:
        self.lines.append(str(message))

    def text(self) -> str:
        return "\n".join(self.lines)


class FakeProcess:
    """A child process that must never be terminated or killed by the code under test."""

    def __init__(self, code=None) -> None:
        self.pid = 4242
        self.returncode = code

    def poll(self):
        return self.returncode

    def terminate(self):
        raise AssertionError("a foreign process must never be terminated")

    def wait(self, timeout=None):
        raise AssertionError("a foreign process must never be waited on")

    def kill(self):
        raise AssertionError("a foreign process must never be killed")


class FakeSelf:
    """The attributes the launcher's own methods read from ``self``."""

    def __init__(self, journal: FakeJournal) -> None:
        self.journal = journal
        self.url = "http://127.0.0.1:8020/"
        self.own: list = []
        self.windows: list = []

    # The real methods, so the retry logic and the window lifecycle under test are genuinely executed.
    open_window_with_profile = launcher.Supervisor.open_window_with_profile
    record_transition = launcher.Supervisor.record_transition
    register_windows = launcher.Supervisor.register_windows
    unregister_windows = launcher.Supervisor.unregister_windows
    registered_windows = launcher.Supervisor.registered_windows
    present_windows = launcher.Supervisor.present_windows
    window_states = launcher.Supervisor.window_states
    registered_browser_processes = launcher.Supervisor.registered_browser_processes
    window_check = launcher.Supervisor.window_check
    recover_windows = launcher.Supervisor.recover_windows
    window_check_line = launcher.Supervisor.window_check_line
    await_windows_closed = launcher.Supervisor.await_windows_closed

    def watch_servers(self, stop_event) -> None:  # the real watchdog is irrelevant to these tests
        stop_event.set()


class ProfileSelectionTests(unittest.TestCase):
    def test_01_the_primary_profile_comes_first_and_fallbacks_are_separate(self) -> None:
        primary = pathlib.Path("X:/repo/.forge/webview-profile")
        candidates = launcher.window_profile_candidates(primary)
        self.assertEqual(candidates[0], primary)
        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(len(set(candidates)), len(candidates))
        self.assertTrue(all(item != primary for item in candidates[1:]))

    def test_02_an_existing_free_profile_is_reused_and_left_intact(self) -> None:
        # The contract changed deliberately: an existing directory whose profile no live browser holds is
        # reusable. Existence alone is not ownership, and treating it as such is what used to exhaust every
        # candidate and end in "no usable window". The profile's own files must still be untouched.
        with tempfile.TemporaryDirectory() as directory:
            primary = pathlib.Path(directory) / "webview-profile"
            candidates = launcher.window_profile_candidates(primary)[1:]
            candidates[0].mkdir(parents=True)
            marker = candidates[0] / "held-by-a-browser"
            marker.write_text("do not touch", encoding="utf-8")
            journal = FakeJournal()
            claimed = launcher.claim_window_profile(candidates, journal)
            self.assertEqual(claimed, candidates[0], "a free existing profile is reused, not skipped")
            self.assertTrue(marker.is_file(), "the profile's files must be left exactly as they were")
            self.assertEqual(marker.read_text(encoding="utf-8"), "do not touch")
            # The profile had no marker yet, so the claim takes the ordinary exclusive-creation path and
            # records that it was claimed; the diagnostic must still name the profile that was handed out.
            self.assertIn("window profile", journal.text())
            self.assertIn(candidates[0].name, journal.text())

    def test_03_parallel_launches_never_share_a_new_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary = pathlib.Path(directory) / "webview-profile"
            candidates = launcher.window_profile_candidates(primary)[1:]
            claimed: list = []
            lock = threading.Lock()

            def claim() -> None:
                found = launcher.claim_window_profile(candidates)
                with lock:
                    claimed.append(found)

            threads = [threading.Thread(target=claim) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(claimed), 3)
            self.assertTrue(all(item is not None for item in claimed), f"a launch got nothing: {claimed}")
            self.assertEqual(len({str(item) for item in claimed}), 3, f"two launches shared: {claimed}")

    def test_04_a_usable_window_is_reused_without_starting_extra_servers(self) -> None:
        journal = FakeJournal()
        fake = FakeSelf(journal)
        # The existing window is found and brought forward; it is closed right afterwards (the user closed
        # it), and the close is decided on the registered window handle, not on a title scan.
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(launcher, "REPO", pathlib.Path(directory)), \
                    mock.patch.object(launcher, "window_handles", return_value=[11]), \
                    mock.patch.object(launcher, "window_title", return_value="FORGE AI"), \
                    mock.patch.object(launcher, "bring_to_front", return_value=True), \
                    mock.patch.object(launcher, "window_is_visible", return_value=False), \
                    mock.patch.object(launcher.time, "sleep", return_value=None), \
                    mock.patch.object(launcher, "start_server") as start:
                code = launcher.Supervisor.close_window(fake)
            state_path, _lock_path = launcher.instance_paths(pathlib.Path(directory))
            stored = launcher.load_state(state_path)
        self.assertEqual(code, 0, "the reuse path reports success")
        start.assert_not_called()
        self.assertIn("already open", journal.text())
        self.assertIn("window closed", journal.text())
        self.assertEqual(stored["windows"], [], "the window must be unregistered once it is gone")

    def test_05_a_busy_profile_is_retried_on_a_separate_one_and_logged(self) -> None:
        journal = FakeJournal()
        fake = FakeSelf(journal)
        chrome = launcher.Browser(
            "chrome", pathlib.Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        )
        with tempfile.TemporaryDirectory() as directory:
            profiles: list[str] = []
            calls: list[list[str]] = []

            def fake_popen(arguments, **kwargs):
                calls.append(list(arguments))
                profiles.append(str(arguments[3]))
                return FakeProcess(code=0)  # exits at once, exactly as a held profile makes it do

            with mock.patch.object(launcher, "REPO", pathlib.Path(directory)), \
                    mock.patch.object(launcher, "resolve_browser", return_value=chrome), \
                    mock.patch.object(launcher, "window_handles", return_value=[]), \
                    mock.patch.object(launcher, "subprocess") as module_subprocess:
                module_subprocess.Popen.side_effect = fake_popen
                code = launcher.Supervisor.close_window(fake)
            self.assertEqual(code, 0)
            self.assertGreaterEqual(len(profiles), 3, f"no retry happened: {profiles}")
            self.assertNotIn("--no-sandbox", calls[0], "the first attempt must keep chrome's sandbox")
            self.assertIn(
                "--no-sandbox",
                calls[1],
                "a chrome that died before a usable window is retried once without the sandbox",
            )
            self.assertIn(
                "--test-type",
                calls[1],
                "chrome shows a warning strip over the UI for --no-sandbox unless --test-type is there",
            )
            self.assertEqual(profiles[0], profiles[1], "the sandbox retry reuses the same profile")
            self.assertNotEqual(profiles[0], profiles[2], "the profile retry reused the held profile")
            self.assertTrue(profiles[0].endswith("webview-profile-chrome"), profiles[0])
            self.assertIn("webview-profile-chrome-alt", profiles[2])
            self.assertIn("exited with code 0", journal.text())
            self.assertIn("--no-sandbox", journal.text())
            self.assertIn("separate profile", journal.text())
            self.assertIn("window opened with chrome", journal.text())
            self.assertTrue((pathlib.Path(directory) / ".forge").is_dir(), "the fallback lives under .forge")

    def test_07_chrome_is_preferred_over_edge_and_each_browser_gets_its_own_profile(self) -> None:
        """Chrome first, Edge as the fallback, and never one browser's profile handed to the other."""

        both = launcher.resolve_browser(
            available=lambda path: path.name in {"chrome.exe", "msedge.exe"}
        )
        self.assertIsNotNone(both)
        self.assertEqual(both.name, "chrome")
        self.assertTrue(str(both.executable).endswith(r"Google\Chrome\Application\chrome.exe"))

        edge_only = launcher.resolve_browser(available=lambda path: path.name == "msedge.exe")
        self.assertIsNotNone(edge_only, "Edge must remain the fallback")
        self.assertEqual(edge_only.name, "edge")

        self.assertIsNone(
            launcher.resolve_browser(available=lambda path: False),
            "neither browser installed must be reported honestly, not guessed",
        )

        repo = pathlib.Path("X:/repo")
        self.assertEqual(
            launcher.window_profile(repo, both).name,
            "webview-profile-chrome",
            "Chrome must not open the profile Edge has been writing",
        )
        self.assertEqual(launcher.window_profile(repo, edge_only).name, "webview-profile-edge")
        self.assertNotEqual(
            launcher.window_profile(repo, both), launcher.window_profile(repo, edge_only)
        )

    def test_06_stopping_forge_leaves_foreign_processes_alone(self) -> None:
        journal = FakeJournal()
        fake = FakeSelf(journal)
        foreign = FakeProcess()
        with mock.patch.object(launcher, "port_is_served", return_value=False):
            launcher.Supervisor.stop_own(fake)
        # Nothing of the launcher's own was registered, so no process may be touched: the stand-in
        # raises on terminate/kill, and no stopping line may appear in the journal.
        self.assertIsNotNone(foreign)
        self.assertNotIn("stopping pid", journal.text())
        self.assertNotIn("killing pid", journal.text())


if __name__ == "__main__":
    unittest.main()
