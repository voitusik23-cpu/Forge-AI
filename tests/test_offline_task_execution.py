"""The offline (mock) task path really executes through the deterministic harness.

Before this path existed, ``POST /api/tasks/run`` with ``provider_name: "mock"``
answered with one canned sentence from ``MockProvider.generate`` and recorded five
fixed events. These tests pin the replacement behaviour:

* the submitted task is executed by the canonical ``AgentHarness`` run loop
  (discovery -> planning -> decision -> read-only workspace verification ->
  acceptance -> terminal state), and the returned ``output`` is derived from the
  task text and from what the run really observed;
* the durable run record carries the loop's own events (well beyond five) and a
  terminal classification;
* two different descriptions produce different, description-specific results;
* the path costs nothing and opens no socket, and it never calls the provider's
  ``generate``;
* the named-provider path is untouched: a live provider still dispatches through
  ``RunExecutor`` -> provider chain;
* the fail-closed refusal for a service with no authorized project still holds.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import socket
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.orchestrator.models import Run, RunState, Task, TaskResult
from app.runtime.run_store import RunStore
from offline_runtime import offline_runtime, offline_settings


class _NetworkSpy:
    """Record every attempt to reach the network, failing the test if one happens."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self._originals: dict[str, object] = {}

    def __enter__(self) -> "_NetworkSpy":
        self._originals = {
            "connect": socket.socket.connect,
            "create_connection": socket.create_connection,
            "getaddrinfo": socket.getaddrinfo,
        }
        calls = self.calls

        def connect(sock_self, address):  # noqa: ANN001 - mirror the real signature
            calls.append(("connect", address))
            raise AssertionError(f"offline task path tried to connect to {address!r}")

        def create_connection(address, *args, **kwargs):  # noqa: ANN001
            calls.append(("create_connection", address))
            raise AssertionError(f"offline task path tried to connect to {address!r}")

        def getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
            calls.append(("getaddrinfo", host))
            raise AssertionError(f"offline task path resolved {host!r}")

        socket.socket.connect = connect  # type: ignore[assignment]
        socket.create_connection = create_connection  # type: ignore[assignment]
        socket.getaddrinfo = getaddrinfo  # type: ignore[assignment]
        return self

    def __exit__(self, *exc_info) -> None:  # noqa: ANN002
        socket.socket.connect = self._originals["connect"]  # type: ignore[assignment]
        socket.create_connection = self._originals["create_connection"]  # type: ignore[assignment]
        socket.getaddrinfo = self._originals["getaddrinfo"]  # type: ignore[assignment]


class _StubRunExecutor:
    """A minimal stand-in for the provider-path executor, used to prove dispatch."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        task = kwargs["task"]
        run = Run(task=task, **({"id": kwargs["run_id"]} if kwargs.get("run_id") else {}))
        run.state = RunState.COMPLETED
        run.result = TaskResult(
            task_id=task.id,
            success=True,
            output="provider-path output",
            provider="openrouter",
            model_name="some/model",
        )
        return run


class OfflineTaskExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._base = (
            pathlib.Path(__file__).resolve().parents[1]
            / ".forge"
            / "test-offline-task"
            / f"{os.getpid()}-{id(self)}"
        )
        shutil.rmtree(self._base, ignore_errors=True)
        self.root = self._base / "runs"
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["FORGE_RUN_STORE_ROOT"] = str(self.root)
        self.addCleanup(lambda: shutil.rmtree(self._base, ignore_errors=True))
        # A TEST runtime narrows every live provider away, so nothing outside this
        # process can be reached even by mistake.
        self.service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.root),
            authorized_project_id="offline-task-project",
        )

    def _dispatch(self, description: str, provider_name: str | None = "mock"):
        return self.service.run_task_detailed(
            TaskRunRequest(
                description=description,
                category="code",
                provider_name=provider_name,
            )
        )

    # ------------------------------------------------------------------ 1
    def test_01_a_task_is_executed_by_the_harness_not_the_canned_provider_text(
        self,
    ) -> None:
        outcome = self._dispatch("перечислить содержимое корня репозитория")
        response = outcome.response
        self.assertTrue(response.success, response.error)
        self.assertEqual(response.state, "COMPLETED")
        self.assertNotIn("MockProvider response:", response.output)
        self.assertIn("OFFLINE TASK EXECUTION", response.output)
        self.assertIn("перечислить содержимое корня репозитория", response.output)
        # The report is built from the loop's own record, so it names the stages
        # that really ran and the checks that were really performed.
        self.assertIn("Plan derived from the task text: 1 read-only check(s)", response.output)
        self.assertIn("[list]", response.output)
        self.assertIn("directory_listed", response.output)
        self.assertIn("Acceptance: PASS", response.output)
        self.assertIn("Final state: COMPLETED", response.output)

    # ------------------------------------------------------------------ 2
    def test_02_the_run_record_carries_the_loops_own_events_and_a_terminal_verdict(
        self,
    ) -> None:
        outcome = self._dispatch("перечислить содержимое корня репозитория")
        run_id = outcome.response.run_id
        self.assertTrue(outcome.history_recorded, outcome.history_error)
        record = self.service.get_run_record(run_id)
        self.assertIsNotNone(record)
        # The old path recorded a fixed five events; a real loop records the
        # discovery, planning, decision, verification and acceptance stages.
        self.assertGreater(record["event_count"], 5)
        self.assertEqual(record["state"], "COMPLETED")
        self.assertEqual(record["status"], "COMPLETED")
        classification = record["classification"]
        self.assertTrue(classification["is_terminal"])
        self.assertEqual(classification["outcome"], "TERMINAL_SUCCESS")

        event_types = [event.event_type.name for event in RunStore(root=self.root).read_events(run_id)]
        for required in (
            "HARNESS_STARTED",
            "RUN_STARTED",
            "CRITERION_DEFINED",
            "PLANNING_COMPLETED",
            "VERIFICATION_COMPLETED",
            "ACCEPTANCE_COMPLETED",
            "HARNESS_COMPLETED",
        ):
            self.assertIn(required, event_types, event_types)
        self.assertGreaterEqual(len(event_types), 20)
        # A completed run must not carry a failure marker in its own trail: the
        # durable telemetry sink is rooted where the history lives, so a successful
        # offline run records a measurement rather than a write failure.
        self.assertNotIn("HARNESS_FAILED", event_types, event_types)

    # ------------------------------------------------------------------ 3
    def test_03_two_descriptions_produce_different_derived_results(self) -> None:
        listing = self._dispatch("перечислить содержимое корня репозитория").response
        readme = self._dispatch(
            "проверить, что README.md существует и прочитать первые 5 строк"
        ).response
        self.assertTrue(listing.success, listing.error)
        self.assertTrue(readme.success, readme.error)
        self.assertNotEqual(listing.output, readme.output)
        # Each result is specific to its own description: a listing reports a
        # directory listing, a read reports the first lines of that file.
        self.assertIn("directory_listed", listing.output)
        self.assertNotIn("file_read", listing.output)
        self.assertIn("file_read", readme.output)
        self.assertIn("README.md", readme.output)
        self.assertIn("first 5 line(s)", readme.output)
        self.assertNotIn("directory_listed", readme.output)
        self.assertNotEqual(listing.run_id, readme.run_id)

    # ------------------------------------------------------------------ 4
    def test_04_the_offline_path_costs_nothing_and_touches_no_network(self) -> None:
        service = self.service
        with patch(
            "app.agents.providers.mock.MockProvider.generate",
            side_effect=AssertionError("the offline path must not call a provider"),
        ):
            with _NetworkSpy() as spy:
                outcome = service.run_task_detailed(
                    TaskRunRequest(
                        description="read the first 2 lines of README.md",
                        category="code",
                        provider_name="mock",
                    )
                )
        self.assertEqual(spy.calls, [])
        response = outcome.response
        self.assertTrue(response.success, response.error)
        # A measured zero: no provider was called, so nothing was consumed.
        self.assertEqual(response.tokens, 0)
        self.assertEqual(response.cost, 0.0)
        self.assertEqual(outcome.usage_basis, "MOCK")
        self.assertEqual(outcome.cost_basis, "MOCK")
        self.assertEqual(response.provider_used, "mock")
        self.assertGreater(response.duration_seconds, 0.0)

    # ------------------------------------------------------------------ 5
    def test_05_an_unbound_service_still_refuses_to_run_anything(self) -> None:
        unbound = ForgeApiService(run_store=RunStore(root=self.root))
        with self.assertRaises(PermissionError) as caught:
            unbound.run_task_detailed(
                TaskRunRequest(description="anything", provider_name="mock")
            )
        self.assertIn("authorized project", str(caught.exception))
        # The refusal happens before any run exists, so no history was created for it.
        self.assertEqual(
            [item for item in self.root.iterdir() if item.is_dir() and item.name != "idempotency"],
            [],
        )

    # ------------------------------------------------------------------ 6
    def test_06_a_named_external_provider_still_dispatches_through_the_provider_path(
        self,
    ) -> None:
        stub = _StubRunExecutor()
        service = ForgeApiService(
            runtime=offline_runtime(),
            run_store=RunStore(root=self.root / "named-provider"),
            authorized_project_id="named-provider-project",
        )
        # The service always composes its own executor over the orchestrator it will
        # dispatch through, so the stand-in is put in place after construction. This
        # is the seam the provider path uses, and the offline path must not touch it.
        service.runtime = replace(service.runtime, run_executor=stub)
        outcome = service.run_task_detailed(
            TaskRunRequest(
                description="перечислить содержимое корня репозитория",
                category="code",
                provider_name="openrouter",
            )
        )
        response = outcome.response
        self.assertEqual(len(stub.calls), 1, "a named provider must go through RunExecutor")
        self.assertEqual(stub.calls[0]["provider_name"], "openrouter")
        self.assertEqual(response.provider_used, "openrouter")
        self.assertEqual(response.model_used, "some/model")
        self.assertEqual(response.output, "provider-path output")
        self.assertNotIn("OFFLINE TASK EXECUTION", response.output)

    # ------------------------------------------------------------------ 7
    def test_07_a_check_that_cannot_be_performed_is_a_failure_not_a_pass(self) -> None:
        outcome = self._dispatch("check that this-file-does-not-exist.txt exists")
        response = outcome.response
        self.assertFalse(response.success)
        self.assertEqual(response.state, "FAILED")
        self.assertIn("expected_file_missing", response.output)
        self.assertIn("Acceptance: FAIL", response.output)
        self.assertIsNotNone(response.error)
        record = self.service.get_run_record(response.run_id)
        self.assertEqual(record["classification"]["outcome"], "TERMINAL_FAILURE")

    # ------------------------------------------------------------------ 8
    def test_08_a_path_outside_the_workspace_is_refused_not_read(self) -> None:
        outcome = self._dispatch("read the first 5 lines of ../../etc/passwd")
        response = outcome.response
        self.assertFalse(response.success)
        self.assertIn("workspace_or_path_rejected", response.output)
        self.assertNotIn("root:", response.output)


if __name__ == "__main__":
    unittest.main()
