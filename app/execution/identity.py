"""Authoritative command identity and matching rules for execution authorization."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CommandIdentity:
    """Canonical executable and argv used by Permission and Approval."""

    executable: str
    argv: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "executable", canonical_executable(self.executable))
        object.__setattr__(self, "argv", tuple(str(a) for a in self.argv))

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(
            {"executable": self.executable, "argv": list(self.argv)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def canonical_executable(value: object) -> str:
    """Canonicalize an executable without basename substitution."""
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise ValueError("executable must be a non-empty string")
    raw = value.strip().replace("\\", "/")
    # Bare commands remain exact tokens. Path-bearing commands are canonicalized
    # as paths, never reduced to a basename or stem.
    has_path = "/" in raw or bool(ntpath.splitdrive(raw)[0]) or raw.startswith(".")
    if has_path:
        path = Path(raw)
        if not path.is_absolute() and not raw.startswith("."):
            path = Path(os.path.abspath(raw))
        else:
            path = path.resolve(strict=False)
        raw = path.as_posix()
    return os.path.normcase(raw)


def command_identity(command: Iterable[object]) -> CommandIdentity:
    parts = tuple(command)
    if not parts:
        raise ValueError("command must not be empty")
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise ValueError("command parts must be non-empty strings")
    return CommandIdentity(
        executable=canonical_executable(parts[0]),
        argv=tuple(parts[1:]),
    )


def executable_matches(requested: str, allowed: str) -> bool:
    """Match exact canonical executable identities only."""
    try:
        return canonical_executable(requested) == canonical_executable(allowed)
    except ValueError:
        return False


def command_is_allowed(
    command: Iterable[object],
    allowed_commands: Iterable[object],
    *,
    exact_argv: bool = False,
) -> bool:
    """Check one command against executable entries or full CommandIdentity entries."""
    try:
        requested = command_identity(command)
    except ValueError:
        return False
    for allowed in allowed_commands:
        if isinstance(allowed, CommandIdentity):
            if executable_matches(requested.executable, allowed.executable) and requested.argv == allowed.argv:
                return True
        elif isinstance(allowed, str) and executable_matches(requested.executable, allowed):
            if not exact_argv:
                return True
    return False


def dangerous_interpreter_argv(identity: CommandIdentity) -> bool:
    """Identify interpreter eval flags that must have an exact approved intent."""
    name = identity.executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = name.rsplit(".", 1)[0]
    is_interpreter = (
        stem == "python"
        or stem.startswith("python3")
        or stem.startswith("pypy")
        or stem in {"node", "nodejs", "ruby", "perl"}
    )
    return is_interpreter and any(
        arg in {"-c", "--command", "-e", "--eval", "-m", "--module", "-"}
        for arg in identity.argv
    )
