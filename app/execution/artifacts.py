"""Artifact domain models and deterministic hashing helpers for Forge Execution Plane."""

from __future__ import annotations

import hashlib
import mimetypes
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_MAX_ARTIFACT_BYTES: int = 10 * 1024 * 1024  # 10 MB


@dataclass(frozen=True)
class Artifact:
    """Declared durable work product generated during an execution run."""

    artifact_id: str
    run_id: str
    relative_path: str
    media_type: str
    size_bytes: int
    sha256: str
    provenance_source: str
    created_at: str
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.metadata, dict):
            object.__setattr__(self, "metadata", dict(self.metadata))


def create_artifact_from_file(
    file_path: Path,
    *,
    relative_path: str,
    run_id: str,
    provenance_source: str = "execution",
    artifact_id: str | None = None,
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    metadata: Mapping[str, object] | None = None,
) -> Artifact:
    """Create a verified, hashed Artifact from a disk file.

    Enforces:
    - Path safety / relative path canonicalization
    - Existence and regular file check
    - Reject symlinks escaping workspace or symlink targets
    - Enforce artifact size bounds
    - Deterministic SHA-256 calculation
    - Deterministic media type detection
    """
    clean_rel = relative_path.replace("\\", "/").strip("/")
    if not clean_rel or ".." in clean_rel.split("/"):
        raise ValueError(f"Invalid artifact relative path: {relative_path}")

    if not file_path.is_file():
        raise FileNotFoundError(f"Artifact file not found: {file_path}")

    if file_path.is_symlink():
        raise ValueError(f"Symlinked artifact targets are not permitted: {file_path}")

    size_bytes = file_path.stat().st_size
    if size_bytes > max_bytes:
        raise ValueError(
            f"Artifact size {size_bytes} bytes exceeds maximum allowed limit {max_bytes} bytes"
        )

    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    digest = hasher.hexdigest()

    guessed_type, _ = mimetypes.guess_type(str(file_path))
    media_type = guessed_type or "application/octet-stream"

    now_iso = datetime.now(timezone.utc).isoformat()
    art_id = artifact_id or f"art-{digest[:16]}"

    return Artifact(
        artifact_id=art_id,
        run_id=run_id,
        relative_path=clean_rel,
        media_type=media_type,
        size_bytes=size_bytes,
        sha256=digest,
        provenance_source=provenance_source,
        created_at=now_iso,
        metadata=dict(metadata or {}),
    )
