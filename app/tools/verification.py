"""Read-only verification of explicit workspace file expectations and execution evidence."""

from __future__ import annotations

import hashlib
import re
import stat
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from app.execution.request import (
    ExecutionOutcomeStatus,
    ExecutionResult,
    ExecutionStatus,
)
from app.orchestrator.models import EventType
from app.tools.workspace import Workspace, WorkspacePathError


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    DENIED = "denied"
    ERROR = "error"
    NOT_RUN = "not_run"


class VerificationType(str, Enum):
    """Supported types of objective verification."""

    PROCESS_EXIT = "process_exit"
    COMMAND_EXECUTION = "command_execution"
    WORKSPACE_FILE = "workspace_file"
    CUSTOM = "custom"


@dataclass(frozen=True)
class VerificationExpectation:
    relative_path: str
    exists: bool
    sha256: str | None = None


@dataclass(frozen=True)
class VerificationRequest:
    """Explicit request to verify an acceptance criterion using an objective check."""

    verification_id: str
    criterion_id: str
    verification_type: VerificationType | str = VerificationType.PROCESS_EXIT
    execution_request_id: str | None = None
    expected_exit_code: int | None = 0
    expected_check: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.expected_check, dict):
            object.__setattr__(self, "expected_check", dict(self.expected_check))
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def validate(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not isinstance(self.verification_id, str) or not self.verification_id.strip():
            errors.append("verification_id_required")
        if not isinstance(self.criterion_id, str) or not self.criterion_id.strip():
            errors.append("criterion_id_required")
        if self.expected_exit_code is not None and not isinstance(self.expected_exit_code, int):
            errors.append("invalid_expected_exit_code")
        return tuple(errors)


@dataclass(frozen=True)
class VerificationEvidence:
    """Objective, structured, audit-grade verification evidence."""

    evidence_id: str
    verification_id: str
    source: str
    execution_result_id: str | None = None
    outcome: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "verification_id": self.verification_id,
            "source": self.source,
            "execution_result_id": self.execution_result_id,
            "outcome": self.outcome,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a verification request."""

    verification_id: str
    status: VerificationStatus
    code: str
    relative_path: str | None = None
    fingerprint: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    execution_result_id: str | None = None
    criterion_id: str | None = None
    evidence: VerificationEvidence | None = None

    def __post_init__(self) -> None:
        if self.evidence is not None and self.execution_result_id is None:
            object.__setattr__(self, "execution_result_id", self.evidence.execution_result_id)
        if (
            self.evidence is not None
            and self.criterion_id is None
            and "criterion_id" in self.evidence.metadata
        ):
            object.__setattr__(self, "criterion_id", str(self.evidence.metadata["criterion_id"]))


def create_execution_verification_evidence(
    criterion_id: str,
    execution_result: ExecutionResult,
) -> dict[str, object]:
    """Minimal integration point associating execution outcome with criterion verification."""
    return {
        "criterion_id": criterion_id,
        "execution_request_id": execution_result.request_id,
        "execution_result_id": execution_result.request_id,
        "execution_status": execution_result.status.value,
        "outcome_status": execution_result.outcome_status.value
        if execution_result.outcome_status
        else execution_result.status.value,
        "exit_code": execution_result.exit_code,
        "duration_seconds": execution_result.duration_seconds,
        "truncated": execution_result.truncated,
    }


class VerificationEvaluator:
    """Deterministic evaluation of VerificationRequest against ExecutionResult.

    Does NOT run processes or rely on LLM opinions; strictly inspects objective execution data.
    """

    def evaluate_execution(
        self,
        request: VerificationRequest,
        execution_result: Optional[ExecutionResult],
        *,
        run_id: str = "",
        observer: Optional[Callable[[EventType, dict[str, object]], None]] = None,
    ) -> VerificationResult:
        """Evaluate an execution result against a verification request."""
        if observer is not None:
            v_type_val = (
                request.verification_type.value
                if hasattr(request.verification_type, "value")
                else str(request.verification_type)
            )
            observer(
                EventType.VERIFICATION_REQUESTED,
                {
                    "run_id": run_id,
                    "verification_id": request.verification_id,
                    "criterion_id": request.criterion_id,
                    "execution_request_id": request.execution_request_id,
                    "verification_type": v_type_val,
                },
            )

        req_errors = request.validate()
        if req_errors:
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id or "invalid",
                source="evaluator",
                outcome="INVALID_REQUEST",
                metadata={"errors": list(req_errors), "criterion_id": request.criterion_id},
            )
            res = VerificationResult(
                verification_id=request.verification_id or "invalid",
                status=VerificationStatus.ERROR,
                code="invalid_verification_request",
                criterion_id=request.criterion_id,
                evidence=evidence,
                metadata={"errors": list(req_errors)},
            )
            self._emit_completed(observer, run_id, res)
            return res

        if execution_result is None:
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id,
                source="evaluator",
                execution_result_id=request.execution_request_id,
                outcome="MISSING",
                metadata={"reason": "execution_result_missing", "criterion_id": request.criterion_id},
            )
            res = VerificationResult(
                verification_id=request.verification_id,
                status=VerificationStatus.NOT_RUN,
                code="execution_missing",
                criterion_id=request.criterion_id,
                execution_result_id=request.execution_request_id,
                evidence=evidence,
                metadata={"reason": "execution_result_missing"},
            )
            self._emit_completed(observer, run_id, res)
            return res

        # Check timeout
        if (
            execution_result.status == ExecutionStatus.TIMEOUT
            or (
                execution_result.outcome_status
                and execution_result.outcome_status == ExecutionOutcomeStatus.EXECUTION_TIMEOUT
            )
        ):
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id,
                source="execution",
                execution_result_id=execution_result.request_id,
                outcome="TIMEOUT",
                metadata={
                    "duration_seconds": execution_result.duration_seconds,
                    "timeout": True,
                    "criterion_id": request.criterion_id,
                },
            )
            res = VerificationResult(
                verification_id=request.verification_id,
                status=VerificationStatus.ERROR,
                code="execution_timeout",
                criterion_id=request.criterion_id,
                execution_result_id=execution_result.request_id,
                evidence=evidence,
                metadata={"duration_seconds": execution_result.duration_seconds, "timeout": True},
            )
            self._emit_completed(observer, run_id, res)
            return res

        # Check execution error
        if (
            execution_result.status == ExecutionStatus.ERROR
            or (
                execution_result.outcome_status
                and execution_result.outcome_status == ExecutionOutcomeStatus.EXECUTION_ERROR
            )
        ):
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id,
                source="execution",
                execution_result_id=execution_result.request_id,
                outcome="ERROR",
                metadata={
                    "status": execution_result.status.value,
                    "duration_seconds": execution_result.duration_seconds,
                    "criterion_id": request.criterion_id,
                },
            )
            res = VerificationResult(
                verification_id=request.verification_id,
                status=VerificationStatus.ERROR,
                code="execution_error",
                criterion_id=request.criterion_id,
                execution_result_id=execution_result.request_id,
                evidence=evidence,
                metadata={"status": execution_result.status.value},
            )
            self._emit_completed(observer, run_id, res)
            return res

        # Check denied
        if execution_result.status == ExecutionStatus.DENIED:
            reason = (
                execution_result.outcome_status.value
                if execution_result.outcome_status
                else "DENIED"
            )
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id,
                source="execution",
                execution_result_id=execution_result.request_id,
                outcome="DENIED",
                metadata={"denial_reason": reason, "criterion_id": request.criterion_id},
            )
            res = VerificationResult(
                verification_id=request.verification_id,
                status=VerificationStatus.DENIED,
                code=f"execution_denied_{reason.lower()}",
                criterion_id=request.criterion_id,
                execution_result_id=execution_result.request_id,
                evidence=evidence,
                metadata={"denial_reason": reason},
            )
            self._emit_completed(observer, run_id, res)
            return res

        # Check failure or exit code mismatch
        if (
            execution_result.status == ExecutionStatus.FAILURE
            or (
                request.expected_exit_code is not None
                and execution_result.exit_code != request.expected_exit_code
            )
        ):
            code = (
                "exit_code_mismatch"
                if execution_result.exit_code != request.expected_exit_code
                else "execution_failure"
            )
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id,
                source="execution",
                execution_result_id=execution_result.request_id,
                outcome="FAILURE",
                metadata={
                    "exit_code": execution_result.exit_code,
                    "expected_exit_code": request.expected_exit_code,
                    "duration_seconds": execution_result.duration_seconds,
                    "criterion_id": request.criterion_id,
                },
            )
            res = VerificationResult(
                verification_id=request.verification_id,
                status=VerificationStatus.FAIL,
                code=code,
                criterion_id=request.criterion_id,
                execution_result_id=execution_result.request_id,
                evidence=evidence,
                metadata={
                    "exit_code": execution_result.exit_code,
                    "expected_exit_code": request.expected_exit_code,
                },
            )
            self._emit_completed(observer, run_id, res)
            return res

        # Optional checks from expected_check
        if request.expected_check.get("disallow_truncated") and execution_result.truncated:
            evidence = VerificationEvidence(
                evidence_id=str(uuid4()),
                verification_id=request.verification_id,
                source="execution",
                execution_result_id=execution_result.request_id,
                outcome="FAILURE",
                metadata={"truncated": True, "criterion_id": request.criterion_id},
            )
            res = VerificationResult(
                verification_id=request.verification_id,
                status=VerificationStatus.FAIL,
                code="output_truncated",
                criterion_id=request.criterion_id,
                execution_result_id=execution_result.request_id,
                evidence=evidence,
                metadata={"truncated": True},
            )
            self._emit_completed(observer, run_id, res)
            return res

        # All checks passed
        evidence = VerificationEvidence(
            evidence_id=str(uuid4()),
            verification_id=request.verification_id,
            source="execution",
            execution_result_id=execution_result.request_id,
            outcome="SUCCESS",
            metadata={
                "exit_code": execution_result.exit_code,
                "duration_seconds": execution_result.duration_seconds,
                "criterion_id": request.criterion_id,
            },
        )
        res = VerificationResult(
            verification_id=request.verification_id,
            status=VerificationStatus.PASS,
            code="execution_matched",
            criterion_id=request.criterion_id,
            execution_result_id=execution_result.request_id,
            evidence=evidence,
            metadata={
                "exit_code": execution_result.exit_code,
                "duration_seconds": execution_result.duration_seconds,
            },
        )
        self._emit_completed(observer, run_id, res)
        return res

    @staticmethod
    def _emit_completed(
        observer: Optional[Callable[[EventType, dict[str, object]], None]],
        run_id: str,
        result: VerificationResult,
    ) -> None:
        if observer is not None:
            observer(
                EventType.VERIFICATION_COMPLETED,
                {
                    "run_id": run_id,
                    "verification_id": result.verification_id,
                    "criterion_id": result.criterion_id,
                    "execution_result_id": result.execution_result_id,
                    "status": result.status.value,
                    "code": result.code,
                },
            )


@dataclass(frozen=True)
class TraceabilityValidationResult:
    """Outcome of validating requirement -> criterion -> verification -> evidence trace."""

    valid: bool
    errors: tuple[str, ...]
    traceability_map: Mapping[str, dict[str, object]] = field(default_factory=dict)


def validate_verification_traceability(
    *,
    requirements: Iterable[Any],
    criteria: Iterable[Any],
    verifications: Iterable[VerificationResult],
    execution_results: Optional[Iterable[ExecutionResult]] = None,
    criterion_verification_map: Optional[Mapping[str, str]] = None,
) -> TraceabilityValidationResult:
    """Deterministic validation against missing or broken traceability links."""
    errors: list[str] = []

    # 1. Map requirements
    req_ids: set[str] = set()
    for r in requirements:
        rid = getattr(r, "requirement_id", None)
        if rid and isinstance(rid, str) and rid.strip():
            req_ids.add(rid)

    # 2. Map criteria and check requirement linkage
    criteria_by_id: dict[str, Any] = {}
    for c in criteria:
        cid = getattr(c, "criterion_id", None)
        if not cid:
            continue
        if cid in criteria_by_id:
            errors.append(f"duplicate_criterion_id:{cid}")
        criteria_by_id[cid] = c
        c_req = getattr(c, "requirement_id", "")
        if c_req and c_req not in req_ids:
            errors.append(f"unknown_requirement:{c_req}")

    # 3. Check verifications
    seen_vids: set[str] = set()
    verifications_by_id: dict[str, VerificationResult] = {}
    for v in verifications:
        if not isinstance(v, VerificationResult):
            continue
        if v.verification_id in seen_vids:
            errors.append(f"duplicate_verification_id:{v.verification_id}")
        seen_vids.add(v.verification_id)
        verifications_by_id[v.verification_id] = v

        # Check missing evidence
        if v.evidence is None:
            errors.append(f"missing_evidence:{v.verification_id}")

        # Check criterion mapping
        crit_id = v.criterion_id
        if crit_id is None and criterion_verification_map:
            for c_id, v_id in criterion_verification_map.items():
                if v_id == v.verification_id:
                    crit_id = c_id
                    break

        if crit_id is None:
            errors.append(f"orphan_verification:{v.verification_id}")
        elif crit_id not in criteria_by_id:
            errors.append(f"unknown_criterion:{crit_id}")
            errors.append(f"orphan_verification:{v.verification_id}")
        else:
            # Check if explicit map conflicts with verification's criterion_id
            if criterion_verification_map:
                for c_id, v_id in criterion_verification_map.items():
                    if v_id == v.verification_id and c_id != crit_id:
                        errors.append(
                            f"verification_referencing_wrong_criterion:{v.verification_id}"
                        )

    # 4. Check execution results if provided
    if execution_results is not None:
        exec_ids = {e.request_id for e in execution_results if hasattr(e, "request_id")}
        for v in verifications:
            if v.execution_result_id and v.execution_result_id not in exec_ids:
                errors.append(f"unknown_execution_result:{v.execution_result_id}")

    # Build traceability map
    trace_map: dict[str, dict[str, object]] = {}
    for rid in req_ids:
        crit_list = [
            cid for cid, c in criteria_by_id.items() if getattr(c, "requirement_id", "") == rid
        ]
        trace_map[rid] = {
            "criteria": crit_list,
            "verifications": [
                v.verification_id
                for v in verifications_by_id.values()
                if v.criterion_id in crit_list
            ],
        }

    return TraceabilityValidationResult(
        valid=len(errors) == 0,
        errors=tuple(errors),
        traceability_map=trace_map,
    )


class WorkspaceVerifier:
    """Compare one explicit file expectation with the current workspace state."""

    def verify(
        self,
        expectation: VerificationExpectation,
        *,
        workspace: Workspace | None,
        run_id: str,
        observer: Callable[[EventType, dict[str, object]], None],
        verification_id: str | None = None,
        criterion_id: str | None = None,
    ) -> VerificationResult:
        identifier = verification_id or str(uuid4())
        normalized: str | None = None
        fingerprint: str | None = None
        metadata: dict[str, object] = {}
        status, code = VerificationStatus.DENIED, "invalid_expectation"
        try:
            if (
                not isinstance(expectation, VerificationExpectation)
                or not isinstance(expectation.exists, bool)
                or (
                    expectation.sha256 is not None
                    and (
                        not expectation.exists
                        or not isinstance(expectation.sha256, str)
                        or re.fullmatch(r"[0-9a-fA-F]{64}", expectation.sha256) is None
                    )
                )
            ):
                raise ValueError("invalid expectation")
            if workspace is None:
                raise WorkspacePathError("workspace root is unavailable")
            target, normalized, parts = workspace.resolve_target(expectation.relative_path)
            target = workspace.verify_target(parts)
            try:
                info = target.lstat()
            except FileNotFoundError:
                info = None
            except OSError as exc:
                raise WorkspacePathError("target could not be checked") from exc

            if info is None:
                status, code = (
                    (VerificationStatus.PASS, "expected_absent")
                    if not expectation.exists
                    else (VerificationStatus.FAIL, "expected_file_missing")
                )
                metadata["actual_exists"] = False
            elif not expectation.exists:
                status, code = VerificationStatus.FAIL, "unexpected_file_present"
                metadata["actual_exists"] = True
            elif not stat.S_ISREG(info.st_mode):
                status, code = VerificationStatus.FAIL, "target_not_regular_file"
                metadata["actual_exists"] = True
            else:
                target = workspace.verify_target(parts)
                digest = hashlib.sha256()
                size = 0
                with target.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                        size += len(chunk)
                workspace.verify_target(parts)
                fingerprint = digest.hexdigest()
                metadata.update(actual_exists=True, size_bytes=size)
                if expectation.sha256 is None or fingerprint.lower() == expectation.sha256.lower():
                    status, code = VerificationStatus.PASS, "expectation_matched"
                else:
                    status, code = VerificationStatus.FAIL, "content_hash_mismatch"
        except WorkspacePathError:
            status, code = VerificationStatus.DENIED, "workspace_or_path_rejected"
        except ValueError:
            status, code = VerificationStatus.DENIED, "invalid_expectation"
        except OSError:
            status, code = VerificationStatus.DENIED, "file_read_failed"

        evidence = VerificationEvidence(
            evidence_id=str(uuid4()),
            verification_id=identifier,
            source="workspace_file",
            outcome=status.value,
            metadata={**metadata, "code": code, "criterion_id": criterion_id},
        )

        result = VerificationResult(
            verification_id=identifier,
            status=status,
            code=code,
            relative_path=normalized,
            fingerprint=fingerprint,
            metadata=metadata,
            criterion_id=criterion_id,
            evidence=evidence,
        )
        event_data: dict[str, object] = {
            "run_id": run_id,
            "verification_id": identifier,
            "status": status.value,
            "code": code,
        }
        if normalized is not None:
            event_data["relative_path"] = normalized
        if fingerprint is not None:
            event_data["fingerprint"] = fingerprint
        if criterion_id is not None:
            event_data["criterion_id"] = criterion_id
        event_data.update(metadata)
        observer(EventType.VERIFICATION_COMPLETED, event_data)
        return result
