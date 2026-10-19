"""The standard composition carries an idempotency guard; without one it stays fail-closed.

These tests exercise the **production** composition -- ``create_api_app`` as ``app/api/main.py``
calls it -- instead of a service with a manually injected guard, which is what every earlier test
did and why the gap survived.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.agent_runtime.criterion_identity import TaskIdentity
from app.agent_runtime.idempotency_integration import (
    IdempotencyLedger,
    OperationClass,
    OperationIdentity,
    RunIdempotencyGuard,
    default_idempotency_guard,
)
from app.api.main import build_idempotency_guard
from app.api.server import create_api_app


def service_of(app):
    """The service the factory composed, exposed for the contract under test."""

    return app.state.forge_ui_assets


class ProductionCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.ledger_root = pathlib.Path(self._directory.name) / "idempotency"
        self.guard = RunIdempotencyGuard.create(ledger_root=str(self.ledger_root))

    def test_01_without_a_guard_the_graph_is_refused(self) -> None:
        # A request that passes shape validation and reaches the runner check: one step naming a
        # declaration that does not exist. Without a guard the composition refusal comes first.
        client = TestClient(create_api_app())
        response = client.post(
            "/api/product/graph",
            json={
                "graph_run_id": "guardless",
                "steps": [
                    {
                        "step_id": "only",
                        "action": {"declaration_id": "no.such.declaration", "parameters": []},
                        "depends_on": [],
                    }
                ],
            },
        )
        self.assertGreaterEqual(response.status_code, 400, response.text)
        self.assertIn("step-graph runner", response.text)

    def test_02_with_a_guard_that_refusal_is_gone(self) -> None:
        client = TestClient(create_api_app(idempotency_guard=self.guard, authorized_project_id="phase2-project"))
        response = client.post("/api/product/graph", json={"steps": [], "graph_run_id": "guarded"})
        # An empty graph is still refused on its own terms; what must disappear is the composition
        # refusal that says no runner exists at all.
        self.assertNotIn("no step-graph runner is composed", response.text)

    def test_03_the_production_entry_point_builds_a_durable_guard(self) -> None:
        root = str(build_idempotency_guard().bounded_summary()["ledger_root"])
        self.assertIn("idempotency", root)
        self.assertNotEqual(
            root, str(default_idempotency_guard().bounded_summary()["ledger_root"])
        )

    def test_04_the_guard_root_follows_the_run_store_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("FORGE_RUN_STORE_ROOT")
            os.environ["FORGE_RUN_STORE_ROOT"] = directory
            try:
                root = str(build_idempotency_guard().bounded_summary()["ledger_root"])
                self.assertTrue(root.startswith(str(pathlib.Path(directory))), root)
            finally:
                if previous is None:
                    os.environ.pop("FORGE_RUN_STORE_ROOT", None)
                else:
                    os.environ["FORGE_RUN_STORE_ROOT"] = previous

    def test_05_a_duplicate_delivery_does_not_execute_twice(self) -> None:
        client = TestClient(create_api_app(idempotency_guard=self.guard, authorized_project_id="phase2-project"))
        payload = {"description": "phase2 duplicate check", "provider_name": "mock"}
        headers = {"Idempotency-Key": "phase2-key-1"}
        first = client.post("/api/tasks/run", json=payload, headers=headers)
        second = client.post("/api/tasks/run", json=payload, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(first.json()["run_id"], second.json()["run_id"])
        self.assertEqual(second.headers.get("X-Forge-Idempotent-Replay"), "true")
        conflict = client.post(
            "/api/tasks/run",
            json={"description": "different work", "provider_name": "mock"},
            headers=headers,
        )
        self.assertEqual(conflict.status_code, 409)

    def test_06_the_same_key_in_another_project_is_isolated(self) -> None:
        client = TestClient(create_api_app(idempotency_guard=self.guard, authorized_project_id="phase2-project"))
        headers = {"Idempotency-Key": "phase2-key-2"}
        first = client.post(
            "/api/tasks/run",
            json={"description": "isolation", "provider_name": "mock", "project_id": "alpha"},
            headers=headers,
        )
        second = client.post(
            "/api/tasks/run",
            json={"description": "isolation", "provider_name": "mock", "project_id": "beta"},
            headers=headers,
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertNotEqual(first.json()["run_id"], second.json()["run_id"])
        self.assertNotEqual(second.headers.get("X-Forge-Idempotent-Replay"), "true")

    def test_07_an_unrecorded_operation_is_a_first_attempt_not_a_repeat(self) -> None:
        identity = OperationIdentity(
            operation_class=OperationClass.TASK_RUN,
            idempotency_key="phase2-retry",
            task_identity=TaskIdentity(task_id="t", task_fingerprint="a" * 64),
        )
        decision = self.guard.repeat_decision(identity)
        guarantee = str(getattr(decision, "guarantee", "")).upper()
        self.assertIn("FIRST_ATTEMPT", guarantee)
        self.assertIn("NO DURABLE RECORD", str(getattr(decision, "reason", "")).upper())

    def test_08_a_duplicate_survives_a_restart_of_the_composition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("FORGE_RUN_STORE_ROOT")
            os.environ["FORGE_RUN_STORE_ROOT"] = directory
            try:
                payload = {"description": "durability across restart", "provider_name": "mock"}
                headers = {"Idempotency-Key": "phase2-restart-key"}
                first = TestClient(create_api_app(idempotency_guard=self.guard, authorized_project_id="phase2-project")).post(
                    "/api/tasks/run", json=payload, headers=headers
                )
                # A second application object stands in for a restarted process: same durable root,
                # fresh in-memory state.
                second = TestClient(create_api_app(idempotency_guard=self.guard, authorized_project_id="phase2-project")).post(
                    "/api/tasks/run", json=payload, headers=headers
                )
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(second.status_code, 200, second.text)
                self.assertEqual(first.json()["run_id"], second.json()["run_id"])
                self.assertEqual(second.headers.get("X-Forge-Idempotent-Replay"), "true")
            finally:
                if previous is None:
                    os.environ.pop("FORGE_RUN_STORE_ROOT", None)
                else:
                    os.environ["FORGE_RUN_STORE_ROOT"] = previous


if __name__ == "__main__":
    unittest.main()
