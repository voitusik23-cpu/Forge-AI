"""Ephemeral workspace manager enforcing COPY staging, path boundaries, and deterministic cleanup."""

from __future__ import annotations

import os
import shutil
import stat
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
from app.execution.secret_files import is_secret_directory, is_secret_file


def _file_state(path: Path, *, max_bytes: int) -> dict[str, object]:
    """Observable state of one file: existence, size, mtime and content digest.

    ``mtime_ns`` is recorded but is never the proof on its own: a copy can preserve
    it. The digest is what distinguishes "the file changed" from "the file is the
    same file that was already there".
    """

    import hashlib

    state: dict[str, object] = {
        "exists": False,
        "size_bytes": None,
        "mtime_ns": None,
        "sha256": None,
        "unreadable": False,
    }
    try:
        info = path.stat()
    except FileNotFoundError:
        return state
    except OSError:
        state["unreadable"] = True
        return state
    state["exists"] = True
    state["size_bytes"] = int(info.st_size)
    state["mtime_ns"] = int(getattr(info, "st_mtime_ns", 0))
    if int(info.st_size) > max_bytes:
        # Too large to digest under the artifact bound; existence and size are still
        # recorded, and the comparison reports "unknown" rather than guessing.
        state["unreadable"] = True
        return state
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        state["unreadable"] = True
        return state
    state["sha256"] = digest.hexdigest()
    return state


def snapshot_artifact_states(
    root: "Path | None", targets: "Sequence[str]", *, max_bytes: int | None = None
) -> dict[str, dict[str, object]]:
    """State of every declared artifact target, taken **before** a command starts.

    This is evidence, not authority: it lets a later harvest say whether the file it
    measured was already there, unchanged, when the attempt began. Targets that are
    unsafe or credential-shaped are skipped here exactly as the harvest refuses
    them, so the snapshot never reads anything the harvest would not.
    """

    limit = DEFAULT_MAX_ARTIFACT_BYTES if max_bytes is None else max_bytes
    snapshot: dict[str, dict[str, object]] = {}
    if root is None:
        return snapshot
    base = Path(root).resolve()
    for target in targets or ():
        if not isinstance(target, str) or not target.strip():
            continue
        # An absolute or drive-qualified target is refused, never reinterpreted as
        # a relative one: silently turning ``/etc/passwd`` into ``etc/passwd`` is the
        # aliasing class of bug this file already paid for once.
        if target.startswith(("/", "\\\\")) or (
            len(target) > 1 and target[1] == ":"
        ):
            continue
        clean = target.replace("\\", "/").strip("/")
        if not clean or ".." in clean.split("/"):
            continue
        parts = clean.split("/")
        if any(is_secret_directory(part) for part in parts[:-1]) or is_secret_file(
            parts[-1]
        ):
            continue
        try:
            candidate = (base / clean).resolve(strict=False)
            candidate.relative_to(base)
        except (OSError, RuntimeError, ValueError):
            continue
        snapshot[clean] = _file_state(candidate, max_bytes=limit)
    return snapshot


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
        # Names (not contents) of files refused by the secret filter during staging.
        # Reported so an operator can see that a credential file was withheld instead of
        # silently wondering why the run cannot find it.
        self._secret_files_skipped: list[str] = []

    @property
    def secret_files_skipped(self) -> tuple[str, ...]:
        """Names of files withheld from the staged tree because they hold credentials."""

        return tuple(self._secret_files_skipped)

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

    def bind_source_root(self, source_root: Path | None) -> None:
        """Declare the workspace to stage, when the manager was built without one.

        The adapter may be composed with a pre-built manager -- so a caller can choose the
        scratch location -- and with no source root bound. In that case the isolated branch
        used to run in an **empty** scratch directory: the child's working directory
        contained none of the project's files, and a command referencing one failed with
        "can't open file". Binding the root here restores staging without forcing every
        composition to reconstruct the manager.

        A root already bound is never replaced: an explicit choice made at construction
        time outranks an adapter default.
        """

        if self._source_root is None and source_root is not None:
            self._source_root = Path(source_root).resolve()

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
        self._secret_files_skipped = []

        if self._source_root is not None and self._source_root.is_dir():
            self._stage_inputs_copy(self._source_root, self._scratch_root)

        return self._scratch_root

    def resolve_path(self, relative_path: str, *, root: Path | None = None) -> Path:
        """Resolve a path relative to a root and verify it does not escape.

        ``root`` defaults to the active scratch root. It is passed explicitly by
        :meth:`harvest_artifacts` when the composition executes **without**
        isolation, where the trusted root is the operator's own workspace carried
        by the ``AuthorizedExecution`` token rather than a scratch directory.
        A caller-supplied root is not authority: the only value ever passed here
        is that frozen token root, and the traversal check below is applied to it
        exactly as it is to the scratch root.
        """
        root = self.scratch_root if root is None else Path(root)
        root = root.resolve()
        norm = relative_path.replace("\\", "/").strip()
        if not norm or norm == ".":
            return root

        parts = norm.split("/")
        if ".." in parts or norm.startswith("/"):
            raise ValueError(f"Path traversal detected: {relative_path}")

        candidate = root / norm
        # Inspect the lexical path before resolving it.  On Windows a junction is
        # a reparse point but is not reported by ``Path.is_symlink()``; resolving
        # first would otherwise let a path cross the trusted perimeter silently.
        current = root
        for component in parts:
            current = current / component
            if self._is_reparse_point(current):
                raise ValueError("Workspace path crosses a reparse point")

        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("Resolved path escapes ephemeral workspace root") from exc

        return resolved

    def harvest_artifacts(
        self,
        artifact_targets: Sequence[str],
        *,
        run_id: str,
        provenance_source: str = "execution",
        root: Path | None = None,
        baseline: dict[str, dict[str, object]] | None = None,
    ) -> tuple[Artifact, ...]:
        """Harvest declared artifact targets from a trusted execution root.

        Enforces:
        - Relative path safety / no directory traversal (..)
        - Symlink checks (symlinks are rejected)
        - Artifact size bounds
        - Cryptographic hashing (SHA-256)

        The root is the active scratch root by default. A composition that
        executes without isolation passes the trusted workspace root from the
        ``AuthorizedExecution`` token instead, so "the adapter harvests" is true
        on that path too. Harvesting only *measures*: it records a relative path,
        a size and a SHA-256, and never copies, opens for write, or relocates a
        file, so it grants no authority and reaches no path outside the root.
        """
        root = self.scratch_root if root is None else Path(root).resolve()
        harvested: list[Artifact] = []

        for target in artifact_targets:
            if not isinstance(target, str) or not target.strip():
                raise ValueError("Artifact target must be a non-empty string")

            clean_target = target.replace("\\", "/").strip("/")
            if not clean_target or ".." in clean_target.split("/"):
                raise ValueError(f"Unsafe artifact target path: {target}")

            # Credential material is never harvested. Harvesting opens the file and
            # publishes its digest, and the target set is not purely the operator's:
            # a step graph arrives from the client and its verification adds targets
            # to the request. Staging withholds these names from the scratch tree and
            # the scanner refuses to fingerprint them; harvesting now agrees with
            # both. The rule is the name, not the existence of the file.
            parts = clean_target.split("/")
            if any(is_secret_directory(part) for part in parts[:-1]) or is_secret_file(
                parts[-1]
            ):
                raise ValueError(
                    f"Refusing to harvest credential material: {clean_target}"
                )

            resolved_path = self.resolve_path(clean_target, root=root)

            if not resolved_path.exists():
                continue

            if resolved_path.is_symlink():
                raise ValueError(f"Artifact target cannot be a symlink: {clean_target}")

            if not resolved_path.is_file():
                continue

            # Provenance: was this file already here, unchanged, when the attempt
            # began? ``changed_by_this_attempt is True`` is the only value the
            # verifier accepts as production; ``None`` means the question cannot be
            # answered and is reported as unevaluable rather than as success.
            before = (baseline or {}).get(clean_target)
            after = _file_state(resolved_path, max_bytes=self._max_artifact_bytes)
            if before is None or before.get("unreadable") or after.get("unreadable"):
                changed: bool | None = None
            elif not before.get("exists"):
                changed = True
            elif before.get("sha256") != after.get("sha256"):
                changed = True
            else:
                changed = False
            provenance = {
                "existed_before": bool(before.get("exists")) if before else None,
                "changed_by_this_attempt": changed,
                "sha256_before": before.get("sha256") if before else None,
                "size_before": before.get("size_bytes") if before else None,
                "baseline_recorded_for": str(run_id),
            }
            art = create_artifact_from_file(
                resolved_path,
                relative_path=clean_target,
                run_id=run_id,
                provenance_source=provenance_source,
                max_bytes=self._max_artifact_bytes,
                metadata=provenance,
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
        """Stage inputs from source root into destination root using COPY semantics only.

        Secret files are **not** staged. The staged tree becomes the working directory
        of the process a run starts, so a credential file copied here is a credential
        file the admitted code can read with an ordinary relative open. Before this
        filter existed, staging excluded only ``.git``, ``__pycache__`` and ``*.pyc``,
        and a directory containing ``.env`` was copied verbatim.

        The recognizer is shared with the project scanner
        (:mod:`app.execution.secret_files`), so "what the scanner refuses to index" and
        "what the execution workspace refuses to copy" cannot drift apart again.

        A name-based filter is not complete -- a secret inside ``notes.txt`` is not
        recognized by any name rule, and declared-input allow-listing remains deferred.
        What is guaranteed here is checkable: no file whose name marks it as credential
        material, and no credential directory, reaches the staged tree.
        """

        for root_dir, dirs, files in os.walk(src_root):
            # Check for symlinks in directories
            rel_dir = Path(root_dir).relative_to(src_root)
            current_dst_dir = dst_root / rel_dir

            # Prune in place: ignored directories and credential directories are never
            # descended into, so a nested secret cannot be reached by walking into it.
            dirs[:] = [
                d
                for d in dirs
                if d not in {".git", "__pycache__"} and not is_secret_directory(d)
            ]

            for d in dirs:
                dir_path = Path(root_dir) / d
                if self._is_reparse_point(dir_path):
                    raise ValueError("Reparse points are rejected as staging mechanisms")
                (current_dst_dir / d).mkdir(parents=True, exist_ok=True)

            for f in files:
                if f.endswith(".pyc"):
                    continue
                if is_secret_file(f):
                    # Refused, not an error: a repository legitimately contains such a
                    # file, and failing the run would make the safe outcome the broken
                    # one. The refusal is counted so a caller can report it.
                    self._secret_files_skipped.append(f)
                    continue
                src_file = Path(root_dir) / f
                if self._is_reparse_point(src_file):
                    raise ValueError("Reparse points are rejected as staging mechanisms")
                dst_file = current_dst_dir / f
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        """True for a symlink, a Windows directory junction, or any other reparse point.

        ``Path.is_symlink()`` returns False for a Windows **junction**: ``os.path.islink``
        recognizes one, ``Path.is_symlink()`` does not. Relying on ``is_symlink()`` alone
        therefore let a junction through the check, and the subsequent
        ``os.walk(followlinks=False)`` raised a raw ``OSError`` while descending. That is
        not a clean refusal, and a raw ``OSError`` is not something a caller can act on.

        ``st_file_attributes`` exists only on Windows, so the check is guarded and
        degrades to the POSIX symlink test elsewhere.
        """

        try:
            if path.is_symlink():
                return True
            if os.name == "nt":
                attributes = getattr(
                    os.stat(path, follow_symlinks=False), "st_file_attributes", 0
                )
                return bool(
                    attributes
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                )
        except FileNotFoundError:
            # A declared artifact may legitimately be absent: the verifier must
            # report ``artifact_not_produced`` after execution.  Non-existent
            # paths are not reparse points; their existing parent components
            # were checked above.
            return False
        except OSError:
            # A path that cannot be inspected must be refused, not copied.
            return True
        return False
