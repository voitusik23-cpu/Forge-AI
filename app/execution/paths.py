"""Canonical path security module enforcing ProgramIdentity and WorkspaceRelativePath invariants."""

from __future__ import annotations

import ntpath
import os
import posixpath
import re
from pathlib import Path, PurePosixPath, PureWindowsPath


class PathSecurityError(ValueError):
    """Raised when a path violates canonical path-security invariants."""


_WINDOWS_RESERVED_DEVICE_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)


def canonical_executable(raw: str) -> str:
    """Canonicalize and strictly validate an executable name or absolute path.

    Invariants:
    1. Bare executable (no slash or backslash) is preserved exactly.
    2. Absolute path is preserved in clean normalized form without traversal (.. or .).
    3. Any relative path with directory components (./x, ../x, a/b, .\\x, ..\\x, a\\b) is REJECTED.
    4. Any leading dot notation (. or ..) is REJECTED.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PathSecurityError("executable must be a non-empty string")

    stripped = raw.strip()
    if "\x00" in stripped:
        raise PathSecurityError("executable cannot contain null bytes")

    # Reject leading dots immediately
    if stripped.startswith("."):
        raise PathSecurityError(f"relative executable path is not allowed: {raw}")

    normalized = stripped.replace("\\", "/")

    # Bare executable without slashes
    if "/" not in normalized:
        return os.path.normcase(stripped)

    # Path-bearing executable must be absolute
    posix_abs = normalized.startswith("/")
    windows_abs = bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith("//")

    if not (posix_abs or windows_abs):
        raise PathSecurityError(f"relative executable path with directories is not allowed: {raw}")

    # Disallow traversal components inside absolute paths
    parts = normalized.split("/")
    for part in parts:
        if part in ("..", "."):
            raise PathSecurityError(f"executable path cannot contain traversal components: {raw}")

    return os.path.normcase(normalized)


def is_bare_executable(raw: str) -> bool:
    """Determine if raw command is a bare executable without path components."""
    if "/" in raw or "\\" in raw:
        return False
    if raw.startswith("."):
        return False
    return True


def normalize_workspace_relative_path(value: object) -> str:
    """Normalize and strictly validate a workspace-relative path.

    Invariants:
    - Must be a non-empty relative path string.
    - Rejects POSIX absolute paths (/...).
    - Rejects Windows drive absolute paths (C:\\..., C:/...).
    - Rejects Windows drive-relative paths (C:foo).
    - Rejects rooted Windows paths (\\foo, /foo).
    - Rejects UNC paths (\\\\server\\share, //server/share).
    - Rejects Windows device namespaces (\\\\?\\, \\\\.\\).
    - Rejects traversal components (.. or .).
    - Rejects Alternate Data Streams (ADS) colon syntax (:).
    - Rejects Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9).
    - Rejects trailing dot or space forms in path components.
    - Rejects null bytes.
    - Normalizes separators to forward slashes.
    - Returns canonical forward-slash relative path string, or '.' for root.
    """
    if not isinstance(value, str):
        raise PathSecurityError("path must be a string")

    stripped = value.strip()
    if not stripped:
        raise PathSecurityError("path must be a non-empty relative path")

    if "\x00" in stripped:
        raise PathSecurityError("path cannot contain null bytes")

    if stripped == ".":
        return "."

    if stripped.endswith(("/", "\\")):
        raise PathSecurityError("path cannot end with a trailing separator")

    # Treat either slash as separator
    normalized = stripped.replace("\\", "/")

    # Reject UNC and device paths
    if normalized.startswith("//") or stripped.startswith(("\\\\", "//")):
        raise PathSecurityError("UNC and device paths are not allowed")

    # Reject Windows drive letters (C:, c:\, C:foo)
    if re.match(r"^[A-Za-z]:", normalized):
        raise PathSecurityError("Windows drive paths are not allowed")

    windows_path = PureWindowsPath(stripped)
    posix_path = PurePosixPath(normalized)

    if windows_path.drive:
        raise PathSecurityError("drive-qualified paths are not allowed")

    if windows_path.root or posix_path.is_absolute() or normalized.startswith("/"):
        raise PathSecurityError("absolute paths are not allowed")

    parts = posix_path.parts
    if not parts:
        return "."

    for part in parts:
        if part in ("..", "."):
            raise PathSecurityError("path traversal is not allowed")
        if ":" in part:
            raise PathSecurityError("colon and ADS syntax are not allowed")
        if part.endswith((".", " ")):
            raise PathSecurityError("trailing dot and space forms are not allowed")
        stem = part.split(".")[0].upper()
        if stem in _WINDOWS_RESERVED_DEVICE_NAMES:
            raise PathSecurityError(f"Windows reserved device name is not allowed: {part}")

    return "/".join(parts)


def normalize_workspace_relative_parts(value: object) -> tuple[str, ...]:
    """Normalize and return the components of a workspace-relative path as a tuple."""
    normalized = normalize_workspace_relative_path(value)
    if normalized == ".":
        return ()
    return tuple(normalized.split("/"))

