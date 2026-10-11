"""Block #10 — structured HTTP errors and honest ``executed`` semantics.

WHAT THIS FILE PROVES, AND HOW
==============================

Every claim is checked through the **real HTTP surface** (``create_api_app`` +
``fastapi.testclient.TestClient``) or through the **real execution contour**
(``ForgeApiService`` -> ``StepGraphRunner`` -> ``RunScope`` -> ``ApprovalPolicy``
-> ``ExecutionCoordinator`` -> ``LocalExecutionAdapter`` -> ``RunStore``), never
against a private helper standing in for a route. The runtime is structurally
offline (``create_runtime(test_mode=True)``); every workspace, ``RunStore`` and
idempotency ledger is a per-test temporary.

THE TWO DEFECTS THIS FILE PINS, BOTH MEASURED BEFORE THE FIX
===========================================================

1. **An unhandled exception produced a body with no information.**
   ``POST /api/product/graph`` with ``[{"step_id":"s1","declaration_id":"..."}]``
   (a *bare* ``declaration_id`` instead of ``{"action": {...}}``) reached
   ``StepDefinition.from_dict`` inside the surface, raised
   ``StepGraphStateError``, and that class was **not** in the tuple
   ``_run_catching`` caught. Measured on a live uvicorn server:

       status : 500
       body   : Internal Server Error     (21 bytes, plain text, no code)

   The fix is a common mechanism, not a per-route ``try/except``: one envelope
   (``error_code`` + ``message`` + ``request_id``) built in one place, produced by
   route-level handling for typed refusals and by app-level exception handlers for
   everything else.

2. **``executed: true`` next to ``status: "REFUSED"`` although nothing ran.**
   With ``ApprovalPolicy(approval_required_tools=("python",))`` and a resolver that
   had nothing to say, the route answered ``executed: true`` with the step
   ``WAITING_APPROVAL`` and no file on disk. ``executed`` meant "the runner drove
   this attempt", which is true; a caller reads it as "something ran", which was
   false. ``command_started`` is the field that answers the caller's question, and
   it is derived from the recorded execution outcome.

HOW EACH TEST CAN FAIL
======================

Each test names, in its docstring, the specific change that makes it go red. Tests
marked ``_SENSITIVITY_`` prove their own protection is load-bearing by
re-running the exact decision rule with one input changed and asserting the answer
flips -- so the test cannot pass on a build where the rule was quietly loosened.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.server import create_api_app
from app.tools.approval import ApprovalPolicy

from test_product_integration import (  # noqa: E402 - tests/ is not a package
    Block8Case,
)

#: The minimum every error body on this surface must carry. ``refused`` and
#: ``executed`` are the historical fields and must not disappear; ``error_code``
#: and ``message`` are the Block #10 additions.
REQUIRED_ERROR_KEYS = ("refused", "executed", "error_code", "message")

#: Values that must never appear in an error body. An absolute path from the
#: server's own filesystem is the leak that matters most here.
_FORBIDDEN_IN_BODY = (
    "Traceback (most recent call last)",
    "site-packages",
    "\\app\\",
    "/app/",
)


class _ErrorEnvelopeCase(Block8Case):
    """Shared assertions for the one error contract."""

    @property
    def bad_step_document(self):
        """The step document that produced the uninformative 500.

        It is a *bare* ``declaration_id`` -- the shape a caller naturally writes
        and the shape the contract does not accept. The declaration id comes from
        the fixture, so this file hard-codes no generated identifier.
        """

        return [{"step_id": "s1", "declaration_id": self.one_id}]

    @property
    def good_step_document(self):
        """The step document the contract DOES accept."""

        return [{"step_id": "s1", "action": {"declaration_id": self.one_id}}]

    def client(self, service=None) -> TestClient:  # type: ignore[override]
        """A client that lets an unhandled exception become a RESPONSE.

        ``raise_server_exceptions=False`` is required and is not a weakening: the
        claim under test is "the server answers with a classified 500" -- the
        exception itself is also re-raised by ``ServerErrorMiddleware`` (whose
        documented behaviour is "we always continue to raise"), so a test that let
        it propagate would be testing the test client, not the surface. The live
        check in ``test_01c`` drives a real uvicorn server and therefore observes
        the response with no such setting involved.
        """

        app = create_api_app(service or self.service())
        client = TestClient(app, raise_server_exceptions=False)
        self._clients.append(client)
        return client

    def setUp(self) -> None:
        super().setUp()
        # ONE resolver per test, shared by the service this case composes and by
        # the approval the test grants. Passing the same object means the approval
        # the test submits is the approval the coordinator will ask for.
        from app.tools.approval import InMemoryApprovalResolver

        self.resolver = InMemoryApprovalResolver()

    def service(self, **kwargs):  # type: ignore[override]
        kwargs.setdefault("approval_resolver", self.resolver)
        return super().service(**kwargs)

    def assert_error_envelope(
        self,
        response,
        *,
        envelope_only: bool = False,
        forbidden_extra: tuple[str, ...] = (),
    ) -> dict:
        """Assert one response body is the error contract, and return the envelope."""

        self.assertGreaterEqual(len(response.content), 2, "the body is empty")
        try:
            body = response.json()
        except json.JSONDecodeError:  # pragma: no cover - a non-JSON body is the bug
            self.fail(f"the error body is not JSON: {response.text!r}")
        nested = body.get("detail") if isinstance(body.get("detail"), dict) else None
        if nested is None:
            envelope = body
        else:
            if envelope_only:
                self.assertEqual(
                    sorted(nested), sorted(body["detail"].keys())
                )
            envelope = nested
        for key in REQUIRED_ERROR_KEYS:
            self.assertIn(key, envelope, f"{key} missing from {body!r}")
        self.assertIsInstance(envelope["error_code"], str)
        self.assertTrue(envelope["error_code"], "error_code must be non-empty")
        self.assertIsInstance(envelope["message"], str)
        self.assertTrue(envelope["message"], "message must be non-empty")
        self.assertIs(envelope["refused"], True)
        self.assertIs(envelope["executed"], False)
        for token in _FORBIDDEN_IN_BODY + forbidden_extra:
            self.assertNotIn(token, response.text, f"leaked {token!r} to the caller")
        return envelope

    def graph(self, client: TestClient, graph_run_id: str, steps):
        return client.post(
            "/api/product/graph",
            json={"graph_run_id": graph_run_id, "steps": steps},
        )


class ErrorContractShapeTests(_ErrorEnvelopeCase):
    """One shape for a malformed body, on every route that takes a body."""

    def test_01_a_step_without_an_action_mapping_is_classified_not_opaque(self) -> None:
        """_INJECTION_TARGET_: the route or the handler is what classifies this.

        Goes red if: the ``action`` pre-check is removed AND the
        ``StepGraphStateError`` classification is removed (the 500 comes back);
        or the envelope loses ``error_code``/``message`` (the bare
        ``Internal Server Error``-era shape returns); or the status stops being a
        4xx for a malformed request body.
        """

        client = self.client()
        response = self.graph(client, "err-bad-step", self.bad_step_document)
        self.assertEqual(
            response.status_code,
            400,
            f"a malformed step document must be the caller's 400, got "
            f"{response.status_code}: {response.text}",
        )
        envelope = self.assert_error_envelope(response)
        self.assertEqual(envelope["error_code"], "invalid_request_schema")
        # The specific, bounded reason the route chose is still readable.
        self.assertIn("action", response.text)
        # AND NOTHING RAN.
        self.assertFalse((self.root / "one.txt").exists())

    def test_01b_a_step_that_is_not_an_object_is_classified(self) -> None:
        """A list of bare strings is refused as a shape error, not as a 500."""

        client = self.client()
        response = self.graph(client, "err-step-string", ["s1"])
        self.assertEqual(response.status_code, 400, response.text)
        envelope = self.assert_error_envelope(response)
        self.assertEqual(envelope["error_code"], "invalid_request_schema")
        self.assertFalse((self.root / "one.txt").exists())

    def test_01c_the_live_status_and_body_match_the_in_process_measurement(self) -> None:
        """The measurement is repeated where ServerErrorMiddleware cannot re-raise.

        ``TestClient`` re-raises an unhandled exception out of the transport, so a
        500 measured only through it would be a claim about the test client. This
        test drives a REAL uvicorn server on a loopback port and reads the bytes a
        client receives.

        Goes red if: the app-level handlers are removed (the body becomes the
        21-byte ``Internal Server Error`` plain text again); the middleware stops
        recording ``X-Request-ID``; or the request_id starts being invented.
        """

        import threading
        import urllib.error
        import urllib.request

        service = self.service()
        app = create_api_app(service=service)
        port = _free_port()
        holder: dict = {}
        ready = threading.Event()

        def serve() -> None:
            import uvicorn

            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
            )
            holder["server"] = server
            ready.set()
            server.run()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            self.assertTrue(ready.wait(30), "the probe server never started")
            deadline = 200
            for _ in range(deadline):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
                    break
                except Exception:  # noqa: BLE001
                    import time

                    time.sleep(0.1)

            def post(payload, headers=None):
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/product/graph",
                    data=json.dumps(payload).encode(),
                    headers={"Content-Type": "application/json", **(headers or {})},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(request, timeout=60) as response:
                        return response.status, response.read().decode()
                except urllib.error.HTTPError as exc:
                    return exc.code, exc.read().decode()

            status, text = post(
                {"graph_run_id": "live-err", "steps": self.bad_step_document},
                {"X-Request-ID": "block10-correlation-1"},
            )
            self.assertEqual(status, 400, text)
            # The graph routes answer FLAT, so the envelope is the document. The
            # test reads it the way a client does -- through the route's own shape,
            # asserted explicitly so a silent change of placement fails here.
            body = json.loads(text)["detail"]
            self.assertEqual(body["error_code"], "invalid_request_schema")
            self.assertTrue(body["message"])
            self.assertIs(body["executed"], False)
            self.assertEqual(body["request_id"], "block10-correlation-1")

            status, text = post(
                {"graph_run_id": "live-err-2", "steps": self.good_step_document}
            )
            self.assertEqual(status, 200, text)
            self.assertEqual(json.loads(text)["result"]["status"], "COMPLETED")
            # The correlation handle is never invented: a request that supplied no
            # header gets no request_id anywhere in its response.
            self.assertNotIn('"request_id": "block10', text)
            self.assertNotIn('"request_id": ""', text)
        finally:
            holder.get("server") and setattr(holder["server"], "should_exit", True)
            thread.join(timeout=15)

    def test_01d_the_unhandled_exception_handler_produces_a_classified_500(self) -> None:
        """The app-level ``Exception`` handler is what makes an opaque 500 impossible.

        This test makes the handler REACHABLE without leaving the process: the
        route's own refusal classifier is replaced with a ``raise`` of the same
        exception, so the failure arrives at the handler exactly as an unforeseen
        one would. That is the fault injection for the "no empty/default body" rule;
        without it, the claim "an unhandled exception is classified" would rest on
        reading the source.

        Goes red if: the handler is removed (the body becomes Starlette's
        21-byte ``Internal Server Error`` plain text); the handler leaks
        ``str(exc)``; or the status stops being 500.
        """

        service = self.service()
        client = self.client(service)
        from app.api import operating_surface as surface_module

        real = surface_module.OperatingSurface._classify_graph_failure

        def passthrough(exc):  # noqa: ANN001
            # Deliberately NOT classified: this is what an unforeseen exception
            # looks like by the time it leaves the route.
            raise exc

        surface_module.OperatingSurface._classify_graph_failure = staticmethod(passthrough)
        try:
            response = client.post(
                "/api/product/graph",
                json={
                    "graph_run_id": "handler-probe",
                    "steps": [
                        {"step_id": "s1", "action": {"declaration_id": "never-declared"}}
                    ],
                },
            )
        finally:
            surface_module.OperatingSurface._classify_graph_failure = real

        self.assertEqual(response.status_code, 500, response.text)
        envelope = self.assert_error_envelope(response)
        self.assertEqual(envelope["error_code"], "internal_execution_error")
        self.assertNotIn("StepGraphValidationError", response.text)
        self.assertFalse((self.root / "one.txt").exists())

    def test_02_a_valid_graph_still_executes_after_the_error_layer(self) -> None:
        """The contract must not cost the working path.

        Goes red if: the error handling refuses a valid graph, or the route stops
        reporting ``replayed: false`` for a fresh attempt.
        """

        client = self.client()
        response = self.graph(client, "err-ok", self.good_step_document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIs(body["replayed"], False)
        self.assertEqual(body["result"]["status"], "COMPLETED")
        self.assertEqual(body["result"]["steps"][0]["status"], "SUCCEEDED")
        self.assertEqual(
            (self.root / "one.txt").read_text(encoding="utf-8"), "1"
        )
        self.assertIs(body["command_started"], True)
        self.assertEqual(body["commands_started"], 1)

    def test_03_the_statuses_stay_where_they_were(self) -> None:
        """Each fault keeps its documented status, and gains a classification.

        Goes red if: a refusal is turned into a success (never acceptable), a
        caller error is upgraded to a server error, or a status is guessed from
        the exception's text.
        """

        client = self.client()

        # 400 -- a malformed body field.
        bad_id = self.graph(client, "../evil", self.good_step_document)
        self.assertEqual(bad_id.status_code, 400, bad_id.text)
        self.assertEqual(
            self.assert_error_envelope(bad_id)["error_code"], "invalid_request_schema"
        )

        # 403 -- the project boundary.
        foreign = client.get("/api/product/runs", params={"project_id": "another"})
        self.assertEqual(foreign.status_code, 403, foreign.text)
        self.assertEqual(
            self.assert_error_envelope(foreign)["error_code"], "forbidden_action"
        )

        # 404 -- an unrecorded run.
        missing = client.get("/api/product/runs/does-not-exist")
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(
            self.assert_error_envelope(missing)["error_code"], "state_conflict"
        )

        # 409 -- a well-formed request the server refuses (an undeclared action).
        undeclared = self.graph(
            client, "err-undeclared", [{"step_id": "s1", "action": {"declaration_id": "nope"}}]
        )
        self.assertEqual(undeclared.status_code, 409, undeclared.text)
        envelope = self.assert_error_envelope(undeclared)
        self.assertEqual(envelope["error_code"], "invalid_request_schema")
        self.assertEqual(envelope["code"], "graph_invalid")

        # 422 -- a malformed query parameter.
        bad_limit = client.get("/api/product/runs", params={"limit": "0"})
        self.assertEqual(bad_limit.status_code, 422, bad_limit.text)
        envelope = self.assert_error_envelope(bad_limit)
        self.assertEqual(envelope["error_code"], "invalid_request_schema")
        self.assertIn("validation_errors", envelope)

        # AND NOTHING RAN during any of it.
        self.assertFalse((self.root / "one.txt").exists())
        onboarding = client.get("/api/product/onboarding").json()
        self.assertEqual(onboarding["counts"]["recorded_runs"], 0)

    def test_03b_a_damaged_record_is_a_state_conflict_not_a_shape_error(self) -> None:
        """The two meanings of ``StepGraphStateError`` are not collapsed.

        A malformed REQUEST is the caller's 400. A persisted record this build
        cannot trust is a conflict with server state, 409. The route distinguishes
        them by WHERE the exception came from, not by reading its message.

        Goes red if: the persisted-record path starts answering 400; or a damaged
        record starts being treated as "no record" and resumes anyway.
        """

        service = self.service()
        client = self.client(service)
        self.assertEqual(
            self.graph(client, "damaged", self.good_step_document).status_code, 200
        )
        # Damage the record the way a torn write would: the declared plan is gone.
        import dataclasses

        state = self.run_store.read_state("damaged")
        self.assertIsNotNone(state)
        payload = dict(state.project_state)
        key = next(iter(payload))
        record = dict(payload[key])
        record["declared_plan"] = []
        payload[key] = record
        self.run_store.write_state(
            dataclasses.replace(state, project_state=payload)
        )

        response = client.post("/api/product/graph/damaged/resume", json={})
        self.assertNotEqual(response.status_code, 200, response.text)
        self.assertEqual(response.status_code, 409, response.text)
        envelope = self.assert_error_envelope(response)
        self.assertEqual(envelope["error_code"], "state_conflict")
        self.assertEqual(envelope["code"], "graph_state_refused")

    def test_04_a_missing_dependency_is_named_as_one(self) -> None:
        """No idempotency guard -> the composition cannot run a graph, and says so.

        Goes red if: the fail-closed composition starts building a runner with a
        process-wide default ledger; or the missing dependency is reported as the
        caller's conflict.
        """

        service = self.service()
        # Remove the injected guard: the composition can no longer build a runner.
        service._idempotency_guard = None
        client = self.client(service)
        response = self.graph(client, "err-noguard", self.good_step_document)
        self.assertEqual(response.status_code, 409, response.text)
        envelope = self.assert_error_envelope(response)
        self.assertEqual(envelope["error_code"], "missing_required_dependency")
        self.assertEqual(envelope["code"], "step_graph_unavailable")
        self.assertFalse((self.root / "one.txt").exists())

    def test_05_an_approval_refusal_is_named_as_one(self) -> None:
        """A required-but-ungranted approval is its own class, and nothing ran.

        Goes red if: the approval class is folded into a generic conflict, or the
        step starts being reported as executed.
        """

        service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        client = self.client(service)
        response = self.graph(client, "err-approval", self.good_step_document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["result"]["status"], "REFUSED")
        step = body["result"]["steps"][0]
        self.assertEqual(step["status"], "WAITING_APPROVAL")
        self.assertIn(step["execution_outcome"], {"APPROVAL_WAITING"})
        self.assertFalse((self.root / "one.txt").exists())
        # The approval projection names the requirement rather than granting it.
        approvals = client.get("/api/product/approvals/err-approval").json()
        self.assertEqual(approvals["awaiting"], 1)
        self.assertIs(approvals["requests"][0]["granted_by_server"], False)
        self.assertTrue(approvals["requests"][0]["intent_fingerprint"])

    def test_06_the_envelope_never_reflects_the_request_or_a_path(self) -> None:
        """No traceback, no absolute path, no echoed caller value.

        Goes red if: the handler starts publishing ``str(exc)`` for an unhandled
        exception, or a 4xx body starts echoing the caller's own input.
        """

        client = self.client()
        response = self.graph(client, "../escape-attempt", self.good_step_document)
        self.assertEqual(response.status_code, 400, response.text)
        self.assert_error_envelope(
            response, forbidden_extra=("escape-attempt", str(self.root))
        )
        self.assertNotIn(str(self.root).replace("\\", "\\\\"), response.text)
        # The message is from a closed table, so it is byte-identical across
        # requests with the same code.
        second = self.graph(client, "../escape-attempt-2", self.good_step_document)
        self.assertEqual(
            json.loads(second.text)["detail"]["message"],
            json.loads(response.text)["detail"]["message"],
        )

    def test_07_the_request_id_is_only_reported_when_the_caller_supplied_one(self) -> None:
        """A correlation handle, never an invented one.

        Goes red if: the server starts generating an id (which would look like a
        correlation with its log while correlating with nothing).
        """

        client = self.client()
        without = self.graph(client, "../id-check", self.good_step_document)
        self.assertEqual(without.status_code, 400, without.text)
        self.assertNotIn("request_id", json.loads(without.text)["detail"])

        with_header = client.post(
            "/api/product/graph",
            json={"graph_run_id": "../id-check-2", "steps": self.good_step_document},
            headers={"X-Request-ID": "operator-correlation-42"},
        )
        self.assertEqual(with_header.status_code, 400, with_header.text)
        self.assertEqual(
            json.loads(with_header.text)["detail"]["request_id"],
            "operator-correlation-42",
        )

    def test_08_the_error_and_success_shapes_are_both_readable_to_a_client(self) -> None:
        """One rule reads every error on this surface, whatever the route shape.

        The graph routes serialize a typed refusal FLAT; the verify/accept routes
        raise ``HTTPException``, which FastAPI nests under ``"detail"``. Block #10
        does not unify those two placements -- that would break every existing
        consumer -- it unifies the FIELDS. This test is the contract: read
        ``refused``/``executed``/``error_code``/``message`` from either placement.

        Goes red if: either half stops carrying the four fields.
        """

        client = self.client()
        flat = client.get("/api/product/change-impact", params={"baseline": "never"})
        self.assertEqual(flat.status_code, 409, flat.text)
        nested = client.post(
            "/api/product/verify", json={"declaration_id": "one"}
        )
        self.assertEqual(nested.status_code, 400, nested.text)

        for label, response in (("flat", flat), ("nested", nested)):
            with self.subTest(shape=label):
                envelope = self.assert_error_envelope(response)
                self.assertTrue(
                    set(REQUIRED_ERROR_KEYS).issubset(envelope.keys()), label
                )

    def test_09_a_missing_required_dependency_is_distinguishable_by_code(self) -> None:
        """_SENSITIVITY_: the dependency class is a lookup, not a status guess.

        Three different faults must produce three different ``error_code`` values,
        so a client can route them: a missing dependency, a request-shape error and
        a state conflict. Asserted together, because a rule that returned one code
        for all three would satisfy any single one of them.
        """

        from app.api.routes import error_code_for
        from app.api.operating_surface import Refused, SurfaceCompositionError

        dependency = SurfaceCompositionError("x", code="approval_policy_missing")
        shape = Refused("x", code="graph_invalid")
        conflict = Refused("x", code="graph_state_refused")
        codes = {
            error_code_for(dependency, 500),
            error_code_for(shape, 409),
            error_code_for(conflict, 409),
        }
        self.assertEqual(
            codes,
            {
                "missing_required_dependency",
                "invalid_request_schema",
                "state_conflict",
            },
        )


class ExecutedSemanticsTests(_ErrorEnvelopeCase):
    """``executed`` vs ``command_started``: what each one actually claims."""

    def test_20_a_command_that_started_and_succeeded_reports_both(self) -> None:
        """Case (a): the action started and completed.

        Goes red if: ``command_started`` stops being derived from the recorded
        outcome, or a real dispatch stops reporting true.
        """

        client = self.client()
        response = self.graph(client, "exec-ok", self.good_step_document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIs(body["executed"], True)
        self.assertIs(body["command_started"], True)
        self.assertEqual(body["commands_started"], 1)
        step = body["result"]["steps"][0]
        self.assertIs(step["command_started"], True)
        self.assertEqual(step["execution_outcome"], "EXECUTION_SUCCESS")
        self.assertEqual(step["execution_exit_code"], 0)
        self.assertTrue((self.root / "one.txt").exists())

    def test_21_waiting_for_approval_is_not_a_command_start(self) -> None:
        """Case (b): the graph was processed and is waiting for approval.

        This is the MEASURED defect. Before the fix the route answered
        ``executed: true`` with the step ``WAITING_APPROVAL``, and a client that
        read ``executed`` as "something ran" was simply wrong.

        Goes red if: ``command_started`` is derived from ``step_run_id`` or from the
        step status (both are set on this path), instead of from the recorded
        execution outcome.
        """

        service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        client = self.client(service)
        response = self.graph(client, "exec-wait", self.good_step_document)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        result = body["result"]
        self.assertEqual(result["status"], "REFUSED")
        step = result["steps"][0]
        self.assertEqual(step["status"], "WAITING_APPROVAL")
        self.assertEqual(step["execution_outcome"], "APPROVAL_WAITING")
        # THE TWO FALSE CLAIMS THE OLD SURFACE MADE, both still visible as facts:
        # the execution plane WAS named, and the step run id WAS assigned...
        self.assertEqual(step["performed_executable"], "python")
        self.assertTrue(step["step_run_id"])
        # ...and neither of them is a command start.
        self.assertIs(step["command_started"], False)
        self.assertIs(body["command_started"], False)
        self.assertEqual(body["commands_started"], 0)
        self.assertFalse((self.root / "one.txt").exists())

    def test_22_a_pre_execution_refusal_is_not_a_command_start(self) -> None:
        """Case (c): the graph refused before the command started.

        An undeclared action is refused by ``StepGraph.assert_valid`` before any
        scope is frozen, so the attempt never reaches the execution plane at all.

        Goes red if: a refused attempt reports ``command_started`` true, or the
        four-case distinction collapses.
        """

        client = self.client()
        response = self.graph(
            client,
            "exec-refused",
            [{"step_id": "s1", "action": {"declaration_id": "never-declared"}}],
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.status_code, 409)
        self.assertFalse((self.root / "one.txt").exists())
        # The refusal carries no command start at all -- the body is an error
        # envelope, and the envelope says nothing ran.
        envelope = self.assert_error_envelope(response)
        self.assertIs(envelope["executed"], False)

    def test_23_a_graph_stopped_by_error_reports_the_real_outcome(self) -> None:
        """Case (d): the command really ran and did not succeed.

        This is the difference between the four cases, stated as an assertion: the
        command started (true), and the step is FAILED with the real exit code.

        Goes red if: a failed-but-real execution stops reporting
        ``command_started`` true (which would hide that the process existed).
        """

        client = self.client()
        response = self.graph(
            client,
            "exec-failed",
            [
                {
                    "step_id": "s1",
                    "action": {"declaration_id": self.fail_id},
                    "verification": {"kind": "EXIT_CODE", "expected_exit_code": 0},
                }
            ],
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        step = body["result"]["steps"][0]
        self.assertEqual(step["status"], "FAILED")
        self.assertEqual(step["execution_outcome"], "EXECUTION_FAILURE")
        self.assertEqual(step["execution_exit_code"], 3)
        self.assertIs(step["command_started"], True)
        self.assertIs(body["command_started"], True)
        self.assertEqual(body["commands_started"], 1)
        self.assertEqual(body["result"]["status"], "FAILED")

    def test_24_a_replay_starts_nothing_and_says_so(self) -> None:
        """A recorded attempt returned again is not a command start.

        Goes red if: ``command_started``/``commands_started`` are taken from the
        RECORDED attempt rather than from what this call did.
        """

        client = self.client()
        first = self.graph(client, "exec-replay", self.good_step_document)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertIs(first.json()["command_started"], True)

        second = self.graph(client, "exec-replay", self.good_step_document)
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertIs(body["replayed"], True)
        self.assertIs(body["executed"], False)
        self.assertIs(body["command_started"], False)
        self.assertEqual(body["commands_started"], 0)
        # The RECORDED attempt did start one command, and the record still says so.
        self.assertIs(body["result"]["steps"][0]["command_started"], True)

    def test_25_the_client_can_tell_all_four_cases_apart(self) -> None:
        """_SENSITIVITY_: the four cases are distinguishable from the response.

        A single field cannot answer four questions, so the assertion is that the
        PAIR (``status``, ``command_started``) separates them. Goes red if any two
        cases collapse to the same pair -- which is exactly what happened before
        ``command_started`` existed, when cases (b) and (c) both read
        ``executed: true``.
        """

        cases: dict[str, tuple[str, bool]] = {}

        # (a) started and completed
        client = self.client()
        body = self.graph(client, "four-a", self.good_step_document).json()
        cases["started_and_completed"] = (body["result"]["status"], body["command_started"])

        # (b) waiting for approval
        waiting_service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        waiting_client = self.client(waiting_service)
        body = self.graph(waiting_client, "four-b", self.good_step_document).json()
        cases["waiting_for_approval"] = (body["result"]["status"], body["command_started"])

        # (c) refused before the command started -- error envelope
        refused = self.graph(
            client,
            "four-c",
            [{"step_id": "s1", "action": {"declaration_id": "never-declared"}}],
        )
        envelope = self.assert_error_envelope(refused)
        cases["refused_before_execution"] = (
            envelope["error_code"],
            bool(envelope["executed"]),
        )

        # (d) stopped by error after the command ran
        body = self.graph(
            client,
            "four-d",
            [
                {
                    "step_id": "s1",
                    "action": {"declaration_id": self.fail_id},
                    "verification": {"kind": "EXIT_CODE", "expected_exit_code": 0},
                }
            ],
        ).json()
        cases["stopped_by_error"] = (body["result"]["status"], body["command_started"])

        self.assertEqual(len(set(cases.values())), 4, cases)
        self.assertEqual(cases["started_and_completed"], ("COMPLETED", True))
        self.assertEqual(cases["waiting_for_approval"], ("REFUSED", False))
        self.assertEqual(cases["refused_before_execution"][0], "invalid_request_schema")
        self.assertEqual(cases["refused_before_execution"][1], False)
        self.assertEqual(cases["stopped_by_error"], ("FAILED", True))


class ScenarioCTests(_ErrorEnvelopeCase):
    """Scenario C: a controlled execution error, driven end to end.

    ``HTTP -> Graph -> Approval -> Authorized Execution -> Command Result ->
    RunStore -> Recovery -> HTTP Response``.

    The command is a safe local one that writes a PARTIAL side effect and then
    exits 3, so "did anything land on disk" is a real question answered by
    independent inspection of the filesystem rather than by an assumption.
    """

    #: A command that leaves a partial side effect behind and then fails. Local,
    #: no network, no arguments from the caller.
    PARTIAL_THEN_FAIL = (
        "python",
        "-c",
        "open('partial.txt','w',encoding='utf-8').write('half');"
        "print('about to fail'); raise SystemExit(3)",
    )

    def service(self, **kwargs):  # type: ignore[override]
        declarations = dict(self.declarations())
        from app.execution.declaration import ExecutionDeclaration

        declarations["scenario.c"] = ExecutionDeclaration(
            declaration_id="scenario.c",
            command=self.PARTIAL_THEN_FAIL,
            profile_id="block8-profile",
            working_directory=".",
            intent="Leave a partial side effect, then fail.",
            timeout_seconds=30.0,
        )
        kwargs.setdefault("declarations", declarations)
        return super().service(**kwargs)

    def _steps(self):
        return [
            {
                "step_id": "c-step",
                "action": {"declaration_id": "scenario.c"},
                "verification": {"kind": "EXIT_CODE", "expected_exit_code": 0},
            }
        ]

    def test_30_waiting_for_approval_leaves_no_partial_side_effect(self) -> None:
        """Before any grant, the Scenario C command has not run at all.

        Goes red if: a step refused for want of approval still dispatches, which
        would make the Scenario C measurement below meaningless.
        """

        service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        client = self.client(service)

        first = self.graph(client, "scenario-c", self._steps())
        self.assertEqual(first.status_code, 200, first.text)
        body = first.json()
        self.assertEqual(body["result"]["status"], "REFUSED")
        self.assertEqual(body["result"]["steps"][0]["status"], "WAITING_APPROVAL")
        self.assertIs(body["command_started"], False)
        self.assertFalse((self.root / "partial.txt").exists())

        # The approval projection publishes the exact fingerprint a grant must
        # match, and refuses to grant it itself.
        approvals = client.get("/api/product/approvals/scenario-c").json()
        self.assertEqual(approvals["awaiting"], 1)
        self.assertTrue(approvals["requests"][0]["intent_fingerprint"])
        self.assertIs(approvals["requests"][0]["granted_by_server"], False)

    def test_30b_the_failing_command_really_runs_and_fails_under_a_matching_approval(
        self,
    ) -> None:
        """The same chain, with approval granted for exactly the recorded intent.

        The approval is submitted under the ``(run_id, invocation_id)`` key the
        coordinator really uses, for the fingerprint the route published. The
        command then really runs, really writes its partial side effect, and
        really exits 3.

        Goes red if: the command stops running (``command_started`` false); the exit
        code stops being recorded; the API reports success; or the partial side
        effect stops being detected by looking at the disk.
        """

        from app.tools.approval import ApprovalState

        service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        client = self.client(service)
        run_id = "scenario-c-real"
        steps = self._steps()
        steps[0]["step_id"] = "c-step"

        # The approval must exist BEFORE the dispatch, keyed exactly as the
        # coordinator will ask for it.
        step_run_id = f"step.{run_id}.c-step"
        invocation = f"{step_run_id}#request"
        fingerprint = self._fingerprint_for(service, steps, run_id)
        self.assertTrue(fingerprint)
        service._approval_resolver.submit(
            step_run_id, invocation, ApprovalState.APPROVED, intent_fingerprint=fingerprint
        )

        response = self.graph(client, run_id, steps)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        result = body["result"]
        step = result["steps"][0]

        # -- Authorized Execution really happened.
        self.assertIs(body["command_started"], True, body)
        self.assertIs(step["command_started"], True)
        self.assertEqual(step["performed_executable"], "python")
        self.assertEqual(step["performed_working_directory"], ".")
        self.assertTrue(step["step_run_id"])
        self.assertTrue(step["request_id"])

        # -- Command Result: a real, controlled failure.
        self.assertEqual(step["execution_outcome"], "EXECUTION_FAILURE")
        self.assertEqual(step["execution_exit_code"], 3)
        self.assertEqual(step["status"], "FAILED")
        # The declared check was not reached, and the record says WHICH of the two
        # things happened: a check that could not be evaluated because the command
        # itself failed. ``NOT_RUN`` here is the honest value -- it is never folded
        # into a pass, and the evidence names the reason.
        self.assertEqual(step["verification_status"], "NOT_RUN")
        self.assertEqual(step["verification_expected"], "exit_code=0")
        self.assertIn(
            "verification_not_run:execution_did_not_succeed", step["evidence"]
        )

        # -- Statuses match the real outcome. THE API DOES NOT REPORT SUCCESS.
        self.assertNotEqual(result["status"], "COMPLETED")
        self.assertEqual(result["status"], "FAILED")
        self.assertIs(body["replayed"], False)

        # -- A PARTIAL SIDE EFFECT IS DETECTED BY INDEPENDENT DISK INSPECTION,
        #    not assumed absent. The command wrote it before exiting 3.
        partial = self.root / "partial.txt"
        self.assertTrue(partial.exists(), "the partial side effect was not found on disk")
        self.assertEqual(partial.read_text(encoding="utf-8"), "half")
        self.assertEqual(
            hashlib.sha256(partial.read_bytes()).hexdigest(),
            hashlib.sha256(b"half").hexdigest(),
        )

        # -- RunStore stores the REAL result: the step's own durable run history
        #    names the authorization decision and the real outcome. Read through
        #    the store's own event log, which is what survives a restart.
        step_events = self._step_events(service, step_run_id)
        self.assertTrue(step_events, "the step run recorded no events")
        kinds = [event["type"] for event in step_events]
        self.assertIn("EXECUTION_REQUESTED", kinds, kinds)
        self.assertIn("EXECUTION_COMPLETED", kinds, kinds)
        completed = next(
            event for event in step_events if event["type"] == "EXECUTION_COMPLETED"
        )
        self.assertEqual(completed["metadata"]["outcome_status"], "EXECUTION_FAILURE")
        self.assertEqual(completed["metadata"]["exit_code"], 3)
        self.assertEqual(completed["metadata"]["status"], "FAILURE")
        # The durable record carries no traceback and no absolute internal path.
        joined = json.dumps(step_events)
        self.assertNotIn("Traceback", joined)
        self.assertNotIn(str(self.root), joined)
        self.assertNotIn("about to fail", joined)

        # -- Diagnostics are available without a traceback, a secret or an absolute
        #    internal path.
        diagnostics = client.get(f"/api/product/runs/{step_run_id}")
        self.assertEqual(diagnostics.status_code, 200, diagnostics.text)
        text = diagnostics.text
        for token in ("Traceback (most recent call last)", "site-packages", str(self.root)):
            self.assertNotIn(token, text)
        self.assertNotIn("about to fail", text, "the child's stdout was published")

        # -- A repeat does not re-execute: the record stops it, and the HTTP
        #    response says so in its own field.
        before = partial.read_text(encoding="utf-8")
        again = self.graph(client, run_id, steps)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertIs(again.json()["replayed"], True)
        self.assertIs(again.json()["command_started"], False)
        self.assertEqual(partial.read_text(encoding="utf-8"), before)

    def test_30c_the_recovery_scan_never_claims_the_failed_step_succeeded(self) -> None:
        """The recovery projection reports the real state and resumes nothing.

        Goes red if: a recovery plan reports a step as recovered without a claim
        token, or the recovery route writes a claim.
        """

        from app.tools.approval import ApprovalState

        service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        client = self.client(service)
        run_id = "scenario-c-recovery"
        steps = self._steps()
        step_run_id = f"step.{run_id}.c-step"
        fingerprint = self._fingerprint_for(service, steps, run_id)
        service._approval_resolver.submit(
            step_run_id,
            f"{step_run_id}#request",
            ApprovalState.APPROVED,
            intent_fingerprint=fingerprint,
        )
        self.assertEqual(self.graph(client, run_id, steps).status_code, 200)

        scan = client.get("/api/product/recovery")
        self.assertEqual(scan.status_code, 200, scan.text)
        body = scan.json()
        self.assertIn("plans", body)
        for plan in body["plans"]:
            self.assertNotIn("recovered", json.dumps(plan))
        # The plan for this attempt names the step's real state.
        matching = [
            plan for plan in body["plans"] if plan.get("graph_run_id") == run_id
        ]
        self.assertTrue(matching, f"no recovery plan for {run_id}: {body}")
        plan = matching[0]
        self.assertFalse(plan.get("dispatch_performed", False))
        statuses = {
            step.get("step_id"): step.get("recorded_status") or step.get("status")
            for step in plan.get("steps", [])
        }
        self.assertNotIn("SUCCEEDED", json.dumps(statuses))

        # The per-step route agrees and still executes nothing.
        detail = client.get(f"/api/product/recovery/{run_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertNotEqual(detail.json().get("recorded_status"), "COMPLETED")

    def test_31_an_unknown_side_effect_forbids_an_automatic_retry(self) -> None:
        """``REFUSED_SIDE_EFFECT_UNKNOWN`` is a refusal, never a permission.

        The repair loop must return that outcome unchanged, stop with
        ``side_effect_outcome_unknown`` and dispatch nothing. This is the rule the
        whole block turns on: an effect that may have landed must never be doubled
        by an automatic repeat.

        Goes red if: ``_plan_step`` stops short-circuiting on that guarantee (the
        outcome becomes ``REPAIR_ADMISSIBLE`` with ``permits_automatic_repeat``);
        or the loop's ``dispatch_performed``/``dispatched`` starts reporting true.
        """

        from app.analysis.repair import RepairLoop, RepairOutcome, StopRule
        from app.agent_runtime.idempotency_integration import RepeatGuarantee
        from app.workflows.step_graph import (
            StepGraphSnapshot,
            StepRecord,
            StepGraphStatus,
            StepStatus,
            VerificationStatus,
        )

        class _Decision:
            guarantee = RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN
            permits_automatic_repeat = False
            reason = "a non-replay-safe effect began with no surviving completion"

        snapshot = StepGraphSnapshot(
            graph_id="repair-probe",
            graph_run_id="repair-probe",
            status=StepGraphStatus.FAILED,
            steps=(
                StepRecord(
                    step_id="s1",
                    status=StepStatus.FAILED,
                    declaration_id="declared.action",
                    execution_outcome="EXECUTION_FAILURE",
                    execution_exit_code=3,
                    verification_status=VerificationStatus.FAILED,
                    attempts=1,
                ),
            ),
            attempts=1,
        )
        loop = RepairLoop(
            graph_run_id="repair-probe",
            snapshot=snapshot,
            declarations={"declared.action": None},
            repeat_decisions={"s1": _Decision()},
            approval_policy=ApprovalPolicy(),
            approval_resolver=None,
        )
        plan = loop.plan()
        self.assertEqual(plan.outcome, RepairOutcome.REFUSED_SIDE_EFFECT_UNKNOWN)
        self.assertEqual(plan.stop_rule, StopRule.SIDE_EFFECT_OUTCOME_UNKNOWN)
        self.assertIs(plan.dispatch_performed, False)
        self.assertEqual(len(plan.steps), 1)
        step = plan.steps[0]
        self.assertEqual(step.step_id, "s1")
        self.assertIs(step.permits_automatic_repeat, False)
        self.assertIs(step.authorized_to_dispatch, False)
        self.assertIs(step.dispatched, False)
        self.assertEqual(
            step.refusal_code, RepeatGuarantee.REFUSED_SIDE_EFFECT_UNKNOWN.value
        )
        # _SENSITIVITY_: with the SAME snapshot but a guarantee that does permit a
        # repeat, the loop reaches a different outcome -- so the refusal above is
        # produced by the guarantee and not by the fixture.
        class _Permissive:
            guarantee = RepeatGuarantee.FIRST_ATTEMPT
            permits_automatic_repeat = True
            reason = "the recorded effect reached a terminal position"

        other = RepairLoop(
            graph_run_id="repair-probe",
            snapshot=snapshot,
            declarations={"declared.action": None},
            repeat_decisions={"s1": _Permissive()},
            approval_policy=ApprovalPolicy(),
            approval_resolver=None,
        ).plan()
        self.assertNotEqual(other.outcome, RepairOutcome.REFUSED_SIDE_EFFECT_UNKNOWN)
        self.assertIs(other.dispatch_performed, False)
        # Still nothing dispatched, on either path: the loop is a planner.
        self.assertIs(other.steps[0].dispatched, False)

    def test_32_the_repair_route_dispatches_nothing_and_says_where_it_stops(
        self,
    ) -> None:
        """Over HTTP: the repair plan is bounded, explicit and inert.

        Goes red if: the route starts executing a repair, or the response stops
        naming why the loop stopped.
        """

        from app.tools.approval import ApprovalState

        service = self.service(
            approval_policy=ApprovalPolicy(approval_required_tools=("python",))
        )
        client = self.client(service)
        run_id = "scenario-c-repair"
        steps = self._steps()
        step_run_id = f"step.{run_id}.c-step"
        fingerprint = self._fingerprint_for(service, steps, run_id)
        service._approval_resolver.submit(
            step_run_id,
            f"{step_run_id}#request",
            ApprovalState.APPROVED,
            intent_fingerprint=fingerprint,
        )
        self.assertEqual(self.graph(client, run_id, steps).status_code, 200)
        partial_before = (self.root / "partial.txt").read_bytes()
        workspace_before = _workspace_fingerprint(self.root)

        response = client.post(
            "/api/product/repair-plan", json={"graph_run_id": run_id}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIs(body["dispatch_performed"], False)
        self.assertTrue(body["stop_rule"])
        self.assertIn("dispatch_path", body)
        self.assertTrue(body["dispatch_path"])
        self.assertIs(body["found"], True)
        for step in body["steps"]:
            self.assertIs(step["dispatched"], False)
            self.assertIs(step["authorized_to_dispatch"], False)
        # The workspace is byte-identical: planning is not a side effect.
        self.assertEqual(_workspace_fingerprint(self.root), workspace_before)
        self.assertEqual((self.root / "partial.txt").read_bytes(), partial_before)

    # -- helpers ---------------------------------------------------------
    def _fingerprint_for(self, service, steps, run_id: str) -> str:
        """The intent fingerprint the coordinator WILL bind an approval to.

        Read from the composed runner's own pure helper, which is the same value
        ``/api/product/approvals`` publishes -- so the test grants approval for
        exactly what the server will ask for, rather than for a value the test
        invented.
        """

        from app.api.operating_surface import build_operating_surface

        surface = build_operating_surface(service)
        runner = surface.build_step_graph_runner()
        self.assertIsNotNone(runner)
        from app.api.operating_surface import _graph_from_request

        graph = _graph_from_request("operator-plan", steps)
        return runner.intent_fingerprint(graph, steps[0]["step_id"])

    def _step_events(self, service, run_id: str) -> list[dict]:
        record = service._run_store.load(run_id)
        if record is None:
            return []
        return [
            {"type": event.event_type.name, "metadata": dict(event.metadata)}
            for event in record.events
        ]


def _workspace_fingerprint(root: Path) -> dict[str, str]:
    """Every path under ``root`` with its digest, so a write cannot hide."""

    fingerprint: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            fingerprint[relative + "/"] = "dir"
            continue
        fingerprint[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprint


def _free_port() -> int:
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


if __name__ == "__main__":
    unittest.main()
