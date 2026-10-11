"""Change impact, evidence-graph, verification-planning and repair-loop analysis.

Block #9. Every module in this package is a **read-mostly projection over records
that already exist**. None of them is a second memory system, a second fact
registry, a second topology graph, or a second execution mechanism:

* ``impact.py`` extends the Block #8 change-impact report (``app/api/routes.py``
  ``GET /api/product/change-impact``) rather than adding a parallel route. It
  compares the scanner's own ``SnapshotFile.fingerprint`` values and the
  understanding layer's ``StructuralFact`` records;
* ``evidence.py`` builds a traceability view out of the identifiers that already
  exist -- ``MemoryRecord.memory_id`` (the goal and the requirements),
  ``AcceptanceCriterion.criterion_id``, ``StepRecord.step_id`` /
  ``step_run_id`` / ``request_id`` / ``action_fingerprint``,
  ``RunIdempotencyGuard``'s ledger, ``SnapshotFile.relative_path`` and
  ``StepRecord.execution_exit_code`` / ``verification_status``;
* ``verification.py`` chooses checks from changes and the deterministic
  dependency data the scanner already produced. It calls no model and guesses no
  dependency;
* ``repair.py`` is a **bounded decision loop**. It dispatches nothing itself: the
  one action it may ask for is an operator-declared verification, through the
  existing ``ForgeApiService`` entry point, and only when the existing
  ``RunIdempotencyGuard.repeat_decision`` says repeating is permitted.

Nothing here grants authority. Every dispatch still passes ``RunScope`` ->
``ApprovalPolicy`` -> ``ExecutionCoordinator`` -> ``AuthorizedExecution``.
"""

from __future__ import annotations

__all__ = [
    "impact",
    "evidence",
    "verification",
    "repair",
]
