"""Deterministic, non-authoritative verification for a production run.

``FrozenCriteriaVerifier`` turns a frozen set of server-side verification
expectations into ``VerificationResult`` values. It verifies workspace facts
only: it never executes anything, never chooses a command, and never receives an
executable, an argv, an environment, a timeout, a profile, or a ``RunScope``.

Its input is a plain ``Mapping[str, VerificationExpectation]`` rather than an
``AcceptanceSpec`` so that it can also serve the existing ``AgentHarness``
verification action, which already passes exactly that mapping. The mapping is
supplied by trusted composition in both cases.

Error handling is deliberately explicit and never optimistic: a verifier that
cannot complete its work reports ``VerificationStatus.ERROR``, which the
acceptance gate treats as a failure rather than a pass.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.orchestrator.models import EventType
from app.tools.verification import (
    VerificationExpectation,
    VerificationResult,
    VerificationStatus,
    WorkspaceVerifier,
)


class FrozenCriteriaVerifier:
    """Evaluate frozen server-side expectations against a workspace."""

    def __init__(self, verifier: WorkspaceVerifier | None = None) -> None:
        self._verifier = verifier or WorkspaceVerifier()

    def verify_all(
        self,
        expectations: Mapping[str, VerificationExpectation],
        *,
        workspace: object | None,
        run_id: str,
        observer: Callable[[EventType, Mapping[str, object]], None],
        execution_result_id: str | None = None,
    ) -> tuple[VerificationResult, ...]:
        """Verify every expectation and return one result per criterion.

        The caller owns the expectations; this method can neither add, drop, nor
        replace one. An unusable expectation set yields no results at all, so the
        acceptance gate reports ``verification_missing`` rather than a pass.
        """
        if not isinstance(expectations, Mapping) or not expectations:
            return ()

        results: list[VerificationResult] = []
        for criterion_id in sorted(expectations):
            expectation = expectations[criterion_id]
            results.append(
                self._verify_one(
                    expectation,
                    criterion_id=criterion_id,
                    workspace=workspace,
                    run_id=run_id,
                    observer=observer,
                    execution_result_id=execution_result_id,
                )
            )
        return tuple(results)

    def _verify_one(
        self,
        expectation: object,
        *,
        criterion_id: str,
        workspace: object | None,
        run_id: str,
        observer: Callable[[EventType, Mapping[str, object]], None],
        execution_result_id: str | None,
    ) -> VerificationResult:
        try:
            result = self._verifier.verify(
                expectation,
                workspace=workspace,
                run_id=run_id,
                observer=lambda event_type, data: observer(event_type, data),
                criterion_id=criterion_id,
            )
        except Exception:  # noqa: BLE001 - a broken verifier must not pass
            # Fail closed: an exception is an ERROR, never a PASS.
            result = VerificationResult(
                verification_id=f"verification-error-{criterion_id}",
                status=VerificationStatus.ERROR,
                code="verifier_error",
                criterion_id=criterion_id,
                execution_result_id=execution_result_id,
            )
            observer(
                EventType.VERIFICATION_COMPLETED,
                {
                    "run_id": run_id,
                    "verification_id": result.verification_id,
                    "criterion_id": criterion_id,
                    "status": result.status.value,
                    "code": result.code,
                },
            )
        if execution_result_id and result.execution_result_id is None:
            # Keep the verification attributable to the execution it checked.
            result = VerificationResult(
                verification_id=result.verification_id,
                status=result.status,
                code=result.code,
                relative_path=result.relative_path,
                fingerprint=result.fingerprint,
                metadata=result.metadata,
                execution_result_id=execution_result_id,
                criterion_id=result.criterion_id,
                evidence=result.evidence,
            )
        return result
