"""Bounded workspace scanner for safe, non-executing static code analysis."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Sequence

from app.snapshots import SnapshotFile
from app.tools.workspace import Workspace, WorkspacePathError
from app.understanding.extractors.base import BaseExtractor
from app.understanding.extractors.generic_extractor import GenericExtractor
from app.understanding.extractors.manifest_extractor import ManifestExtractor
from app.understanding.extractors.python_extractor import PythonAstExtractor
from app.understanding.models import (
    DeclaredDependency,
    ManifestDescriptor,
    ScanLimits,
    ScanWarning,
    StructuralFact,
    StructuralFactType,
)


class BoundedProjectScanner:
    """Traverse and inspect files within a Workspace under strict resource and security bounds."""

    _IGNORED_DIRS = frozenset({
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "vendor",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        ".idea",
        ".vscode",
        ".next",
        ".nuxt",
        "target",
        "bin",
        "obj",
    })

    _SECRET_PATTERNS = frozenset({
        "credentials.json",
        "service_account.json",
        "serviceaccount.json",
        "client_secret.json",
    })

    _BINARY_EXTENSIONS = frozenset({
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
        ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar", ".bz2",
        ".exe", ".bin", ".pyc", ".pyd", ".so", ".dylib", ".dll",
        ".class", ".jar", ".war",
        ".db", ".sqlite", ".sqlite3",
        ".woff", ".woff2", ".ttf", ".eot",
        ".mp3", ".mp4", ".wav", ".avi", ".mov",
    })

    def __init__(
        self,
        limits: ScanLimits | None = None,
        extractors: Sequence[BaseExtractor] | None = None,
    ) -> None:
        self.limits = limits or ScanLimits()
        self.extractors = tuple(
            extractors
            if extractors is not None
            else (PythonAstExtractor(), ManifestExtractor(), GenericExtractor())
        )

    def scan(
        self,
        workspace: Workspace,
    ) -> tuple[tuple[SnapshotFile, ...], tuple[StructuralFact, ...], tuple[ManifestDescriptor, ...], tuple[ScanWarning, ...]]:
        if not isinstance(workspace, Workspace):
            raise ValueError("workspace must be a valid Workspace instance")

        files: list[SnapshotFile] = []
        facts: list[StructuralFact] = []
        manifests: list[ManifestDescriptor] = []
        warnings: list[ScanWarning] = []

        total_bytes = 0
        file_count = 0
        root_path = Path(workspace.root).resolve()

        for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
            # 1. Prune ignored directories in-place
            dirnames[:] = [
                d for d in sorted(dirnames)
                if d not in self._IGNORED_DIRS and not d.startswith(".git")
            ]

            for filename in sorted(filenames):
                if file_count >= self.limits.max_file_count:
                    warnings.append(
                        ScanWarning(
                            warning_type="MAX_FILE_COUNT_EXCEEDED",
                            message=f"Scanner reached maximum file limit ({self.limits.max_file_count} files). Traversal stopped.",
                        )
                    )
                    return (tuple(files), tuple(facts), tuple(manifests), tuple(warnings))

                full_path = Path(dirpath) / filename

                # Compute relative path
                try:
                    rel_path = full_path.relative_to(root_path).as_posix()
                except ValueError:
                    continue

                # Validate with Workspace rules
                try:
                    parts = Workspace.normalize_relative_path(rel_path)
                except WorkspacePathError as exc:
                    warnings.append(
                        ScanWarning(
                            warning_type="WORKSPACE_PATH_ERROR",
                            message=f"Skipping invalid workspace path {rel_path}: {exc}",
                            file_path=rel_path,
                        )
                    )
                    continue

                # Symlink safety: do not follow symlinks outside workspace root
                try:
                    lstat_info = full_path.lstat()
                except OSError as exc:
                    warnings.append(
                        ScanWarning(
                            warning_type="STAT_ERROR",
                            message=f"Could not stat {rel_path}: {exc}",
                            file_path=rel_path,
                        )
                    )
                    continue

                if stat.S_ISLNK(lstat_info.st_mode):
                    try:
                        resolved_symlink = full_path.resolve(strict=True)
                        if not str(resolved_symlink).startswith(str(root_path)):
                            warnings.append(
                                ScanWarning(
                                    warning_type="SYMLINK_ESCAPE_IGNORED",
                                    message=f"Skipping symlink {rel_path} pointing outside workspace",
                                    file_path=rel_path,
                                )
                            )
                            continue
                    except (OSError, RuntimeError):
                        warnings.append(
                            ScanWarning(
                                warning_type="BROKEN_SYMLINK_IGNORED",
                                message=f"Skipping broken symlink {rel_path}",
                                file_path=rel_path,
                            )
                        )
                        continue

                # Check secret exclusion
                if self._is_secret_file(filename, rel_path):
                    files.append(SnapshotFile(relative_path=rel_path, exists=True, fingerprint=None))
                    warnings.append(
                        ScanWarning(
                            warning_type="SECRET_FILE_EXCLUDED",
                            message=f"Excluded sensitive secret file {rel_path} from indexing",
                            file_path=rel_path,
                        )
                    )
                    continue

                file_size = lstat_info.st_size
                file_count += 1

                # Check binary file extension
                ext = Path(filename).suffix.lower()
                if ext in self._BINARY_EXTENSIONS:
                    try:
                        digest = self._hash_file(full_path)
                    except OSError as exc:
                        warnings.append(
                            ScanWarning(
                                warning_type="FILE_READ_ERROR",
                                message=f"Could not read {rel_path}: {exc}",
                                file_path=rel_path,
                            )
                        )
                        continue
                    files.append(SnapshotFile(relative_path=rel_path, exists=True, fingerprint=digest))
                    continue

                # Check max file size limit
                if file_size > self.limits.max_file_size_bytes:
                    try:
                        digest = self._hash_file(full_path)
                    except OSError as exc:
                        warnings.append(
                            ScanWarning(
                                warning_type="FILE_READ_ERROR",
                                message=f"Could not read {rel_path}: {exc}",
                                file_path=rel_path,
                            )
                        )
                        continue
                    files.append(SnapshotFile(relative_path=rel_path, exists=True, fingerprint=digest))
                    warnings.append(
                        ScanWarning(
                            warning_type="FILE_EXCEEDS_MAX_SIZE",
                            message=f"File {rel_path} ({file_size} bytes) exceeds limit of {self.limits.max_file_size_bytes} bytes. Skipped AST parsing.",
                            file_path=rel_path,
                        )
                    )
                    continue

                # Check total bytes limit
                if total_bytes + file_size > self.limits.max_total_bytes:
                    warnings.append(
                        ScanWarning(
                            warning_type="TOTAL_BYTES_LIMIT_EXCEEDED",
                            message=f"Scan reached total byte limit of {self.limits.max_total_bytes} bytes at {rel_path}.",
                            file_path=rel_path,
                        )
                    )
                    return (tuple(files), tuple(facts), tuple(manifests), tuple(warnings))

                total_bytes += file_size

                # Read and check for binary / null bytes
                try:
                    content, lines, digest = self._read_text_file(full_path)
                except UnicodeDecodeError:
                    digest = self._hash_file(full_path)
                    files.append(SnapshotFile(relative_path=rel_path, exists=True, fingerprint=digest))
                    continue
                except OSError as exc:
                    warnings.append(
                        ScanWarning(
                            warning_type="FILE_READ_ERROR",
                            message=f"Could not read {rel_path}: {exc}",
                            file_path=rel_path,
                        )
                    )
                    continue

                if "\x00" in content[:8192]:
                    # Binary content
                    files.append(SnapshotFile(relative_path=rel_path, exists=True, fingerprint=digest))
                    continue

                files.append(SnapshotFile(relative_path=rel_path, exists=True, fingerprint=digest))

                # File fact
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.FILE_INVENTORIED,
                        source_path=rel_path,
                        line_start=1,
                        line_end=len(lines) if lines else 1,
                        extractor_id="scanner_inventory_v1",
                        details={
                            "file_size": file_size,
                            "line_count": len(lines),
                            "extension": ext,
                            "fingerprint": digest,
                        },
                    )
                )

                # Run matching extractors
                for extractor in self.extractors:
                    if extractor.can_extract(rel_path):
                        ext_facts, ext_warnings = extractor.extract(rel_path, content, lines)
                        facts.extend(ext_facts)
                        warnings.extend(ext_warnings)

                        # Extract manifest descriptors if extractor produced manifest facts
                        for ef in ext_facts:
                            if ef.fact_type == StructuralFactType.MANIFEST_INVENTORIED:
                                manifest_deps = [
                                    DeclaredDependency(
                                        name=str(df.details.get("dependency_name", "")),
                                        version_spec=str(df.details.get("version_spec", "*")),
                                        manifest_path=rel_path,
                                        dependency_type=str(df.details.get("dependency_type", "production")),
                                    )
                                    for df in ext_facts
                                    if df.fact_type == StructuralFactType.DEPENDENCY_DECLARED
                                    and df.source_path == rel_path
                                ]
                                manifests.append(
                                    ManifestDescriptor(
                                        relative_path=rel_path,
                                        manifest_type=str(ef.details.get("manifest_type", "unknown")),
                                        declared_dependencies=tuple(manifest_deps),
                                        metadata=ef.details,
                                    )
                                )

        return (tuple(files), tuple(facts), tuple(manifests), tuple(warnings))

    @classmethod
    def _is_secret_file(cls, filename: str, rel_path: str) -> bool:
        lower_name = filename.lower()
        if lower_name == ".env" or lower_name.startswith(".env."):
            return True
        if lower_name in cls._SECRET_PATTERNS:
            return True
        if lower_name.endswith((".pem", ".key", ".pfx", ".p12", ".pkcs12", ".kdbx")):
            return True
        if lower_name.startswith("id_rsa") or lower_name.startswith("id_ed25519") or lower_name.startswith("id_ecdsa"):
            return True
        return False

    @staticmethod
    def _hash_file(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _read_text_file(file_path: Path) -> tuple[str, list[str], str]:
        raw = file_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        content = raw.decode("utf-8")
        lines = content.splitlines()
        return (content, lines, digest)
