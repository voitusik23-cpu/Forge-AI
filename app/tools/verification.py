"""Read-only verification of explicit workspace file expectations."""

from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable
from uuid import uuid4

from app.orchestrator.models import EventType
from app.tools.workspace import Workspace, WorkspacePathError


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    DENIED = "denied"


@dataclass(frozen=True)
class VerificationExpectation:
    relative_path: str
    exists: bool
    sha256: str | None = None


@dataclass(frozen=True)
class VerificationResult:
    verification_id: str
    status: VerificationStatus
    code: str
    relative_path: str | None = None
    fingerprint: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


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
                or (expectation.sha256 is not None and (
                    not expectation.exists
                    or not isinstance(expectation.sha256, str)
                    or re.fullmatch(r"[0-9a-fA-F]{64}", expectation.sha256) is None
                ))
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
                # Re-check the root and every path component immediately before opening.
                target = workspace.verify_target(parts)
                digest = hashlib.sha256()
                size = 0
                with target.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                        size += len(chunk)
                # Detect replacement/link changes observed after the read as well.
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

        result = VerificationResult(
            verification_id=identifier,
            status=status,
            code=code,
            relative_path=normalized,
            fingerprint=fingerprint,
            metadata=metadata,
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
        event_data.update(metadata)
        observer(EventType.VERIFICATION_COMPLETED, event_data)
        return result
