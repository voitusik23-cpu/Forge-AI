"""The live progress model: which step is done, how long it has been going, is the ribbon moving.

The owner's complaint was not «I need a percentage»: it was «I cannot tell whether it works or
hangs». The fix is a state model, and this file proves the rules of that model by executing the
**production** module ``ui/src/progress.ts`` with Node (type stripping, exactly like
``tests/test_clarification_loop.py`` executes ``ui/src/forge-result.ts``). Nothing is
re-implemented in Python, so the assertions cannot drift from the code the application runs.

What is asserted
---------------
* **Steps.** ``done`` / ``current`` / ``pending`` / ``error``, derived from the discovery
  session's own ``CONFIRMED`` state and the run record: an unconfirmed step is never green, a
  run in flight makes step 4 ``current``, a terminal-success record makes it ``done`` and a
  terminal failure makes it ``error`` (red). A retry dispatched after a failed run must read
  ``current`` again, not keep the old verdict.
* **The clock.** ``MM:SS`` (``H:MM:SS`` past an hour), floored, never negative, ``00:00`` when
  nothing real is known. There is no total and no ETA anywhere, because the backend publishes
  neither.
* **The ribbon.** Moving only while a run is really in flight; stopped on every terminal record,
  on success and on failure alike.
* **The wiring and the reduced-motion path**, read from the shipped sources: the bar under the
  rail, the same bar in the change block, a ``aria-live="polite"`` region, the hint that names
  the truth (the run is server-side), and both reduced-motion switches in CSS.

No network, no browser, no model: this file only executes a pure module and reads three files.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import unittest

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
PROGRESS_MODULE = REPOSITORY / "ui" / "src" / "progress.ts"
ALIVE_BAR = REPOSITORY / "ui" / "src" / "components" / "AliveBar.tsx"
FORGE_TSX = REPOSITORY / "ui" / "src" / "screens" / "Forge.tsx"
PROJECTS_TSX = REPOSITORY / "ui" / "src" / "screens" / "Projects.tsx"
COMPONENTS_CSS = REPOSITORY / "ui" / "src" / "design" / "components.css"
I18N = REPOSITORY / "ui" / "src" / "i18n.ts"

#: The press that started a run, in epoch milliseconds. Fixed so the arithmetic is exact.
PRESS = 1_760_000_000_000


def _node(script: str) -> str:
    """Run one ES module script with Node, or skip when Node is not installed."""

    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("node is not installed; the progress model cannot be executed")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout


def _facts(payload: dict) -> dict:
    """Serialize one ``StepFacts`` object into the script, keeping the keys explicit."""

    return {
        "phase": payload["phase"],
        "confirmed": payload["confirmed"],
        "inFlight": payload["inFlight"],
        "terminal": payload["terminal"],
        "success": payload["success"],
    }


class ProgressModelTests(unittest.TestCase):
    """The pure rules of `ui/src/progress.ts`, executed from its own source."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = PROGRESS_MODULE.read_text(encoding="utf-8")
        cls.answers = cls._run_model()

    @staticmethod
    def _run_model() -> dict:
        step_cases = {
            "idea": _facts(
                {"phase": "idea", "confirmed": False, "inFlight": False, "terminal": False, "success": False}
            ),
            "clarify": _facts(
                {"phase": "clarify", "confirmed": False, "inFlight": False, "terminal": False, "success": False}
            ),
            "spec": _facts(
                {"phase": "spec", "confirmed": False, "inFlight": False, "terminal": False, "success": False}
            ),
            "confirmed_idle": _facts(
                {"phase": "run", "confirmed": True, "inFlight": False, "terminal": False, "success": False}
            ),
            "in_flight": _facts(
                {"phase": "run", "confirmed": True, "inFlight": True, "terminal": False, "success": False}
            ),
            "succeeded": _facts(
                {"phase": "run", "confirmed": True, "inFlight": False, "terminal": True, "success": True}
            ),
            "failed": _facts(
                {"phase": "run", "confirmed": True, "inFlight": False, "terminal": True, "success": False}
            ),
            "retry_after_failure": _facts(
                {"phase": "run", "confirmed": True, "inFlight": True, "terminal": False, "success": False}
            ),
            "unconfirmed_dispatch": _facts(
                {"phase": "run", "confirmed": False, "inFlight": True, "terminal": False, "success": False}
            ),
        }
        script = f"""
import {{
  aliveStageKey,
  aliveState,
  formatElapsed,
  stampMs,
  stepStates,
}} from {json.dumps(PROGRESS_MODULE.as_uri())};
const stepCases = {json.dumps(step_cases)};
const steps = {{}};
for (const [name, facts] of Object.entries(stepCases)) steps[name] = stepStates(facts);
const seconds = [0, 37, 59, 60, 3599, 3600, 3725, -5, 37.9, NaN];
const press = {PRESS};
const aliveCases = {{
  running_2s: {{ inFlight: true, terminal: false, success: false, observedAt: press, endedAt: 0, now: press + 2000 }},
  running_10s: {{ inFlight: true, terminal: false, success: false, observedAt: press, endedAt: 0, now: press + 10000 }},
  done: {{ inFlight: false, terminal: true, success: true, observedAt: press, endedAt: press + 52000, now: press + 60000 }},
  failed: {{ inFlight: false, terminal: true, success: false, observedAt: press, endedAt: press + 4500, now: press + 9000 }},
  idle: {{ inFlight: false, terminal: false, success: false, observedAt: press, endedAt: 0, now: press + 3000 }},
  terminal_without_record_end: {{ inFlight: false, terminal: true, success: true, observedAt: press, endedAt: 0, now: press + 7000 }},
  end_before_start: {{ inFlight: false, terminal: true, success: true, observedAt: press, endedAt: press - 5000, now: press + 7000 }},
  no_start: {{ inFlight: true, terminal: false, success: false, observedAt: 0, endedAt: 0, now: press }},
}};
const alive = {{}};
for (const [name, facts] of Object.entries(aliveCases)) alive[name] = aliveState(facts);
const stamps = [
  "2026-10-06T00:00:00+00:00",
  "2026-10-06T00:00:00Z",
  "",
  null,
  "not a stamp",
].map((value) => (stampMs(value) > 0));
const stages = [
  aliveStageKey(0, false),
  aliveStageKey(0, true),
  aliveStageKey(3, true),
  aliveStageKey(4, true),
  aliveStageKey(36, true),
];
process.stdout.write(JSON.stringify({{
  steps,
  elapsed: seconds.map((value) => formatElapsed(value)),
  alive,
  stamps,
  stages,
}}));
"""
        return json.loads(_node(script))

    # ---------------------------------------------------------------- the four steps
    def test_01_a_fresh_form_is_on_the_first_step(self) -> None:
        self.assertEqual(
            self.answers["steps"]["idea"],
            {"idea": "current", "clarify": "pending", "spec": "pending", "run": "pending"},
        )

    def test_02_each_passed_step_is_done_and_the_next_one_is_current(self) -> None:
        self.assertEqual(
            self.answers["steps"]["clarify"],
            {"idea": "done", "clarify": "current", "spec": "pending", "run": "pending"},
        )
        self.assertEqual(
            self.answers["steps"]["spec"],
            {"idea": "done", "clarify": "done", "spec": "current", "run": "pending"},
        )

    def test_03_a_confirmed_session_makes_steps_one_to_three_done(self) -> None:
        # The Core's own CONFIRMED state is the only fact that proves the specification exists.
        self.assertEqual(
            self.answers["steps"]["confirmed_idle"],
            {"idea": "done", "clarify": "done", "spec": "done", "run": "current"},
        )

    def test_04_an_unconfirmed_session_never_marks_a_step_it_has_not_reached(self) -> None:
        # Before CONFIRMED the only proof a step exists is that the owner already walked it;
        # the specification and the launch are never green ahead of their own facts.
        clarify = self.answers["steps"]["clarify"]
        self.assertEqual(clarify["spec"], "pending")
        self.assertEqual(clarify["run"], "pending")
        spec = self.answers["steps"]["spec"]
        self.assertEqual(spec["run"], "pending")
        # And the dispatch alone (phase `run`, no CONFIRMED) keeps the last step `current` —
        # never `done`: a run in flight is not an outcome.
        unconfirmed = self.answers["steps"]["unconfirmed_dispatch"]
        self.assertEqual(unconfirmed["run"], "current")
        self.assertNotIn("done", (unconfirmed["run"],))

    def test_05_a_run_in_flight_keeps_the_last_step_current(self) -> None:
        self.assertEqual(
            self.answers["steps"]["in_flight"],
            {"idea": "done", "clarify": "done", "spec": "done", "run": "current"},
        )

    def test_06_a_terminal_record_decides_the_last_step(self) -> None:
        self.assertEqual(
            self.answers["steps"]["succeeded"],
            {"idea": "done", "clarify": "done", "spec": "done", "run": "done"},
        )
        # A failed run is red, never a green tick over something that did not happen.
        self.assertEqual(
            self.answers["steps"]["failed"],
            {"idea": "done", "clarify": "done", "spec": "done", "run": "error"},
        )

    def test_07_a_retry_returns_the_last_step_to_current(self) -> None:
        self.assertEqual(
            self.answers["steps"]["retry_after_failure"],
            {"idea": "done", "clarify": "done", "spec": "done", "run": "current"},
        )

    def test_08_every_state_is_one_of_the_four_words(self) -> None:
        for name, states in self.answers["steps"].items():
            with self.subTest(case=name):
                self.assertEqual(set(states), {"idea", "clarify", "spec", "run"})
                for state in states.values():
                    self.assertIn(state, {"done", "current", "pending", "error"})

    # -------------------------------------------------------------------- the clock
    def test_09_elapsed_is_mm_ss_floored_and_never_negative(self) -> None:
        self.assertEqual(
            self.answers["elapsed"],
            ["00:00", "00:37", "00:59", "01:00", "59:59", "1:00:00", "1:02:05", "00:00", "00:37", "00:00"],
        )

    # ------------------------------------------------------------------ the ribbon
    def test_10_the_ribbon_moves_only_while_the_run_is_in_flight(self) -> None:
        running = self.answers["alive"]["running_2s"]
        self.assertTrue(running["visible"])
        self.assertTrue(running["ribbon"])
        self.assertEqual(running["phase"], "running")
        self.assertEqual(running["clock"], "00:02")
        # ... and the clock is a real tick, not a fixed number.
        self.assertEqual(self.answers["alive"]["running_10s"]["clock"], "00:10")
        self.assertEqual(self.answers["alive"]["running_10s"]["ribbon"], True)

    def test_11_a_terminal_run_stops_the_ribbon_and_prints_its_own_duration(self) -> None:
        done = self.answers["alive"]["done"]
        self.assertFalse(done["ribbon"])
        self.assertEqual(done["phase"], "done")
        # The record's own end (press + 52 s) wins over the later local "now".
        self.assertEqual(done["seconds"], 52)
        self.assertEqual(done["clock"], "00:52")

    def test_12_a_failed_run_stops_the_ribbon_and_says_so(self) -> None:
        failed = self.answers["alive"]["failed"]
        self.assertFalse(failed["ribbon"])
        self.assertEqual(failed["phase"], "failed")
        self.assertEqual(failed["clock"], "00:04")

    def test_13_nothing_is_shown_when_nothing_is_in_flight_or_finished(self) -> None:
        idle = self.answers["alive"]["idle"]
        self.assertFalse(idle["visible"])
        self.assertFalse(idle["ribbon"])

    def test_14_an_unknown_or_inverted_start_is_zero_not_an_invented_number(self) -> None:
        # A terminal record without its own timestamp falls back to the frozen local end ...
        self.assertEqual(self.answers["alive"]["terminal_without_record_end"]["clock"], "00:07")
        # ... and an end before the start is clamped, never printed as a negative clock.
        self.assertEqual(self.answers["alive"]["end_before_start"]["clock"], "00:00")
        # With no press at all there is no elapsed claim.
        self.assertEqual(self.answers["alive"]["no_start"]["clock"], "00:00")

    # ------------------------------------------------------- the record's timestamps
    def test_15_only_a_real_timestamp_is_read_from_the_record(self) -> None:
        self.assertEqual(self.answers["stamps"], [True, True, False, False, False])

    # ---------------------------------------------------------------------- the stage
    def test_16_the_stage_word_follows_the_record_that_proves_it(self) -> None:
        self.assertEqual(
            self.answers["stages"],
            [
                "forge.alive.stage.dispatching",
                "forge.alive.stage.starting",
                "forge.alive.stage.writing",
                "forge.alive.stage.checking",
                "forge.alive.stage.checking",
            ],
        )


class ProgressWiringTests(unittest.TestCase):
    """The shipped sources: the bar under the rail, the same bar in the change block, motion."""

    def test_01_the_alive_bar_is_accessible_and_names_the_server_side_truth(self) -> None:
        source = ALIVE_BAR.read_text(encoding="utf-8")
        self.assertIn('aria-live="polite"', source)
        self.assertIn('role="status"', source)
        self.assertIn("forge.alive.hint", source)
        self.assertIn("data-ribbon", source)
        self.assertIn("aliveState", source)
        # The clock is hidden from screen readers while it runs: a number every second is noise.
        self.assertIn("aria-hidden", source)

    def test_02_the_rail_renders_the_bar_under_it(self) -> None:
        source = FORGE_TSX.read_text(encoding="utf-8")
        self.assertIn("<StepRail lang={lang} states={steps} />", source)
        self.assertIn("stepStates({", source)
        self.assertIn("aliveStageKey(", source)
        # The bar sits directly after the rail, and it is the same component the change block uses.
        self.assertLess(source.index("<StepRail"), source.index("<AliveBar"))

    def test_03_the_change_block_shows_the_same_bar(self) -> None:
        source = PROJECTS_TSX.read_text(encoding="utf-8")
        self.assertIn("<AliveBar", source)
        self.assertIn('testId="change-alive"', source)
        self.assertIn("changeStartedAt", source)
        self.assertIn("changeEndedAt", source)
        self.assertIn("projects.change.working", source)

    def test_04_reduced_motion_stops_the_ribbon_but_keeps_the_text(self) -> None:
        source = COMPONENTS_CSS.read_text(encoding="utf-8")
        self.assertIn("@keyframes forge-alive-ribbon", source)
        self.assertIn("@keyframes forge-step-ribbon", source)
        # Both switches the codebase uses: the OS setting and this interface's own toggle.
        reduced = source[source.index("@media (prefers-reduced-motion: reduce)") :]
        self.assertIn(".forge-alive-ribbon", reduced)
        self.assertIn(".forge-step.current::after", reduced)
        self.assertIn(':root[data-motion="reduced"] .forge-alive-ribbon', source)
        self.assertIn(':root[data-motion="reduced"] .forge-step.current::after', source)
        # The terminal bar is still: a full-width bar, not a frozen sliver off to the left.
        self.assertIn(".forge-alive.done .forge-alive-ribbon", source)
        self.assertIn("animation: none", source)

    def test_05_the_words_exist_in_both_languages(self) -> None:
        source = I18N.read_text(encoding="utf-8")
        for key in (
            "forge.alive.status.running",
            "forge.alive.plan",
            "forge.alive.stage",
            "forge.alive.done",
            "forge.alive.failed",
            "forge.alive.hint",
            "forge.alive.stage.dispatching",
            "forge.alive.stage.starting",
            "forge.alive.stage.writing",
            "forge.alive.stage.checking",
        ):
            with self.subTest(key=key):
                self.assertEqual(source.count(f'"{key}"'), 2, f"{key} must exist in ru and en")


if __name__ == "__main__":
    unittest.main()
