"""Open a delivered project on the owner's machine: folder, editor, terminal.

The owner's words: *«Из Forge должен быть вход в проект — кнопка перехода»*. A button
that only printed a path was not an entrance. This module performs the three things a
person actually means by it:

===========  ==========================================================
``folder``   ``explorer.exe <project folder>``
``editor``   ``code <project folder>`` (VS Code), when it is installed
``terminal`` ``wt.exe -d <folder>``, or ``powershell -NoExit -Command Set-Location``
===========  ==========================================================

Four rules make that safe and honest:

* **The path never comes from the request.** The route resolves the folder from the
  project record, server-side, and hands it here. A request names one of three words.
* **No shell.** Every process is started with ``shell=False`` and an argument vector,
  so a folder name can never become a command. The one command that carries a path as
  text (the PowerShell fallback) quotes it with ``-LiteralPath`` and a doubled
  single quote.
* **An allow-list, not a search.** Exactly these three integrations exist; there is no
  "run this program" entry point and no arbitrary argv.
* **A refusal is a sentence.** When VS Code or a terminal cannot be resolved, or the
  project has no files, or the folder is gone, the answer says so in one plain Russian
  sentence and *nothing is started*. The interface shows that sentence next to the
  disabled button -- it never invents a launch it cannot perform.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from app.projects.folders import canonical, count_files
from app.projects.models import ProjectError

__all__ = [
    "OPEN_TARGETS",
    "OpenRefusal",
    "OpenResult",
    "ProjectOpener",
    "TARGET_EDITOR",
    "TARGET_FOLDER",
    "TARGET_TERMINAL",
]

TARGET_FOLDER = "folder"
TARGET_EDITOR = "editor"
TARGET_TERMINAL = "terminal"

#: The whole open vocabulary. A request may name one of these and nothing else.
OPEN_TARGETS: Tuple[str, ...] = (TARGET_FOLDER, TARGET_EDITOR, TARGET_TERMINAL)

#: The label of each target, as the card prints it.
TARGET_LABELS: Mapping[str, str] = {
    TARGET_FOLDER: "Открыть папку",
    TARGET_EDITOR: "Открыть в VS Code",
    TARGET_TERMINAL: "Открыть терминал",
}


class OpenRefusal(ProjectError):
    """Nothing was started, and this is why."""

    code = "project_open_refused"

    def __init__(self, message: str, *, target: str = "") -> None:
        super().__init__(message)
        self.target = str(target or "")


@dataclass(frozen=True)
class OpenResult:
    """What was started: the program, its pid, and the folder it was pointed at."""

    target: str
    program: str
    pid: int
    folder: str
    arguments: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target": self.target,
            "opened": True,
            "program": self.program,
            "pid": int(self.pid),
            # The absolute folder and the exact argument vector are diagnostics: the
            # files are the owner's own, but a payload still does not advertise host
            # paths above a collapsed block.
            "diagnostics": {
                "folder": self.folder,
                "arguments": list(self.arguments),
            },
        }


def _default_which(program: str) -> Optional[str]:
    return shutil.which(program)


def _windows_directory() -> Optional[Path]:
    value = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    return Path(value) if value else None


def _first_existing(
    candidates: Sequence[object], exists: Optional[Callable[[object], bool]] = None
) -> Optional[str]:
    accept = exists if exists is not None else (lambda item: Path(str(item)).is_file())
    for candidate in candidates:
        if candidate is None:
            continue
        path = Path(str(candidate))
        try:
            if accept(path):
                return str(path)
        except OSError:  # pragma: no cover - defensive
            continue
    return None


#: VS Code's usual installations, checked when ``code`` is not on ``PATH``. The
#: per-user install is first because that is what the current installer defaults to.
def _vscode_candidates() -> List[Path]:
    found: List[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        found.append(Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe")
    for variable in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            found.append(Path(root) / "Microsoft VS Code" / "Code.exe")
    return found


#: Windows Terminal, then the two PowerShell hosts, in the order a fallback tries them.
def _terminal_candidates(
    which: Callable[[str], Optional[str]],
    exists: Optional[Callable[[object], bool]] = None,
) -> List[Tuple[str, List[str]]]:
    """Programs that can open an interactive terminal at a folder, best first."""

    candidates: List[Tuple[str, List[str]]] = []
    wt = which("wt") or which("wt.exe")
    if not wt:
        local = os.environ.get("LOCALAPPDATA")
        wt = _first_existing(
            [Path(local) / "Microsoft" / "WindowsApps" / "wt.exe"] if local else [],
            exists,
        )
    if wt:
        candidates.append((wt, ["-d"]))
    for name in ("pwsh", "powershell"):
        program = which(name) or which(f"{name}.exe")
        if program:
            candidates.append((program, ["-NoExit", "-Command"]))
            break
    if not candidates:
        windir = _windows_directory()
        fallback = _first_existing(
            [windir / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"]
            if windir
            else [],
            exists,
        )
        if fallback:
            candidates.append((fallback, ["-NoExit", "-Command"]))
    return candidates


def _quote_for_powershell(value: str) -> str:
    """A single-quoted PowerShell literal: the one quoting that takes no expansion."""

    return "'" + str(value).replace("'", "''") + "'"


class ProjectOpener:
    """Resolve and start the three OS integrations, or refuse with a sentence.

    ``which`` and ``spawn`` are injectable so a test can prove *which* program would
    have been started, with *which* argument vector, without opening a real Explorer
    window on the machine running the suite. The production defaults are the real ones.
    """

    def __init__(
        self,
        *,
        which: Optional[Callable[[str], Optional[str]]] = None,
        spawn: Optional[Callable[..., Any]] = None,
        exists: Optional[Callable[[object], bool]] = None,
    ) -> None:
        self._which = which if which is not None else _default_which
        self._spawn = spawn if spawn is not None else subprocess.Popen
        # The existence test for the *install paths* (VS Code's usual directories, the
        # system PowerShell). Injectable so a test can state what is installed on the
        # machine it is pretending to be, instead of inheriting the real one.
        self._exists = exists if exists is not None else (lambda item: Path(str(item)).is_file())

    # -------------------------------------------------------------- resolution
    def _explorer(self) -> Optional[str]:
        program = self._which("explorer") or self._which("explorer.exe")
        if program:
            return program
        windir = _windows_directory()
        return _first_existing(
            [windir / "explorer.exe"] if windir else [], self._exists
        )

    def _editor(self) -> Optional[str]:
        for name in ("code", "code.cmd", "code.exe"):
            program = self._which(name)
            if program:
                return program
        return _first_existing(_vscode_candidates(), self._exists)

    def _terminal(self) -> Optional[Tuple[str, List[str]]]:
        candidates = _terminal_candidates(self._which, self._exists)
        return candidates[0] if candidates else None

    # -------------------------------------------------------------- the plan
    def plan(
        self, target: str, folder: Optional[Path], *, files: Optional[int] = None
    ) -> Tuple[Optional[List[str]], str]:
        """The argument vector for ``target``, or ``(None, reason)``.

        The vector is fully resolved here -- program *and* arguments -- so the caller
        cannot add a word to it and a test can assert it exactly.
        """

        if target not in OPEN_TARGETS:
            return None, f"Неизвестное действие: {target!r}."
        if folder is None:
            return None, (
                "Папка проекта не найдена: открывать нечего. "
                "Проверьте, не перемещена ли она."
            )
        try:
            location = canonical(folder)
        except ProjectError:
            return None, "Папку проекта не удалось разрешить: открывать нечего."
        if not location.is_dir():
            return None, "Папка проекта не найдена: открывать нечего."
        if files is None:
            files = count_files(location)
        if int(files) <= 0:
            return None, (
                "В проекте нет файлов: открывать нечего. "
                "Сначала выполните запуск, который создаст файлы."
            )

        if target == TARGET_FOLDER:
            program = self._explorer()
            if not program:
                return None, (
                    "Проводник Windows не найден: откройте папку вручную по пути из "
                    "блока диагностики."
                )
            return [program, str(location)], ""

        if target == TARGET_EDITOR:
            program = self._editor()
            if not program:
                return None, (
                    "VS Code не найден: установите VS Code или добавьте команду code в "
                    "PATH. Папку можно открыть вручную из Проводника."
                )
            return [program, str(location)], ""

        terminal = self._terminal()
        if terminal is None:
            return None, (
                "Терминал не найден: не удалось запустить ни Windows Terminal, ни "
                "PowerShell. Откройте терминал вручную в папке проекта."
            )
        program, arguments = terminal
        if arguments and arguments[-1] == "-Command":
            command = f"Set-Location -LiteralPath {_quote_for_powershell(str(location))}"
            return [program, *arguments, command], ""
        return [program, *arguments, str(location)], ""

    def availability(
        self, folder: Optional[Path], *, files: Optional[int] = None
    ) -> Dict[str, Dict[str, Any]]:
        """Per-target ``{available, reason, label}``, with no process started."""

        found: Dict[str, Dict[str, Any]] = {}
        for target in OPEN_TARGETS:
            vector, reason = self.plan(target, folder, files=files)
            found[target] = {
                "target": target,
                "label": TARGET_LABELS[target],
                "available": vector is not None,
                "reason": reason,
            }
        return found

    # ------------------------------------------------------------- the action
    def open(
        self, target: str, folder: Optional[Path], *, files: Optional[int] = None
    ) -> OpenResult:
        """Start the resolved program, or raise :class:`OpenRefusal` and start nothing."""

        vector, reason = self.plan(target, folder, files=files)
        if vector is None:
            raise OpenRefusal(reason, target=target)
        program, arguments = vector[0], vector[1:]
        try:
            child = self._spawn(
                vector,
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                # Detached from this server: closing Forge must not close the editor,
                # and the child must not hold the API's console.
                creationflags=_detached_flags(),
            )
        except OSError as exc:
            raise OpenRefusal(
                "Не удалось запустить программу: "
                f"{Path(program).name} ({type(exc).__name__}).",
                target=target,
            ) from exc
        return OpenResult(
            target=target,
            program=Path(program).name,
            pid=int(getattr(child, "pid", 0) or 0),
            folder=str(folder),
            arguments=tuple(str(item) for item in arguments),
        )


def _detached_flags() -> int:
    """Process-creation flags that detach the child, or ``0`` off Windows."""

    if os.name != "nt":  # pragma: no cover - this product runs on Windows
        return 0
    flags = 0
    for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        flags |= int(getattr(subprocess, name, 0) or 0)
    return flags
