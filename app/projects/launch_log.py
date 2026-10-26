"""The project's own launcher log: ``<project>\\launch.log``, read and appended.

Two ways lead into a project, and they must end in the same place
----------------------------------------------------------------

The owner's requirement is one panel under «Запустить приложение» that says *why* a program
did not start. Two ways can start that program:

* **Forge's button** -- the runner captures the child's output, redacts it and reports the
  tail in the launch refusal (``log_tail``);
* **the project's desktop shortcut** -- the project's own launcher runs outside Forge, so
  Forge never sees a byte of its output. The one channel left is a file both sides agree on,
  and the mail project's launcher already writes it: ``<project>\\launch.log``.

This module is that channel, and nothing else:

* :func:`read_launch_log_tail` returns the last redacted lines of ``launch.log`` so a launch
  refusal can carry them beside the run's own log. A missing or unreadable file is an empty
  list -- an absent log is a fact, never an error;
* :func:`append_launch_log` appends one bounded, redacted block after a failed launch Forge
  itself performed, so the *next* press reads the very reason this one saw, even from the
  shortcut. The file is trimmed from the front past :data:`MAX_LAUNCH_LOG_BYTES`, so a
  project that fails a thousand times cannot fill a disk with one log.

Redaction is the repository's own, not a second implementation: every line goes through
:func:`app.projects.runner.redact_output_text`, the function the runner already applies to a
child's output. A line the scanner still recognises as secret material is replaced whole --
a partially rewritten secret is a leaked secret.

Nothing here decides what a failure means. The words of the panel are derived on the client
from the lines this module hands over (``ui/src/repair.ts``).
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any, Iterable, List, Optional

from app.projects.runner import redact_output_text

__all__ = [
    "LAUNCH_LOG_NAME",
    "LAUNCH_LOG_TAIL_LINES",
    "MAX_LAUNCH_LOG_BYTES",
    "append_launch_log",
    "launch_log_path",
    "read_launch_log_tail",
]

#: The file both sides agree on, directly inside the project folder. It is the file the
#: project's own launcher writes when it is started from the desktop shortcut.
LAUNCH_LOG_NAME = "launch.log"

#: How many lines one launch refusal publishes from that file. The panel shows the last
#: three and folds the rest, so this is a tail, not a transcript.
LAUNCH_LOG_TAIL_LINES = 20

#: The hard bound of the file itself. Past it the oldest lines are dropped, never the newest.
MAX_LAUNCH_LOG_BYTES = 64 * 1024

#: One appended block is bounded too: a header plus the run's own tail is enough to explain
#: a failure, and a child that printed a megabyte cannot make this write unbounded.
MAX_APPENDED_LINES = 40


def launch_log_path(workspace: Any) -> Optional[Path]:
    """``<project>\\launch.log`` for a workspace value, or ``None`` when there is none."""

    root = _workspace_path(workspace)
    if root is None:
        return None
    return root / LAUNCH_LOG_NAME


def read_launch_log_tail(
    workspace: Any, limit: int = LAUNCH_LOG_TAIL_LINES
) -> List[str]:
    """The last ``limit`` redacted, non-empty lines of the project's launcher log.

    Only a regular file inside the project folder is read, and only its bounded tail: the
    file belongs to the project, but its size belongs to whoever wrote it last. A file that
    is missing, is a directory or refuses to be opened answers an empty list -- the launch
    refusal then simply has no second log to show.
    """

    path = launch_log_path(workspace)
    if path is None:
        return []
    try:
        if not path.is_file():
            return []
        size = path.stat().st_size
        offset = max(0, size - MAX_LAUNCH_LOG_BYTES)
        with path.open("rb") as handle:
            if offset:
                handle.seek(offset)
            raw = handle.read()
    except OSError:
        return []
    text = raw.decode("utf-8", "replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = []
    for line in text.splitlines():
        cleaned = redact_output_text(line.rstrip("\r"))
        if cleaned.strip() == "":
            continue
        lines.append(cleaned)
    count = max(1, int(limit))
    return lines[-count:]


def append_launch_log(workspace: Any, reason: str, lines: Iterable[Any] = ()) -> bool:
    """Append one redacted block about a failed launch; best effort, never raises.

    The block starts with a timestamped line naming the failure in plain words and then
    carries the run's own (already redacted) tail. It is what a *desktop shortcut* press
    leaves behind on the way out, so Forge's next press can show the same reason -- and what
    Forge itself leaves behind when it is the one that failed, so both ways converge.

    A write that the folder does not allow (a read-only project, a vanished folder) answers
    ``False`` and changes nothing: a log that cannot be written must never fail a launch.
    """

    path = launch_log_path(workspace)
    if path is None:
        return False
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    block = [f"[{stamp}] Forge: {str(reason or '').strip() or 'запуск не удался'}"]
    kept = 0
    for raw in lines:
        if kept >= MAX_APPENDED_LINES:
            break
        text = redact_output_text(str(raw or "").rstrip("\r\n"))
        if text.strip() == "":
            continue
        block.append(text)
        kept += 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(block) + "\n")
    except OSError:
        return False
    _trim_launch_log(path)
    return True


def _trim_launch_log(path: Path) -> None:
    """Keep the newest :data:`MAX_LAUNCH_LOG_BYTES` bytes; a failure here is not fatal."""

    try:
        if path.stat().st_size <= MAX_LAUNCH_LOG_BYTES:
            return
        with path.open("rb") as handle:
            handle.seek(-MAX_LAUNCH_LOG_BYTES, os.SEEK_END)
            raw = handle.read()
        text = raw.decode("utf-8", "replace")
        # Drop the first, possibly half, line so the file starts on a real one.
        marker = text.find("\n")
        if marker >= 0:
            text = text[marker + 1 :]
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except OSError:
        return


def _workspace_path(workspace: Any) -> Optional[Path]:
    """A path for a workspace value, or ``None`` when there is none."""

    if workspace is None:
        return None
    try:
        if isinstance(workspace, (str, os.PathLike)):
            return Path(workspace)
        return Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return None
