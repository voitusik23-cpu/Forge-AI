"""Ephemeral workspace manager enforcing COPY staging, path boundaries, and deterministic cleanup."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from types import TracebackType

from app.execution.artifacts import (
    DEFAULT_MAX_ARTIFACT_BYTES,
    Artifact,
    create_artifact_from_file,
)


class EphemeralWorkspaceManager:
    """Manages an isolated temporary scratch workspace for a single execution run."""

    def __init__(
        self,
        source_workspace_root: Path | None = None,
        *,
        base_scratch_dir: Path | None = None,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        self._source_root = source_workspace_root.resolve() if source_workspace_root is not None else None
        self._base_scratch_dir = base_scratch_dir.resolve() if base_scratch_dir is not None else None
        self._max_artifact_bytes = max_artifact_bytes
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._scratch_root: Path | None = None
        self._cleaned: bool = False

    @property
    def scratch_root(self) -> Path:
        """The active ephemeral scratch directory root."""
        if self._scratch_root is None:
            raise RuntimeError("Ephemeral workspace has not been initialized. Call initialize() or use context manager.")
        return self._scratch_root

    @property
    def source_root(self) -> Path | None:
        """The source workspace root, if any."""
        return self._source_root

    def initialize(self) -> Path:
        """Allocate the ephemeral scratch directory and stage source files using COPY semantics."""
        if self._scratch_root is not None:
            return self._scratch_root

        dir_kwargs: dict[str, object] = {"prefix": "forge_scratch_"}
        if self._base_scratch_dir is not None:
            self._base_scratch_dir.mkdir(parents=True, exist_ok=True)
            dir_kwargs["dir"] = str(self._base_scratch_dir)

        self._temp_dir = tempfile.TemporaryDirectory(**dir_kwargs)
        self._scratch_root = Path(self._temp_dir.name).resolve()
        self._cleaned = False

        if self._source_root is not None and self._source_root.is_dir():
            self._stage_inputs_copy(self._source_root, self._scratch_root)

        return self._scratch_root

    def resolve_path(self, relative_path: str) -> Path:
        """Resolve a path relative to the scratch root and verify it does not escape."""
        root = self.scratch_root
        norm = relative_path.replace("\\", "/").strip()
        if not norm or norm == ".":
            return root

        parts = norm.split("/")
        if ".." in parts or norm.startswith("/"):
            raise ValueError(f"Path traversal detected: {relative_path}")

        resolved = (root / norm).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Resolved path escapes ephemeral workspace root: {relative_path}") from exc

        return resolved

    def harvest_artifacts(
        self,
        artifact_targets: Sequence[str],
        *,
        run_id: str,
        provenance_source: str = "execution",
    ) -> tuple[Artifact, ...]:
        """Harvest declared artifact targets from the scratch root before cleanup.

        Enforces:
        - Relative path safety / no directory traversal (..)
        - Symlink checks (symlinks are rejected)
        - Artifact size bounds
        - Cryptographic hashing (SHA-256)
        """
        root = self.scratch_root
        harvested: list[Artifact] = []

        for target in artifact_targets:
            if not isinstance(target, str) or not target.strip():
                raise ValueError("Artifact target must be a non-empty string")

            clean_target = target.replace("\\", "/").strip("/")
            if not clean_target or ".." in clean_target.split("/"):
                raise ValueError(f"Unsafe artifact target path: {target}")

            resolved_path = self.resolve_path(clean_target)

            if not resolved_path.exists():
                continue

            if resolved_path.is_symlink():
                raise ValueError(f"Artifact target cannot be a symlink: {clean_target}")

            if not resolved_path.is_file():
                continue

            art = create_artifact_from_file(
                resolved_path,
                relative_path=clean_target,
                run_id=run_id,
                provenance_source=provenance_source,
                max_bytes=self._max_artifact_bytes,
            )
            harvested.append(art)

        return tuple(harvested)

    def cleanup(self) -> None:
        """Deterministically clean up the ephemeral workspace with bounded Windows retry."""
        if self._cleaned or self._scratch_root is None:
            return

        scratch_path = self._scratch_root
        temp_dir = self._temp_dir

        self._scratch_root = None
        self._temp_dir = None
        self._cleaned = True

        if temp_dir is not None:
            max_retries = 5
            for attempt in range(max_retries):
                try:
                    temp_dir.cleanup()
                    break
                except (PermissionError, OSError):
                    if attempt == max_retries - 1:
                        if scratch_path.exists():
                            shutil.rmtree(scratch_path, ignore_errors=True)
                        break
                    time.sleep(0.05 * (2 ** attempt))

    def __enter__(self) -> EphemeralWorkspaceManager:
        self.initialize()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.cleanup()

    def _stage_inputs_copy(self, src_root: Path, dst_root: Path) -> None:
        """Stage inputs from source root into destination root using COPY semantics only."""
        for root_dir, dirs, files in os.walk(src_root):
            # Check for symlinks in directories
            rel_dir = Path(root_dir).relative_to(src_root)
            current_dst_dir = dst_root / rel_dir

            # Filter out ignored directories (.git, __pycache__)
            dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]

            for d in dirs:
                dir_path = Path(root_dir) / d
                if dir_path.is_symlink():
                    raise ValueError(
                        f"Symlinks are rejected as staging mechanisms in Forge v0.1: {dir_path}"
                    )
                (current_dst_dir / d).mkdir(parents=True, exist_ok=True)

            for f in files:
                if f.endswith(".pyc"):
                    continue
                src_file = Path(root_dir) / f
                if src_file.is_symlink():
                    raise ValueError(
                        f"Symlinks are rejected as staging mechanisms in Forge v0.1: {src_file}"
                    )
                dst_file = current_dst_dir / f
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)
