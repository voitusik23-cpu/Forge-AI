"""Authoritative command identity and matching rules for execution authorization."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
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

    def to_dict(self) -> dict[str, object]:
        return {"executable": self.executable, "argv": list(self.argv)}


from app.execution.paths import (
    PathSecurityError,
    canonical_executable,
    is_bare_executable,
)


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


_PYTHON_SHORT_EVAL_PATTERN = re.compile(r"^-[bBdEiIOPqsuv]*[cm]")
_NODE_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*[ep]")
_RUBY_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*e")
_PERL_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*[eE]")
_PHP_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*[rR]")


def dangerous_interpreter_argv(identity: CommandIdentity) -> bool:
    """Identify interpreter eval, module, or shell flags that must have an exact approved intent."""
    name = identity.executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = name.rsplit(".", 1)[0]

    # 1. Python family
    if stem == "python" or stem.startswith("python3") or stem.startswith("pypy"):
        for arg in identity.argv:
            if arg == "-":
                return True
            if arg.startswith("--"):
                low = arg.lower()
                if low in {"--command", "--module", "--eval"} or low.startswith(
                    ("--command=", "--module=", "--eval=")
                ):
                    return True
            elif arg.startswith("-") and len(arg) > 1:
                if _PYTHON_SHORT_EVAL_PATTERN.match(arg):
                    return True
        return False

    # 2. Shell family
    if stem in {"bash", "sh", "zsh", "dash"}:
        for arg in identity.argv:
            if arg in {"-", "-s"}:
                return True
            if arg.startswith("--"):
                if arg.lower() in {"--command"} or arg.lower().startswith("--command="):
                    return True
            elif arg.startswith("-") and len(arg) > 1:
                if "c" in arg or "s" in arg:
                    return True
        return False

    # 3. PowerShell family
    if stem in {"powershell", "pwsh"}:
        for arg in identity.argv:
            if arg.startswith(("-", "/")) and len(arg) > 1:
                norm = arg.lstrip("-/").lower()
                if norm in {"c", "e", "ec"} or norm.startswith(("c:", "c=", "e:", "e=")):
                    return True
                if norm.startswith(("command", "comm", "encodedcommand", "enc")):
                    return True
        return False

    # 4. Windows CMD family
    if stem == "cmd":
        for arg in identity.argv:
            if arg.startswith(("/", "-")) and len(arg) > 1:
                low = arg.lower()
                if low.startswith(("/c", "/k", "-c", "-k")):
                    return True
        return False

    # 5. Node family
    if stem in {"node", "nodejs"}:
        for arg in identity.argv:
            if arg == "-":
                return True
            if arg.startswith("--"):
                low = arg.lower()
                if low in {"--eval", "--print"} or low.startswith(("--eval=", "--print=")):
                    return True
            elif arg.startswith("-") and len(arg) > 1:
                if _NODE_SHORT_EVAL_PATTERN.match(arg):
                    return True
        return False

    # 6. Ruby family
    if stem == "ruby":
        for arg in identity.argv:
            if arg == "-":
                return True
            if arg.startswith("--"):
                low = arg.lower()
                if low in {"--eval"} or low.startswith("--eval="):
                    return True
            elif arg.startswith("-") and len(arg) > 1:
                if _RUBY_SHORT_EVAL_PATTERN.match(arg):
                    return True
        return False

    # 7. Perl family
    if stem == "perl":
        for arg in identity.argv:
            if arg == "-":
                return True
            if arg.startswith("--"):
                low = arg.lower()
                if low in {"--eval"} or low.startswith("--eval="):
                    return True
            elif arg.startswith("-") and len(arg) > 1:
                if _PERL_SHORT_EVAL_PATTERN.match(arg):
                    return True
        return False

    # 8. PHP family
    if stem == "php":
        for arg in identity.argv:
            if arg == "-":
                return True
            if arg.startswith("-") and len(arg) > 1 and not arg.startswith("--"):
                if _PHP_SHORT_EVAL_PATTERN.match(arg):
                    return True
        return False

    return False
