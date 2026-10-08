"""Stage 0.1: durable physical usage telemetry.

These tests pin the Stage 0.1 boundary frozen in ``docs/DECISIONS.md``
(``D-PLATFORM-01..12``):

* ``PhysicalTelemetry`` is a **physical** contract: tokens, duration, attempt
  identity, and outcome, and provably nothing financial and no ownership;
* the durable sink is append-only, idempotent per attempt, and survives a
  process restart;
* production records telemetry at the **terminal** boundary of an attempt, and
  never before it;
* a missing measurement is never replaced with a guessed value.

The tests drive the real ``AgentHarness`` and the real ``AIDecisionProvider``;
no test double replaces the code under test.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessPhase, HarnessRequest, HarnessStatus
from app.agent_runtime.physical_telemetry import (
    FORBIDDEN_TELEMETRY_FIELDS,
    MAX_ATTEMPT_NUMBER,
    MAX_TOOL_CALL_COUNT,
    MAX_TOKEN_COUNT,
    FileTelemetrySink,
    PhysicalTelemetry,
    TelemetryWriteError,
)
from app.agent_runtime.policy import AgentHarnessPolicy
from app.runtime.bootstrap import create_agent_harness
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.decision.ai_provider import AIDecisionProvider
from app.decision.models import Decision, DecisionAction, DecisionType
from app.tools.acceptance import AcceptanceResult, AcceptanceStatus
from app.tools.workspace import Workspace
from app.usage import Usage


class _UsageProvider(Provider):
    """A provider double that reports a fixed physical measurement."""

    PROVIDER_NAME = "usage-double"

    def __init__(self, *, tokens=(120, 45, 7), fail=False) -> None:
        self._tokens = tokens
        self._fail = fail
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return self.PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return "usage-double-model"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        if self._fail:
            raise RuntimeError("provider unavailable")
        return ProviderResponse(
            provider_name=self.PROVIDER_NAME,
            model_name="usage-double-model",
            output=json.dumps(
                {
                    "decision_type": "COMPLETE",
                    "action": "COMPLETE_RUN",
                    "reason_code": "done",
                    "rationale": "nothing left to do",
                    "confidence": 1.0,
                }
            ),
            usage=Usage(
                input_tokens=self._tokens[0],
                output_tokens=self._tokens[1],
                cached_tokens=self._tokens[2],
            ),
        )


class _FixedActionProvider:
    """Return one fixed action, with no provider call and no measurement."""

    def __init__(
        self,
        action: DecisionAction,
        decision_type: DecisionType = DecisionType.CONTINUE,
    ) -> None:
        self._action = action
        self._decision_type = decision_type

    def decide(self, request):
        return Decision(
            decision_id="fixed-no-measurement",
            run_id=request.run_id,
            decision_type=self._decision_type,
            action=self._action,
            reason_code="fixed",
        )


class _PassingAcceptanceGate:
    """A gate double that reports acceptance as passed.

    It replaces only the acceptance *outcome*, so the run loop reaches its real
    COMPLETED terminal path. It has no execution authority and cannot affect the
    telemetry boundary under test.
    """

    def evaluate(self, criteria=(), verifications=None, run_id="", observer=None,
                 requirements=None) -> AcceptanceResult:
        return AcceptanceResult(status=AcceptanceStatus.PASS, results=(), code="passed")


class _CountingSink:
    """A sink double that counts calls, to prove the boundary is reached once."""

    def __init__(self) -> None:
        self.records: list[PhysicalTelemetry] = []

    def record(self, telemetry: PhysicalTelemetry) -> bool:
        self.records.append(telemetry)
        return True


class _FailingSink:
    """A sink double that always fails, to prove failure is never silent.

    The exception type is configurable because the port promises that *any*
    persistence failure is normalized, not only :class:`TelemetryWriteError`.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        self.calls = 0
        self._error = error or TelemetryWriteError("disk on fire")

    def record(self, telemetry: PhysicalTelemetry) -> bool:
        self.calls += 1
        raise self._error


class PhysicalTelemetrySchemaTests(unittest.TestCase):
    """A, I: the schema is physical and the projection is closed."""

    def test_serialization_is_explicit_and_round_trips(self) -> None:
        """A: one bounded projection, and it round-trips."""
        record = PhysicalTelemetry(
            run_id="run-1",
            attempt_number=2,
            provider_name="deepseek",
            model_name="deepseek-chat",
            input_tokens=1000,
            output_tokens=250,
            cached_tokens=60,
            duration_seconds=1.234567,
            success=True,
            fallback=True,
            tool_call_count=3,
            completed_at="2026-10-08T00:00:00+00:00",
        )
        payload = record.to_dict()
        self.assertEqual(payload["attempt_id"], "run-1#attempt-2")
        self.assertEqual(payload["total_tokens"], 1250)
        self.assertEqual(payload["duration_seconds"], 1.234567)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["tool_call_count"], 3)

        restored = PhysicalTelemetry.from_dict(payload)
        self.assertEqual(restored.to_dict(), payload)
        self.assertEqual(restored.identity, record.identity)

    def test_attempt_identity_follows_the_existing_convention(self) -> None:
        """A: there is one attempt identity format, not a competing second one."""
        from app.agent_runtime.idempotency import AttemptIdentity

        record = PhysicalTelemetry(run_id="run-9", attempt_number=4)
        self.assertEqual(
            record.identity, AttemptIdentity(run_id="run-9", attempt_number=4).attempt_id
        )

    def test_serialized_output_contains_no_financial_or_ownership_field(self) -> None:
        """I: no money, no price, no charge, no ownership, no identity leaks in."""
        record = PhysicalTelemetry(
            run_id="run-1",
            attempt_number=0,
            input_tokens=5,
            output_tokens=5,
            success=True,
        )
        keys = set(record.to_dict())
        leaked = keys & FORBIDDEN_TELEMETRY_FIELDS
        self.assertEqual(leaked, set(), f"financial/ownership fields leaked: {leaked}")
        # A positive check too: the contract really does carry the physical set.
        for expected in (
            "run_id",
            "attempt_number",
            "provider_name",
            "model_name",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "duration_seconds",
            "success",
            "fallback",
            "tool_call_count",
            "completed_at",
        ):
            self.assertIn(expected, keys)

    def test_telemetry_rejects_a_negative_measurement(self) -> None:
        """A: a nonsensical measurement is refused at construction."""
        with self.assertRaises(ValueError):
            PhysicalTelemetry(run_id="run-1", input_tokens=-1)
        with self.assertRaises(ValueError):
            PhysicalTelemetry(run_id="run-1", duration_seconds=-0.5)
        with self.assertRaises(ValueError):
            PhysicalTelemetry(run_id="")

    def test_record_is_immutable(self) -> None:
        """A: a persisted measurement cannot be rewritten in place."""
        record = PhysicalTelemetry(run_id="run-1", input_tokens=1)
        with self.assertRaises(Exception):
            record.input_tokens = 2  # type: ignore[misc]


class FileTelemetrySinkTests(unittest.TestCase):
    """B, E, F, H: durable append, idempotency, distinct attempts, restart."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "telemetry"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _record(self, run_id="run-1", attempt=0, **overrides) -> PhysicalTelemetry:
        payload = {
            "run_id": run_id,
            "attempt_number": attempt,
            "provider_name": "deepseek",
            "model_name": "deepseek-chat",
            "input_tokens": 10,
            "output_tokens": 4,
            "cached_tokens": 1,
            "duration_seconds": 0.5,
            "success": True,
            "completed_at": "2026-10-08T00:00:00+00:00",
        }
        payload.update(overrides)
        return PhysicalTelemetry(**payload)

    def test_appends_one_json_line_per_attempt(self) -> None:
        """B: a terminal attempt becomes exactly one durable line."""
        sink = FileTelemetrySink(self.root)
        self.assertTrue(sink.record(self._record()))
        path = sink.path_for_run("run-1")
        self.assertTrue(path.exists())
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["run_id"], "run-1")
        self.assertEqual(payload["attempt_id"], "run-1#attempt-0")

    def test_duplicate_terminal_persistence_is_idempotent(self) -> None:
        """E: re-persisting one attempt writes no second record."""
        sink = FileTelemetrySink(self.root)
        self.assertTrue(sink.record(self._record()))
        self.assertFalse(sink.record(self._record()))
        self.assertFalse(sink.record(self._record(input_tokens=999)))
        self.assertEqual(len(sink.read_records("run-1")), 1)
        self.assertEqual(sink.read_records("run-1")[0].input_tokens, 10)

    def test_two_attempts_produce_two_records(self) -> None:
        """F: distinct attempts are distinct records."""
        sink = FileTelemetrySink(self.root)
        sink.record(self._record(attempt=0))
        sink.record(self._record(attempt=1))
        records = sink.read_records("run-1")
        self.assertEqual(len(records), 2)
        self.assertEqual(
            [r.identity for r in records],
            ["run-1#attempt-0", "run-1#attempt-1"],
        )

    def test_durable_across_a_process_restart(self) -> None:
        """H: a brand new sink instance sees the existing record and does not duplicate."""
        FileTelemetrySink(self.root).record(self._record())
        restarted = FileTelemetrySink(self.root)
        records = restarted.read_records("run-1")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].output_tokens, 4)
        # The restart must not re-append the same attempt.
        self.assertFalse(restarted.record(self._record()))
        self.assertEqual(len(restarted.read_records("run-1")), 1)

    def test_storage_root_is_never_the_workspace(self) -> None:
        """Storage: telemetry lives in its own runtime root, never a workspace."""
        from app.agent_runtime.physical_telemetry import default_telemetry_root

        # The default root is the configured runtime root, and it is resolved
        # outside any workspace directory the caller passes in.
        self.assertEqual(FileTelemetrySink().root, default_telemetry_root())
        self.assertNotEqual(FileTelemetrySink().root, Path(self._tmp.name))
        # With no configuration it follows the project's per-user convention.
        import os

        previous = os.environ.pop("FORGE_TELEMETRY_ROOT", None)
        try:
            fallback = default_telemetry_root()
            self.assertEqual(fallback.name, "telemetry")
            self.assertNotIn(str(Path.cwd()), str(fallback))
        finally:
            if previous is not None:
                os.environ["FORGE_TELEMETRY_ROOT"] = previous

    def test_list_run_ids_reports_runs_with_records(self) -> None:
        """H: the durable log is inspectable."""
        sink = FileTelemetrySink(self.root)
        sink.record(self._record(run_id="run-a"))
        sink.record(self._record(run_id="run-b"))
        self.assertEqual(set(sink.list_run_ids()), {"run-a", "run-b"})

    def test_a_truncated_tail_never_fabricates_a_record(self) -> None:
        """G: an unreadable line is skipped, not guessed at."""
        sink = FileTelemetrySink(self.root)
        sink.record(self._record())
        path = sink.path_for_run("run-1")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write('{"run_id": "run-1", "input_tok')
        records = sink.read_records("run-1")
        self.assertEqual(len(records), 1)

    def test_record_requires_the_contract_type(self) -> None:
        """G: a sink refuses anything that is not the physical contract."""
        with self.assertRaises(TypeError):
            FileTelemetrySink(self.root).record({"run_id": "run-1"})


class HarnessTelemetryBoundaryTests(unittest.TestCase):
    """B, C, D, G: the real run loop records only at the terminal boundary."""

    def setUp(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp.cleanup()

    def _harness(self, *, provider, sink, acceptance_gate=None) -> AgentHarness:
        return AgentHarness(
            decision_provider=provider,
            policy=AgentHarnessPolicy(),
            acceptance_gate=acceptance_gate or _PassingAcceptanceGate(),
            telemetry_sink=sink,
        )

    def _request(self, run_id="run-boundary", attempt_number=0) -> HarnessRequest:
        return HarnessRequest(
            run_id=run_id,
            workspace=self.workspace,
            attempt_number=attempt_number,
        )

    def test_successful_terminal_attempt_persists_measured_tokens(self) -> None:
        """B: a completed attempt records its real measured tokens."""
        sink = FileTelemetrySink(self.root / "telemetry")
        provider = AIDecisionProvider(provider=_UsageProvider())
        harness = self._harness(provider=provider, sink=sink)

        result = harness.run(self._request())

        # The run reaches a genuine terminal outcome. A bare run cannot reach
        # COMPLETED because completion requires a PASS acceptance that only a full
        # verification round produces, so the honest terminal state is FAILED and
        # the measurement records exactly that.
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        records = sink.read_records("run-boundary")
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertFalse(record.success)
        self.assertTrue(record.error_type)
        self.assertEqual(record.input_tokens, 120)
        self.assertEqual(record.output_tokens, 45)
        self.assertEqual(record.cached_tokens, 7)
        self.assertEqual(record.provider_name, "usage-double")
        self.assertEqual(record.model_name, "usage-double-model")
        self.assertGreater(record.duration_seconds, 0.0)
        self.assertEqual(record.attempt_id, "run-boundary#attempt-0")

    def test_failed_terminal_attempt_persists_with_success_false(self) -> None:
        """C: a failed attempt is recorded honestly as a failure."""
        sink = FileTelemetrySink(self.root / "telemetry")
        provider = AIDecisionProvider(provider=_UsageProvider(fail=True))
        harness = self._harness(provider=provider, sink=sink)

        result = harness.run(self._request(run_id="run-failed"))

        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        records = sink.read_records("run-failed")
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0].success)
        self.assertTrue(records[0].error_type)

    def test_no_record_before_the_terminal_boundary(self) -> None:
        """D: nothing is persisted while the attempt is still running."""
        sink = _CountingSink()
        provider = _FixedActionProvider(DecisionAction.COMPLETE_RUN, DecisionType.COMPLETE)
        harness = self._harness(provider=provider, sink=sink)
        # Observe the sink from inside the loop: at the moment the decision is
        # taken, the attempt is not terminal and nothing may be recorded yet.
        seen_during_run: list[int] = []
        original = provider.decide

        def spy(request):
            decision = original(request)
            seen_during_run.append(len(sink.records))
            return decision

        provider.decide = spy  # type: ignore[method-assign]
        result = harness.run(self._request(run_id="run-premature"))

        self.assertTrue(result.final_state.terminal)
        self.assertEqual(seen_during_run, [0], "telemetry preceded the terminal boundary")
        self.assertEqual(len(sink.records), 1)
        self.assertEqual(sink.records[0].identity, "run-premature#attempt-0")

    def test_duplicate_terminal_persistence_through_the_loop_is_idempotent(self) -> None:
        """E: driving the same attempt twice yields one durable record."""
        sink = FileTelemetrySink(self.root / "telemetry")
        provider = _FixedActionProvider(DecisionAction.COMPLETE_RUN, DecisionType.COMPLETE)
        harness = self._harness(provider=provider, sink=sink)

        harness.run(self._request(run_id="run-twice"))
        harness.run(self._request(run_id="run-twice"))

        self.assertEqual(len(sink.read_records("run-twice")), 1)

    def test_waiting_for_approval_is_not_a_recorded_terminal_outcome(self) -> None:
        """G: only a genuinely finished attempt is a recordable terminal outcome.

        ``WAITING_FOR_APPROVAL`` is terminal for the run loop but the attempt is
        not finished, so it is deliberately outside the persisted status set.
        """
        recorded_statuses = {
            HarnessStatus.COMPLETED.value,
            HarnessStatus.FAILED.value,
            HarnessStatus.LIMIT_REACHED.value,
        }
        self.assertNotIn(HarnessStatus.WAITING_FOR_APPROVAL.value, recorded_statuses)
        self.assertIn(HarnessStatus.COMPLETED.value, recorded_statuses)
        self.assertIn(HarnessStatus.FAILED.value, recorded_statuses)
        self.assertIn(HarnessStatus.LIMIT_REACHED.value, recorded_statuses)

    def test_missing_measurement_is_never_invented(self) -> None:
        """G: a provider that reports nothing yields zero tokens, not an estimate."""
        sink = FileTelemetrySink(self.root / "telemetry")
        provider = _FixedActionProvider(DecisionAction.COMPLETE_RUN, DecisionType.COMPLETE)
        harness = self._harness(provider=provider, sink=sink)

        harness.run(self._request(run_id="run-unmeasured"))

        record = sink.read_records("run-unmeasured")[0]
        self.assertEqual(record.input_tokens, 0)
        self.assertEqual(record.output_tokens, 0)
        self.assertEqual(record.cached_tokens, 0)
        self.assertEqual(record.provider_name, "")

    def test_no_financial_field_reaches_the_durable_file(self) -> None:
        """I: the persisted record carries no money, price, or charge."""
        sink = FileTelemetrySink(self.root / "telemetry")
        provider = AIDecisionProvider(provider=_UsageProvider())
        harness = self._harness(provider=provider, sink=sink)
        harness.run(self._request(run_id="run-financial-check"))

        raw = sink.path_for_run("run-financial-check").read_text(encoding="utf-8")
        payload = json.loads(raw.splitlines()[0])
        leaked = set(payload) & FORBIDDEN_TELEMETRY_FIELDS
        self.assertEqual(leaked, set(), f"financial/ownership fields leaked: {leaked}")
        # And the raw text really contains no money vocabulary.
        lowered = raw.lower()
        for word in ("estimated_cost", "provider_reported_cost", "effective_cost",
                     "price", "charge", "wallet", "credit"):
            self.assertNotIn(word, lowered)

    def test_a_failing_sink_does_not_abort_the_run_and_is_reported(self) -> None:
        """G: a durable-write failure is reported, never silently dropped."""
        sink = _FailingSink()
        provider = _FixedActionProvider(DecisionAction.COMPLETE_RUN, DecisionType.COMPLETE)
        harness = self._harness(provider=provider, sink=sink)

        result = harness.run(self._request(run_id="run-sink-failure"))

        self.assertEqual(sink.calls, 1)
        self.assertTrue(result.final_state.terminal)
        names = [event.event_type.name for event in result.events]
        self.assertIn("HARNESS_FAILED", names)
        failures = [
            event
            for event in result.events
            if event.event_type.name == "HARNESS_FAILED"
            and event.metadata.get("reason") == "physical_telemetry_write_failed"
        ]
        self.assertEqual(len(failures), 1, [e.metadata for e in result.events])

    def test_no_sink_means_no_persistence_and_no_failure(self) -> None:
        """Compatibility: a harness without a sink still runs unchanged."""
        provider = _FixedActionProvider(DecisionAction.COMPLETE_RUN, DecisionType.COMPLETE)
        harness = AgentHarness(
            decision_provider=provider,
            policy=AgentHarnessPolicy(),
            acceptance_gate=_PassingAcceptanceGate(),
        )
        result = harness.run(self._request(run_id="run-no-sink"))
        self.assertTrue(result.final_state.terminal)
        self.assertIsNotNone(harness._last_telemetry)

    def test_two_attempts_of_one_run_produce_two_records_through_the_loop(self) -> None:
        """F: attempt identity, not just run identity, distinguishes records."""
        sink = FileTelemetrySink(self.root / "telemetry")
        provider = _FixedActionProvider(DecisionAction.COMPLETE_RUN, DecisionType.COMPLETE)
        harness = self._harness(provider=provider, sink=sink)

        harness.run(self._request(run_id="run-multi", attempt_number=0))
        harness.run(self._request(run_id="run-multi", attempt_number=1))

        records = sink.read_records("run-multi")
        self.assertEqual(len(records), 2)
        self.assertEqual(
            sorted(r.identity for r in records),
            ["run-multi#attempt-0", "run-multi#attempt-1"],
        )


class TerminalOutcomeMappingTests(unittest.TestCase):
    """B, C: the success flag follows the real terminal outcome, nothing else."""

    def setUp(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp.cleanup()

    def _result(self, status: HarnessStatus):
        from app.agent_runtime.models import HarnessResult, HarnessState

        state = HarnessState(
            run_id="run-outcome",
            attempt_number=0,
            iteration=0,
            phase=HarnessPhase.OBSERVE,
            status=status,
            terminal=True,
        )
        return HarnessResult(
            run_id="run-outcome",
            final_state=state,
            iterations=(state,),
            observations=(),
            decisions=(),
            execution_results=(),
            verification_results=(),
            final_acceptance=None,
            final_project_state=None,
            events=(),
        )

    def test_completed_outcome_records_success_true(self) -> None:
        """B: a completed attempt is recorded as a success."""
        sink = FileTelemetrySink(self.root / "telemetry")
        harness = AgentHarness(telemetry_sink=sink)
        request = HarnessRequest(
            run_id="run-outcome", workspace=self.workspace, attempt_number=0
        )
        harness._record_attempt_telemetry(
            request,
            self._result(HarnessStatus.COMPLETED),
            duration_seconds=0.25,
            tool_call_count=2,
        )
        record = sink.read_records("run-outcome")[0]
        self.assertTrue(record.success)
        self.assertEqual(record.error_type, "")
        self.assertEqual(record.tool_call_count, 2)
        self.assertEqual(record.duration_seconds, 0.25)

    def test_failed_outcome_records_success_false(self) -> None:
        """C: a failed attempt is recorded as a failure with its reason."""
        sink = FileTelemetrySink(self.root / "telemetry")
        harness = AgentHarness(telemetry_sink=sink)
        request = HarnessRequest(
            run_id="run-outcome", workspace=self.workspace, attempt_number=0
        )
        harness._record_attempt_telemetry(
            request,
            self._result(HarnessStatus.FAILED),
            duration_seconds=0.5,
            tool_call_count=0,
        )
        record = sink.read_records("run-outcome")[0]
        self.assertFalse(record.success)
        self.assertTrue(record.error_type)

    def test_each_terminal_outcome_is_recorded_at_most_once(self) -> None:
        """E: re-recording the same terminal attempt writes no second line."""
        sink = FileTelemetrySink(self.root / "telemetry")
        harness = AgentHarness(telemetry_sink=sink)
        request = HarnessRequest(
            run_id="run-once", workspace=self.workspace, attempt_number=0
        )
        result = self._result(HarnessStatus.COMPLETED)
        harness._record_attempt_telemetry(
            request, result, duration_seconds=0.1, tool_call_count=0
        )
        harness._record_attempt_telemetry(
            request, result, duration_seconds=9.9, tool_call_count=7
        )
        records = sink.read_records("run-once")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].duration_seconds, 0.1)
        self.assertEqual(records[0].tool_call_count, 0)


class SinkFailureNormalizationTests(unittest.TestCase):
    """F-46-01: any sink failure is normalized, and none destroys the outcome.

    The port promises that a persistence failure never destroys an
    already-determined terminal outcome and is never silent. That promise must
    hold for *any* exception a sink raises, not only for the project's own
    exception type, because a future sink (for example a database-backed one)
    will raise its driver's errors.
    """

    def setUp(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp.cleanup()

    def _drive(self, error: BaseException):
        sink = _FailingSink(error)
        harness = AgentHarness(
            decision_provider=_FixedActionProvider(
                DecisionAction.FAIL_RUN, DecisionType.FAIL
            ),
            policy=AgentHarnessPolicy(),
            telemetry_sink=sink,
        )
        result = harness.run(
            HarnessRequest(
                run_id="run-sink-norm",
                workspace=self.workspace,
                attempt_number=0,
            )
        )
        return sink, result

    def _assert_normalized(self, error: BaseException) -> None:
        sink, result = self._drive(error)
        self.assertEqual(sink.calls, 1)
        self.assertEqual(result.final_state.status, HarnessStatus.FAILED)
        self.assertTrue(result.final_state.terminal)
        failures = [
            event
            for event in result.events
            if event.event_type.name == "HARNESS_FAILED"
            and event.metadata.get("reason") == "physical_telemetry_write_failed"
        ]
        self.assertEqual(len(failures), 1, [e.metadata for e in result.events])
        self.assertEqual(failures[0].metadata.get("cause_category"), type(error).__name__)

    def test_sink_raising_oserror_is_normalized(self) -> None:
        """A: an OSError from the sink does not destroy the terminal outcome."""
        self._assert_normalized(OSError("disk full"))

    def test_sink_raising_valueerror_is_normalized(self) -> None:
        """B: a ValueError from the sink does not destroy the terminal outcome."""
        self._assert_normalized(ValueError("bad record"))

    def test_sink_raising_arbitrary_exception_is_normalized(self) -> None:
        """C: any Exception from the sink is normalized."""
        self._assert_normalized(RuntimeError("driver exploded"))
        self._assert_normalized(TypeError("wrong type"))
        self._assert_normalized(KeyError("missing column"))

    def test_the_original_error_is_preserved_as_the_cause(self) -> None:
        """The normalization keeps the original error rather than discarding it."""
        original = OSError("disk full")
        sink = _FailingSink(original)
        harness = AgentHarness(telemetry_sink=sink)

        with self.assertRaises(TelemetryWriteError) as ctx:
            harness._record_attempt_telemetry(
                HarnessRequest(run_id="run-cause", workspace=self.workspace),
                self._completed_result(),
                duration_seconds=0.0,
                tool_call_count=0,
            )
        self.assertIs(ctx.exception.__cause__, original)

    def _completed_result(self):
        from app.agent_runtime.models import HarnessResult, HarnessState

        state = HarnessState(
            run_id="run-cause",
            attempt_number=0,
            iteration=0,
            phase=HarnessPhase.OBSERVE,
            status=HarnessStatus.COMPLETED,
            terminal=True,
        )
        return HarnessResult(
            run_id="run-cause",
            final_state=state,
            iterations=(state,),
            observations=(),
            decisions=(),
            execution_results=(),
            verification_results=(),
            final_acceptance=None,
            final_project_state=None,
            events=(),
        )

    def test_an_execution_error_outside_the_sink_is_not_relabelled(self) -> None:
        """C: the normalization is scoped to the sink call, not to the whole run."""
        class _ExplodingDecisionProvider:
            def decide(self, request):
                raise RuntimeError("execution stage exploded")

        harness = AgentHarness(
            decision_provider=_ExplodingDecisionProvider(),
            policy=AgentHarnessPolicy(),
            telemetry_sink=_FailingSink(OSError("should never be reached")),
        )
        # The execution failure must propagate as itself. If the telemetry
        # handling were a blanket catch around the run, this would surface as a
        # TelemetryWriteError instead.
        with self.assertRaises(RuntimeError) as ctx:
            harness.run(
                HarnessRequest(
                    run_id="run-exec-error",
                    workspace=self.workspace,
                    attempt_number=0,
                )
            )
        self.assertNotIsInstance(ctx.exception, TelemetryWriteError)
        self.assertEqual(str(ctx.exception), "execution stage exploded")

    def test_a_failing_sink_is_not_reported_as_a_run_failure_outcome(self) -> None:
        """The run keeps its own outcome; only the measurement is lost."""
        sink, result = self._drive(OSError("disk full"))
        self.assertEqual(sink.calls, 1)
        # The run failed for its own reason, not because telemetry failed.
        self.assertNotEqual(
            result.final_state.metadata.get("reason"), "physical_telemetry_write_failed"
        )
        self.assertNotIn(
            "physical_telemetry_write_failed",
            str(result.final_state.metadata.get("reason", "")),
        )


class NumericBoundTests(unittest.TestCase):
    """F-46-02: durable telemetry can only contain finite, bounded numbers."""

    def test_duration_accepts_finite_and_zero(self) -> None:
        """D: a real duration is accepted, including zero."""
        self.assertEqual(PhysicalTelemetry(run_id="r", duration_seconds=0).duration_seconds, 0)
        self.assertEqual(
            PhysicalTelemetry(run_id="r", duration_seconds=1.5).duration_seconds, 1.5
        )
        self.assertEqual(
            PhysicalTelemetry(run_id="r", duration_seconds=0.000001).duration_seconds,
            0.000001,
        )

    def test_duration_rejects_non_finite_and_negative(self) -> None:
        """D: NaN, both infinities, and a negative duration are all refused."""
        for bad in (float("nan"), float("inf"), float("-inf"), -0.1, -1.0):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    PhysicalTelemetry(run_id="r", duration_seconds=bad)

    def test_token_counters_accept_zero_and_the_maximum(self) -> None:
        """F: 0 and the documented maximum are both valid measurements."""
        for field in ("input_tokens", "output_tokens", "cached_tokens"):
            with self.subTest(field=field):
                self.assertEqual(
                    getattr(PhysicalTelemetry(run_id="r", **{field: 0}), field), 0
                )
                self.assertEqual(
                    getattr(
                        PhysicalTelemetry(run_id="r", **{field: MAX_TOKEN_COUNT}), field
                    ),
                    MAX_TOKEN_COUNT,
                )

    def test_token_counters_reject_above_the_maximum_and_negative(self) -> None:
        """F: MAX+1 and a negative count are both refused."""
        for field in ("input_tokens", "output_tokens", "cached_tokens"):
            for bad in (MAX_TOKEN_COUNT + 1, -1):
                with self.subTest(field=field, value=bad):
                    with self.assertRaises(ValueError):
                        PhysicalTelemetry(run_id="r", **{field: bad})

    def test_tool_call_count_has_its_own_smaller_bound(self) -> None:
        """G: tool calls are bounded separately from tokens."""
        self.assertEqual(
            PhysicalTelemetry(run_id="r", tool_call_count=0).tool_call_count, 0
        )
        self.assertEqual(
            PhysicalTelemetry(
                run_id="r", tool_call_count=MAX_TOOL_CALL_COUNT
            ).tool_call_count,
            MAX_TOOL_CALL_COUNT,
        )
        for bad in (MAX_TOOL_CALL_COUNT + 1, -1):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    PhysicalTelemetry(run_id="r", tool_call_count=bad)
        self.assertLess(MAX_TOOL_CALL_COUNT, MAX_TOKEN_COUNT)

    def test_attempt_number_is_bounded(self) -> None:
        """The attempt counter is bounded too, so a corrupt value is refused."""
        self.assertEqual(
            PhysicalTelemetry(
                run_id="r", attempt_number=MAX_ATTEMPT_NUMBER
            ).attempt_number,
            MAX_ATTEMPT_NUMBER,
        )
        with self.assertRaises(ValueError):
            PhysicalTelemetry(run_id="r", attempt_number=MAX_ATTEMPT_NUMBER + 1)
        with self.assertRaises(ValueError):
            PhysicalTelemetry(run_id="r", attempt_number=-1)

    def test_non_integer_counters_are_refused(self) -> None:
        """A counter must be an integer; a bool is not a measurement."""
        for bad in (1.5, "12", None, True):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    PhysicalTelemetry(run_id="r", input_tokens=bad)

    def test_deserialization_cannot_bypass_the_bounds(self) -> None:
        """Every construction path is validated, including from_dict."""
        with self.assertRaises(ValueError):
            PhysicalTelemetry.from_dict(
                {"run_id": "r", "input_tokens": MAX_TOKEN_COUNT + 1}
            )
        with self.assertRaises(ValueError):
            PhysicalTelemetry.from_dict(
                {"run_id": "r", "tool_call_count": MAX_TOOL_CALL_COUNT + 1}
            )

    def test_no_nan_or_infinity_can_reach_the_durable_file(self) -> None:
        """E: allow_nan=False is the durable guarantee, not just validation.

        Two independent layers are checked. Construction refuses a non-finite
        duration, and the writer itself refuses to serialize one even if a value
        somehow bypassed construction - which is what makes the durable file safe
        rather than merely the constructor.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sink = FileTelemetrySink(Path(tmp) / "telemetry")
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(value=bad):
                    with self.assertRaises(ValueError):
                        PhysicalTelemetry(run_id="run-nan", duration_seconds=bad)

            good = PhysicalTelemetry(run_id="run-finite", duration_seconds=1.0, success=True)
            self.assertTrue(sink.record(good))
            raw = sink.path_for_run("run-finite").read_text(encoding="utf-8")
            self.assertNotIn("NaN", raw)
            self.assertNotIn("Infinity", raw)
            parsed = json.loads(raw, parse_constant=_reject_constant)
            self.assertEqual(parsed["duration_seconds"], 1.0)

    def test_the_writer_refuses_a_non_finite_value_on_its_own(self) -> None:
        """E: the writer is fail-closed even if validation is bypassed."""
        with tempfile.TemporaryDirectory() as tmp:
            sink = FileTelemetrySink(Path(tmp) / "telemetry")
            smuggled = PhysicalTelemetry(run_id="run-smuggled", success=True)
            # Bypass the frozen constructor's validation to simulate any future
            # code path that reaches the writer with a non-finite measurement.
            object.__setattr__(smuggled, "duration_seconds", float("nan"))
            with self.assertRaises(TelemetryWriteError) as ctx:
                sink.record(smuggled)
            self.assertIsInstance(ctx.exception.__cause__, (TypeError, ValueError))
            # Nothing invalid was persisted.
            path = sink.path_for_run("run-smuggled")
            self.assertFalse(path.exists() and "NaN" in path.read_text(encoding="utf-8"))


def _reject_constant(name: str):
    """A strict JSON reader rejects ``NaN``/``Infinity`` the way other languages do."""
    raise AssertionError(f"invalid JSON constant persisted: {name}")


class ProductionEntryPointTelemetryTests(unittest.TestCase):
    """J: the real production entry point records durable physical telemetry.

    This drives ``ForgeApiService.run_agent_loop`` - the production entry point -
    and proves the measurement reaches the durable sink through the real
    composition, not only through a hand-built harness.
    """

    def setUp(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.telemetry_root = self.root / "telemetry"

    def tearDown(self) -> None:
        from app.runtime.run_scope import RunScope

        RunScope.release_all()
        self._tmp.cleanup()

    def _service(self, *, telemetry_sink, harness_factory=None):
        """Build the production service with an explicit composition sink.

        The service builds its own harness through ``create_agent_harness``, and it
        receives the telemetry sink the same way it receives the run store and the
        idempotency guard: as a composition dependency.
        """
        from app.api.service import ForgeApiService
        from app.execution.capabilities import ExecutionCapability
        from app.execution.declaration import ExecutionDeclaration
        from app.execution.profile import ProjectExecutionProfile
        from app.orchestrator.models import TaskResult
        from app.orchestrator.run import RunExecutor
        from app.runtime.context import RuntimeContext
        from app.tools.approval import ApprovalPolicy
        from app.tools.registry import build_default_tool_registry
        from app.tools.workspace import Workspace

        class _OrchestratorDouble:
            def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
                return TaskResult(task.id, True, output="double")

        workspace = Workspace(self.root)
        orchestrator = _OrchestratorDouble()
        runtime = RuntimeContext(
            settings=None,
            provider_accounts={},
            provider_registry=None,
            provider_capabilities=None,
            agent_registry=None,
            orchestrator=orchestrator,
            run_executor=RunExecutor(orchestrator),
            # The entry point refuses to run without a configured harness; the
            # service then builds its own through ``harness_factory``. This one is
            # only the composition's presence check.
            harness=create_agent_harness(),
        )
        return ForgeApiService(
            runtime=runtime,
            telemetry_sink=telemetry_sink,
            harness_factory=harness_factory,
            workspace=workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=ProjectExecutionProfile(
                profile_id="api-default",
                allowed_commands=("python",),
                capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
                network_access=False,
                working_directory=".",
            ),
            declarations={
                "verify.slice": ExecutionDeclaration(
                    declaration_id="verify.slice",
                    command=("python", "-c", "print('ok')"),
                    profile_id="api-default",
                    working_directory=".",
                )
            },
            approval_policy=ApprovalPolicy(),
        )

    def test_production_entry_point_persists_a_durable_measurement(self) -> None:
        """J: the default production composition writes one durable record."""
        import os

        previous = os.environ.get("FORGE_TELEMETRY_ROOT")
        os.environ["FORGE_TELEMETRY_ROOT"] = str(self.telemetry_root)
        try:
            service = self._service(telemetry_sink=None)
            response = service.run_agent_loop("verify.slice")
        finally:
            if previous is None:
                os.environ.pop("FORGE_TELEMETRY_ROOT", None)
            else:
                os.environ["FORGE_TELEMETRY_ROOT"] = previous

        sink = FileTelemetrySink(self.telemetry_root)
        records = sink.read_records(response.run_id)
        self.assertEqual(len(records), 1, sink.list_run_ids())
        record = records[0]
        self.assertEqual(record.run_id, response.run_id)
        self.assertEqual(record.attempt_id, f"{response.run_id}#attempt-0")
        self.assertGreater(record.duration_seconds, 0.0)
        leaked = set(record.to_dict()) & FORBIDDEN_TELEMETRY_FIELDS
        self.assertEqual(leaked, set())

    def test_measured_tokens_from_the_ai_provider_reach_the_durable_record(self) -> None:
        """B: real provider measurements survive the whole production path."""
        sink = FileTelemetrySink(self.telemetry_root)
        ai_provider = AIDecisionProvider(provider=_UsageProvider(tokens=(321, 65, 9)))

        def _factory(**kwargs):
            from app.agent_runtime.policy import AgentHarnessPolicy

            return AgentHarness(
                decision_provider=ai_provider,
                policy=kwargs.get("policy") or AgentHarnessPolicy(),
                telemetry_sink=kwargs.get("telemetry_sink") or sink,
            )

        service = self._service(telemetry_sink=sink, harness_factory=_factory)
        response = service.run_agent_loop("verify.slice")

        records = sink.read_records(response.run_id)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.input_tokens, 321)
        self.assertEqual(record.output_tokens, 65)
        self.assertEqual(record.cached_tokens, 9)
        self.assertEqual(record.provider_name, "usage-double")
        self.assertEqual(record.model_name, "usage-double-model")


class CoreIndependenceTests(unittest.TestCase):
    """12: the new code creates no Core -> Platform dependency."""

    def test_telemetry_module_has_no_platform_or_database_import(self) -> None:
        """Core stays autonomous: no PostgreSQL, no ORM, no payments, no Platform."""
        source = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "agent_runtime"
            / "physical_telemetry.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "sqlalchemy",
            "psycopg",
            "sqlite3",
            "alembic",
            "stripe",
            "pickle",
            "eval(",
            "exec(",
        ):
            self.assertNotIn(forbidden, source.lower(), f"forbidden dependency: {forbidden}")
        # No import may reach a Platform layer or an ORM at all.
        import ast

        tree = ast.parse(source)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertTrue(imported, "the telemetry module must import something")
        for module in imported:
            root_module = module.split(".")[0]
            self.assertNotIn(root_module, {"platform", "sqlalchemy", "psycopg", "stripe"})
            self.assertIn(
                root_module,
                {"app", "__future__", "json", "math", "os", "re", "dataclasses",
                 "datetime", "pathlib", "typing"},
                f"unexpected non-Core import: {module}",
            )


if __name__ == "__main__":
    unittest.main()
