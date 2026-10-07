"""Tests for Forge Desktop Shell («Кузница»)."""

from __future__ import annotations

import unittest

from app.api.client import ForgeApiClient
from app.api.service import ForgeApiService
from app.desktop.app import ForgeDesktopApp
from app.desktop.views import (
    AgentsView,
    CostsView,
    HomeView,
    ProjectsView,
    RunsView,
    SettingsView,
)


class DesktopShellTests(unittest.TestCase):
    """Verify Desktop Shell window creation, view navigation, and task dispatch."""

    def setUp(self) -> None:
        self.service = ForgeApiService()
        self.client = ForgeApiClient(api_service=self.service)
        self.app = ForgeDesktopApp(client=self.client, headless=True)
        self.app.initialize()

    def tearDown(self) -> None:
        self.app.shutdown()

    def test_01_app_initialization_and_title(self) -> None:
        self.assertTrue(self.app.is_running)
        self.assertIsNotNone(self.app.root)
        self.assertEqual(self.app.root.title(), "FORGE — «Кузница»")

    def test_02_navigation_view_switching(self) -> None:
        views = ["forge", "projects", "agents", "runs", "costs", "settings"]
        for v in views:
            self.app.show_view(v)
            self.assertEqual(self.app.current_view_name, v)

    def test_03_home_view_status_refresh(self) -> None:
        self.app.show_view("forge")
        home_view = self.app._views["forge"]
        self.assertIsInstance(home_view, HomeView)
        home_view.refresh()
        self.assertIn("CONNECTED", home_view.lbl_core_status.cget("text"))
        self.assertIn("CONNECTED", home_view.lbl_api_status.cget("text"))

    def test_04_home_view_task_dispatch(self) -> None:
        self.app.show_view("forge")
        home_view = self.app._views["forge"]
        self.assertIsInstance(home_view, HomeView)

        # Set task prompt and invoke run
        home_view.txt_task.delete(0, "end")
        home_view.txt_task.insert(0, "Execute quick UI task")
        home_view._on_run_task()

        output_text = home_view.txt_output.get("1.0", "end")
        self.assertIn("Result: SUCCESS", output_text)
        self.assertIn("Run ID: run-api-", output_text)

    def test_05_costs_view_dashboard_render(self) -> None:
        self.app.show_view("costs")
        costs_view = self.app._views["costs"]
        self.assertIsInstance(costs_view, CostsView)
        costs_view.refresh()

        items = costs_view.tree.get_children()
        self.assertTrue(len(items) >= 1)

    def test_06_connect_api_lifecycle(self) -> None:
        connected = self.app.connect_api()
        self.assertTrue(connected)
        self.assertIn("CONNECTED", self.app.lbl_sidebar_status.cget("text"))


if __name__ == "__main__":
    unittest.main()
