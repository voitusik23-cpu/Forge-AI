"""Tests for the Forge Pulse desktop widget.

Two layers are covered:

* the pure view model -- what the card is allowed to claim, including the
  difference between "nothing has run yet" and "everything failed";
* the rendered card -- it is built, it draws, and an absent core degrades the
  display instead of inventing numbers.
"""

from __future__ import annotations

import unittest

from app.desktop.widget import (
    ForgePulseWidget,
    WidgetOptions,
    WidgetSnapshot,
    build_snapshot,
    format_cost,
    format_tokens,
    ring_extent,
    success_ratio,
)


class _Totals:
    total_tokens = 12_345
    effective_cost = 1.5
    total_runs = 10
    success_count = 8
    failure_count = 2


class _Account:
    balance_display = "$5.00"
    alert = "healthy"
    status = "active"


class _Provider:
    provider = "openai"
    account_status = _Account()
    total_tokens = 12_345


class _Run:
    total_tokens = 100


class _Report:
    providers = (_Provider(),)
    runs = (_Run(), _Run(), _Run())
    all_time_totals = _Totals()


class _Health:
    core_status = "connected"


class _HealthyClient:
    def check_health(self) -> _Health:
        return _Health()

    def get_dashboard(self) -> _Report:
        return _Report()


class _NoCoreClient:
    def check_health(self) -> object:
        raise ConnectionError("core is not running")

    def get_dashboard(self) -> object:  # pragma: no cover - never reached
        raise AssertionError("dashboard must not be read when the core is absent")


class _NoTelemetryClient:
    def check_health(self) -> _Health:
        return _Health()

    def get_dashboard(self) -> object:
        raise RuntimeError("dashboard unavailable")


class FormattingTests(unittest.TestCase):
    def test_01_tokens_are_compact(self) -> None:
        self.assertEqual(format_tokens(950), "950")
        self.assertEqual(format_tokens(12_345), "12.3k")
        self.assertEqual(format_tokens(3_100_000), "3.1M")
        self.assertEqual(format_tokens("nonsense"), "0")

    def test_02_cost_never_invents_a_value(self) -> None:
        self.assertEqual(format_cost(0), "$0.00")
        self.assertEqual(format_cost(0.004), "<$0.01")
        self.assertEqual(format_cost(12.5), "$12.50")
        self.assertEqual(format_cost(None), "$0.00")

    def test_03_an_empty_history_is_not_a_zero_success_rate(self) -> None:
        """0 runs and 0% success are different facts."""

        self.assertIsNone(success_ratio(0, 0))
        self.assertEqual(success_ratio(0, 4), 0.0)
        self.assertEqual(success_ratio(3, 1), 0.75)
        self.assertIsNone(success_ratio("x", "y"))

    def test_04_the_gauge_draws_nothing_for_an_unknown_rate(self) -> None:
        self.assertEqual(ring_extent(None), 0.0)
        self.assertEqual(ring_extent(0.0), 0.0)
        self.assertAlmostEqual(ring_extent(1.0), 359.9, places=1)
        self.assertGreater(ring_extent(2.0), 0.0)  # clamped, not extrapolated


class SnapshotTests(unittest.TestCase):
    def test_05_a_healthy_core_produces_real_numbers(self) -> None:
        snapshot = build_snapshot(_HealthyClient())

        self.assertTrue(snapshot.core_connected)
        self.assertEqual(snapshot.runs_total, 10)
        self.assertAlmostEqual(snapshot.success_rate or 0.0, 0.8, places=4)
        self.assertEqual(snapshot.total_tokens, 12_345)
        self.assertAlmostEqual(snapshot.total_cost, 1.5, places=4)
        self.assertEqual(len(snapshot.activity), 3)
        self.assertEqual(snapshot.providers[0].name, "openai")
        self.assertEqual(snapshot.providers[0].tone, "ok")
        self.assertEqual(snapshot.degraded_reason, "")

    def test_06_an_absent_core_is_reported_and_nothing_is_invented(self) -> None:
        snapshot = build_snapshot(_NoCoreClient())

        self.assertFalse(snapshot.core_connected)
        self.assertEqual(snapshot.runs_total, 0)
        self.assertIsNone(snapshot.success_rate)
        self.assertEqual(snapshot.providers, ())
        self.assertTrue(snapshot.degraded_reason)

    def test_07_missing_telemetry_is_distinct_from_a_missing_core(self) -> None:
        snapshot = build_snapshot(_NoTelemetryClient())

        self.assertTrue(snapshot.core_connected)
        self.assertIsNone(snapshot.success_rate)
        self.assertEqual(snapshot.total_tokens, 0)


class RenderedCardTests(unittest.TestCase):
    """The card is really built and really drawn; skipped only with no display."""

    def setUp(self) -> None:
        try:
            probe = ForgePulseWidget(_HealthyClient(), options=WidgetOptions())
            root = probe.initialize()
        except Exception as exc:  # noqa: BLE001 - a headless host cannot draw
            self.skipTest(f"no display available for tkinter: {exc}")
        root.withdraw()
        self.widget = probe

    def tearDown(self) -> None:
        widget = getattr(self, "widget", None)
        if widget is not None:
            widget.close()

    def test_08_the_card_draws_its_content(self) -> None:
        snapshot = self.widget.refresh()
        canvas = self.widget._canvas

        self.assertIsInstance(snapshot, WidgetSnapshot)
        self.assertIsNotNone(canvas)
        items = canvas.find_all()
        self.assertGreater(len(items), 25, "the card did not draw its content")
        texts = [canvas.itemcget(item, "text") for item in items
                 if canvas.type(item) == "text"]
        self.assertIn("FORGE", texts)
        self.assertIn("80%", texts)
        self.assertIn("10", texts)

    def test_09_an_absent_core_still_draws_and_says_so(self) -> None:
        self.widget._client = _NoCoreClient()
        self.widget.refresh()
        canvas = self.widget._canvas
        texts = [canvas.itemcget(item, "text") for item in canvas.find_all()
                 if canvas.type(item) == "text"]

        self.assertIn("offline", texts)
        self.assertIn("—", texts, "an unknown success rate must not be drawn as 0%")
        self.assertTrue(any("недоступен" in text for text in texts))

    def test_10_dragging_moves_the_window(self) -> None:
        self.widget.refresh()
        root = self.widget.root
        self.assertIsNotNone(root)
        start_x, start_y = root.winfo_x(), root.winfo_y()

        class _Event:
            def __init__(self, x_root: int, y_root: int) -> None:
                self.x_root = x_root
                self.y_root = y_root

        self.widget._on_press(_Event(500, 500))
        self.widget._on_drag(_Event(530, 520))
        root.update_idletasks()

        self.assertNotEqual((root.winfo_x(), root.winfo_y()), (start_x, start_y))

    def test_11_a_double_click_uses_the_composed_callback(self) -> None:
        calls: list[str] = []
        self.widget.options.on_open_console = lambda: calls.append("console")
        self.widget._on_double_click(None)
        self.assertEqual(calls, ["console"])


if __name__ == "__main__":
    unittest.main()
