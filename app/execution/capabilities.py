"""Minimal capability model classifying process execution capabilities."""

from __future__ import annotations

import re
from collections.abc import Sequence
from enum import Enum


class ExecutionCapability(str, Enum):
    """Fine-grained execution capabilities for command authorization."""

    EXEC_CHILD = "EXEC_CHILD"
    INTERPRET_TEXT = "INTERPRET_TEXT"
    INTERPRET_MODULE = "INTERPRET_MODULE"
    NETWORK = "NETWORK"


# Regular expressions for interpreter flags
_PYTHON_SHORT_EVAL_PATTERN = re.compile(r"^-[bBdEiIOPqsuv]*c")
_PYTHON_SHORT_MODULE_PATTERN = re.compile(r"^-[bBdEiIOPqsuv]*m")
_NODE_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*[ep]")
_RUBY_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*e")
_PERL_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*[eE]")
_PHP_SHORT_EVAL_PATTERN = re.compile(r"^-[a-zA-Z]*[rR]")

_WRAPPER_EXECUTABLES = frozenset({
    "env",
    "xargs",
    "sudo",
    "doas",
    "chroot",
    "nohup",
    "strace",
    "ltrace",
    "time",
    "nice",
    "ionice",
    "taskset",
    "timeout",
    "watch",
    "runas",
})

_NETWORK_EXECUTABLES = frozenset({
    "curl",
    "wget",
    "ssh",
    "scp",
    "rsync",
    "nc",
    "netcat",
    "ftp",
    "telnet",
    "pip",
    "pip3",
    "npm",
    "yarn",
    "pnpm",
    "cargo",
    "gem",
    "composer",
})

_GIT_NETWORK_SUBCOMMANDS = frozenset({
    "clone",
    "fetch",
    "pull",
    "push",
    "remote",
    "ls-remote",
    "submodule",
})


def classify_invocation(
    executable: str, argv: Sequence[str] = ()
) -> frozenset[ExecutionCapability]:
    """Classify the capabilities required to run the specified executable and argv."""
    caps: set[ExecutionCapability] = set()

    clean_exec = executable.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = clean_exec.rsplit(".", 1)[0]
    arg_list = list(argv)

    # 1. Wrappers that execute child commands
    if stem in _WRAPPER_EXECUTABLES:
        caps.add(ExecutionCapability.EXEC_CHILD)

    # find -exec
    if stem == "find":
        for a in arg_list:
            if a in ("-exec", "-execdir", "-ok", "-okdir"):
                caps.add(ExecutionCapability.EXEC_CHILD)
                break

    # 2. Network commands
    if stem in _NETWORK_EXECUTABLES:
        caps.add(ExecutionCapability.NETWORK)

    if stem == "git":
        for a in arg_list:
            if a.lower() in _GIT_NETWORK_SUBCOMMANDS:
                caps.add(ExecutionCapability.NETWORK)
                break

    # 3. Python family
    if stem == "python" or stem.startswith("python3") or stem.startswith("pypy"):
        for arg in arg_list:
            if arg == "-":
                caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("--"):
                low = arg.lower()
                if low == "--command" or low.startswith("--command=") or low == "--eval" or low.startswith("--eval="):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                elif low == "--module" or low.startswith("--module="):
                    caps.add(ExecutionCapability.INTERPRET_MODULE)
            elif arg.startswith("-") and len(arg) > 1:
                if _PYTHON_SHORT_EVAL_PATTERN.match(arg):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                if _PYTHON_SHORT_MODULE_PATTERN.match(arg):
                    caps.add(ExecutionCapability.INTERPRET_MODULE)

    # 4. Shell family
    elif stem in {"bash", "sh", "zsh", "dash", "ksh"}:
        for arg in arg_list:
            if arg in {"-", "-s"}:
                caps.add(ExecutionCapability.INTERPRET_TEXT)
                caps.add(ExecutionCapability.EXEC_CHILD)
            elif arg.startswith("--"):
                if arg.lower() in {"--command"} or arg.lower().startswith("--command="):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                    caps.add(ExecutionCapability.EXEC_CHILD)
            elif arg.startswith("-") and len(arg) > 1:
                if "c" in arg or "s" in arg:
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                    caps.add(ExecutionCapability.EXEC_CHILD)

    # 5. PowerShell family
    elif stem in {"powershell", "pwsh"}:
        for arg in arg_list:
            if arg.startswith(("-", "/")) and len(arg) > 1:
                norm = arg.lstrip("-/").lower()
                if norm in {"c", "e", "ec"} or norm.startswith(("c:", "c=", "e:", "e=")):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                    caps.add(ExecutionCapability.EXEC_CHILD)
                elif norm.startswith(("command", "comm", "encodedcommand", "enc")):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                    caps.add(ExecutionCapability.EXEC_CHILD)

    # 6. Windows CMD family
    elif stem == "cmd":
        for arg in arg_list:
            if arg.startswith(("/", "-")) and len(arg) > 1:
                low = arg.lower()
                if low.startswith(("/c", "/k", "-c", "-k")):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
                    caps.add(ExecutionCapability.EXEC_CHILD)

    # 7. Node / JS
    elif stem in {"node", "nodejs"}:
        for arg in arg_list:
            if arg == "-":
                caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("--"):
                low = arg.lower()
                if low in {"--eval", "--print"} or low.startswith(("--eval=", "--print=")):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("-") and len(arg) > 1:
                if _NODE_SHORT_EVAL_PATTERN.match(arg):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)

    # 8. Ruby
    elif stem == "ruby":
        for arg in arg_list:
            if arg == "-":
                caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("--"):
                low = arg.lower()
                if low in {"--eval"} or low.startswith("--eval="):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("-") and len(arg) > 1:
                if _RUBY_SHORT_EVAL_PATTERN.match(arg):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)

    # 9. Perl
    elif stem == "perl":
        for arg in arg_list:
            if arg == "-":
                caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("--"):
                low = arg.lower()
                if low in {"--eval"} or low.startswith("--eval="):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("-") and len(arg) > 1:
                if _PERL_SHORT_EVAL_PATTERN.match(arg):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)

    # 10. PHP
    elif stem == "php":
        for arg in arg_list:
            if arg == "-":
                caps.add(ExecutionCapability.INTERPRET_TEXT)
            elif arg.startswith("-") and len(arg) > 1 and not arg.startswith("--"):
                if _PHP_SHORT_EVAL_PATTERN.match(arg):
                    caps.add(ExecutionCapability.INTERPRET_TEXT)

    return frozenset(caps)
