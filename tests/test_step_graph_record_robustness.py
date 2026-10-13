"""A damaged step-graph record is refused, never raised, never guessed at.

The documented contract in ``read_step_graph_record`` is explicit: it returns
``(None, reason)`` when what is stored "cannot be trusted", so a caller can tell
"no attempt was ever recorded" from "an attempt was recorded in a form this build
refuses" -- **without catching an exception**, and "a recovery scan must not treat a
corrupt record as 'nothing was running', and it must not crash on one either".

A malformed *numeric* field broke that contract. ``int(\"not-a-number\")`` inside
``StepGraphSnapshot.from_dict``/``StepRecord.from_dict`` raises a plain ``ValueError``,
while the reader caught only ``StepGraphStateError``, so the exception escaped the
reader and aborted every consumer of it -- including the whole recovery scan, which
iterates records with no ``try`` of its own.

RED BEFORE THE FIX: tests 01, 02 and 03 fail (the plain ``ValueError`` escapes).
"""

from __future__ import annotations

import unittest

from app.workflows.step_graph import (
    STEP_GRAPH_PROJECT_STATE_KEY,
    STEP_GRAPH_SCHEMA,
    StepGraphStateError,
    graph_from_declared_plan,
    read_step_graph_record,
)

DECLARATION_ID = "robustness-declaration"


def _record(**step_overrides: object) -> dict:
    step = {"step_id": "a", "status": "SUCCEEDED"}
    step.update(step_overrides)
    return {
        STEP_GRAPH_PROJECT_STATE_KEY: {
            "schema": STEP_GRAPH_SCHEMA,
            "graph_id": "robustness-graph",
            "graph_run_id": "robustness-run",
            "status": "COMPLETED",
            "steps": [step],
            "declared_plan": [],
        }
    }


class CorruptRecordIsRefusedNotRaisedTests(unittest.TestCase):
    def test_01_a_non_numeric_attempt_count_is_refused(self) -> None:
        record, reason = read_step_graph_record(_record(attempts="not-a-number"))

        self.assertIsNone(record)
        self.assertIn("untrustworthy_step_graph_record", reason)

    def test_02_a_non_numeric_exit_code_is_refused(self) -> None:
        record, reason = read_step_graph_record(_record(execution_exit_code="zero"))

        self.assertIsNone(record)
        self.assertIn("untrustworthy_step_graph_record", reason)

    def test_03_a_corrupt_declared_plan_is_refused_by_the_graph_rebuilder(self) -> None:
        """The rebuilder documents ``StepGraphStateError``; it must not raise ValueError."""

        plan = [
            {
                "step_id": "a",
                "action": {"declaration_id": DECLARATION_ID},
                "verification": {"kind": "EXIT_CODE", "expected_exit_code": "zero"},
            }
        ]
        with self.assertRaises(StepGraphStateError):
            graph_from_declared_plan("robustness-graph", plan)

    def test_04_a_sound_record_is_still_read(self) -> None:
        record, reason = read_step_graph_record(_record())

        self.assertIsNotNone(record, reason)
        self.assertEqual(reason, "")
        self.assertEqual(record.graph_run_id, "robustness-run")

    def test_05_an_unsupported_schema_is_refused_as_before(self) -> None:
        payload = _record()[STEP_GRAPH_PROJECT_STATE_KEY]
        payload["schema"] = "some-other-schema"

        record, reason = read_step_graph_record({STEP_GRAPH_PROJECT_STATE_KEY: payload})

        self.assertIsNone(record)
        self.assertIn("untrustworthy_step_graph_record", reason)


if __name__ == "__main__":
    unittest.main()
