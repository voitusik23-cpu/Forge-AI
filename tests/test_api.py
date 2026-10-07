"""Tests for Forge API layer."""

from __future__ import annotations

import unittest

from app.api.client import ForgeApiClient
from app.api.models import TaskRunRequest
from app.api.server import create_api_app
from app.api.service import ForgeApiService


class ApiTests(unittest.TestCase):
    """Verify Forge API endpoints, client abstraction, and security."""

    def setUp(self) -> None:
        self.service = ForgeApiService()
        self.client = ForgeApiClient(api_service=self.service)

    def test_01_health_endpoint(self) -> None:
        health = self.client.check_health()
        self.assertEqual(health.status, "ok")
        self.assertEqual(health.core_status, "connected")
        self.assertEqual(health.version, "0.1.0")
        self.assertTrue(len(health.timestamp) > 0)

    def test_02_system_status(self) -> None:
        status = self.client.get_system_status()
        self.assertEqual(status.core, "connected")
        self.assertEqual(status.api, "connected")
        self.assertIn(status.provider_status, ("READY", "DEGRADED"))
        self.assertTrue(status.providers_total >= 1)

    def test_03_projects_listing(self) -> None:
        projects = self.client.list_projects()
        self.assertTrue(len(projects) >= 1)
        self.assertEqual(projects[0].project_id, "default")
        self.assertEqual(projects[0].status, "active")

    def test_04_agents_listing(self) -> None:
        agents = self.client.list_agents()
        self.assertTrue(len(agents) >= 1)
        agent_names = [a.name for a in agents]
        self.assertTrue(any("mock" in name or "deepseek" in name or "google" in name for name in agent_names))

    def test_05_dashboard_report_via_api(self) -> None:
        report = self.client.get_dashboard()
        self.assertTrue(len(report.providers) >= 1)
        self.assertIsNotNone(report.all_time_totals)

    def test_06_dashboard_refresh(self) -> None:
        res = self.client.refresh_dashboard()
        self.assertIn("refreshed_at", res)
        self.assertTrue(res["providers_checked"] >= 1)

    def test_07_task_run_via_api(self) -> None:
        res = self.client.run_task(
            description="Create a hello world output",
            category="code",
        )
        self.assertTrue(res.run_id.startswith("run-api-"))
        self.assertTrue(res.task_id.startswith("task-"))
        self.assertEqual(res.state, "COMPLETED")
        self.assertTrue(res.success)
        self.assertTrue(len(res.output) > 0)
        self.assertIsNone(res.error)

    def test_08_no_secret_exposure(self) -> None:
        # Check system status
        st_dict = self.client.get_system_status().to_dict()
        st_str = str(st_dict)
        self.assertNotIn("sk-ant-", st_str)
        self.assertNotIn("sk-proj-", st_str)
        self.assertNotIn("sk-or-v1-", st_str)
        self.assertNotIn("AIzaSy", st_str)

        # Check health
        h_dict = self.client.check_health().to_dict()
        h_str = str(h_dict)
        self.assertNotIn("sk-ant-", h_str)
        self.assertNotIn("sk-proj-", h_str)
        self.assertNotIn("sk-or-v1-", h_str)
        self.assertNotIn("AIzaSy", h_str)

        # Check task response
        task_res = self.client.run_task(description="Return only: SAFE").to_dict()
        t_str = str(task_res)
        self.assertNotIn("sk-ant-", t_str)
        self.assertNotIn("sk-proj-", t_str)
        self.assertNotIn("sk-or-v1-", t_str)
        self.assertNotIn("AIzaSy", t_str)

    def test_09_disconnected_client_state(self) -> None:
        client = ForgeApiClient(api_service=None, base_url="http://127.0.0.1:9999")
        health = client.check_health()
        self.assertEqual(health.status, "error")
        self.assertEqual(health.core_status, "disconnected")

        status = client.get_system_status()
        self.assertEqual(status.core, "disconnected")

    def test_10_fastapi_app_construction(self) -> None:
        app = create_api_app(self.service)
        self.assertIsNotNone(app)
        self.assertEqual(app.title, "Forge AI API")


if __name__ == "__main__":
    unittest.main()
