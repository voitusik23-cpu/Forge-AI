"""Remember on this machine which browser cannot start its own sandbox.

The owner's words: *«опять вылезло старое белое меню»* -- while the window that really
opened was his own application. What he was looking at was the **sandboxed** attempt of a
Chromium browser: on this machine such a browser is started by Forge inside its own job
object, and its renderer sandbox aborts with ``0x80000003`` (``STATUS_BREAKPOINT``) after
about half a second. The retry without the sandbox renders the application in the same
second. So every launch began with half a second of a blank white window whose only
outcome was to be replaced by the real one.

The fix is a memory, not a new policy: a browser that has **measured** itself unable to
start its sandbox is not asked to try it again, unless the owner says otherwise.

What is remembered
------------------

One key of the owner's own settings file (``.forge/settings.json``), per browser and per
machine::

    "window_plan_memory": {
        "chrome.exe": {
            "no_sandbox_required": true,
            "sandbox_failures": 3,
            "failed_at": "2026-10-29T01:05:20+02:00",
            "reason": "0x80000003"
        }
    }

The key is the browser's file name (``chrome.exe``), so two browsers on one machine are
remembered separately. ``no_sandbox_required`` is what :meth:`WindowPlanMemory.
requires_no_sandbox` answers; ``sandbox_failures`` is the counter described below;
``failed_at``/``reason`` are what «Настройки» prints and what a person reading the file
needs to understand *why*.

When a browser is remembered
----------------------------

Two rules, and the second one exists because one crash is not a measurement:

* **The documented abort code marks at once.** An exit whose code is
  :data:`SANDBOX_ABORT_CODE` (``0x80000003``) is the measured "this host cannot start this
  browser's sandbox"; there is nothing ambiguous about it, so the browser is remembered at
  the first such exit.
* **Any other exit inside the grace period has to happen three times in a row.** A browser
  that exits inside :data:`app.projects.app_window.WINDOW_GRACE_SECONDS` for another reason
  -- a handoff to an instance holding the profile, an antivirus kill, a corrupt profile --
  is not proof that its sandbox cannot start. Three such exits in a row for the **same**
  browser are. The counter is persisted, so it survives a restart of Forge.
* **A sandboxed success resets it.** A browser whose sandboxed attempt really produced a
  window is not broken: its entry -- counter and mark together -- is dropped, so a machine
  that was repaired is not permanently penalised.

What it is *not*
----------------

This module never starts, stops or even chooses a browser; that stays in
:mod:`app.projects.app_window`. It also never overrides the owner: the «Настройки» switch
«Разрешать повтор без песочницы» is read at every launch, and with it off the memory is
ignored and the sandboxed attempt is tried again (and fails honestly).

The settings file belongs to the deployment, so a failure to read or write it is never
allowed to fail a launch: :meth:`WindowPlanMemory.entries` answers ``{}`` for an unreadable
file, and the opener treats a broken memory as "nothing is remembered".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.app_settings import (
    SETTINGS_SCHEMA_VERSION,
    default_settings_path,
    read_settings_document,
    write_settings_document,
)

__all__ = [
    "MARK_AFTER_FAILURES",
    "SANDBOX_ABORT_CODE",
    "WINDOW_PLAN_MEMORY_KEY",
    "SandboxFailure",
    "WindowPlanMemory",
    "exit_code_text",
    "is_sandbox_abort",
]

#: The one key of ``.forge/settings.json`` this module owns.
WINDOW_PLAN_MEMORY_KEY = "window_plan_memory"

#: ``STATUS_BREAKPOINT``: the measured way a Chromium browser started by this product ends
#: when its own renderer sandbox cannot initialise. It is the code the project's own
#: ``launch.log`` has recorded on this machine (``exit 2147483651``), and the code
#: :data:`app.projects.app_window.SANDBOX_DOWNGRADE_NOTE` names in words.
SANDBOX_ABORT_CODE = 0x80000003

#: How many consecutive exits inside the grace period mark a browser when the code is not
#: the documented abort. Persisted, because one launch tries a browser's sandbox once.
MARK_AFTER_FAILURES = 3


def _now_iso() -> str:
    """The timestamp written into the memory: local time, with its own UTC offset.

    Local rather than UTC because this one value is read by the owner: «Настройки» prints
    its *day and month* («29.10»), and a UTC stamp taken just after midnight would print
    the previous day on this host (UTC+02:00). The offset is kept, so the instant stays
    unambiguous for anyone reading the file.
    """

    return datetime.now().astimezone().isoformat(timespec="seconds")


def _unsigned(code: object) -> Optional[int]:
    """A process exit code as Windows reports it, as an unsigned 32-bit integer."""

    if isinstance(code, bool) or not isinstance(code, int):
        try:
            value = int(code)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
    else:
        value = int(code)
    if value < 0:
        value += 2**32
    return value


def is_sandbox_abort(code: object) -> bool:
    """Whether ``code`` is the documented "the browser's sandbox cannot start" abort."""

    return _unsigned(code) == SANDBOX_ABORT_CODE


def exit_code_text(code: object) -> str:
    """``0x80000003`` for the documented abort, the decimal code for anything else."""

    value = _unsigned(code)
    if value is None:
        return ""
    if value == SANDBOX_ABORT_CODE:
        return "0x80000003"
    return str(value)


@dataclass(frozen=True)
class SandboxFailure:
    """What one recorded sandboxed failure did to the memory of one browser."""

    program: str
    failures: int
    marked: bool
    marked_now: bool
    reason: str
    failed_at: str


def _program_key(program: object) -> str:
    """The memory key of a browser: its file name, lower-cased."""

    text = str(program or "").strip()
    if not text:
        return ""
    name = Path(text).name.strip()
    return name.lower()


class WindowPlanMemory:
    """The per-browser, per-machine memory of window plans that cannot start their sandbox.

    ``path`` is the settings file this memory lives in (``.forge/settings.json`` by
    default, or whatever ``FORGE_SETTINGS_FILE`` names). Every read and write is of that
    one key: the project root, the provider entries and the «Настройки» switches in the
    same file are left exactly as another writer left them.
    """

    def __init__(self, path: Optional[object] = None) -> None:
        self._path = Path(path) if path is not None else default_settings_path()

    @property
    def path(self) -> Path:
        return self._path

    # ------------------------------------------------------------------- read
    def entries(self) -> Dict[str, Dict[str, Any]]:
        """Every remembered browser, normalised; ``{}`` for an unreadable or absent file.

        A value that is not a dict of dicts is ignored rather than repaired: a corrupt
        memory must answer "nothing is remembered", never invent a browser entry.
        """

        raw = read_settings_document(self._path).get(WINDOW_PLAN_MEMORY_KEY)
        if not isinstance(raw, dict):
            return {}
        found: Dict[str, Dict[str, Any]] = {}
        for program, entry in raw.items():
            key = _program_key(program)
            if not key or not isinstance(entry, dict):
                continue
            failures = entry.get("sandbox_failures")
            found[key] = {
                "no_sandbox_required": entry.get("no_sandbox_required") is True,
                "sandbox_failures": int(failures)
                if type(failures) is int and failures > 0
                else 0,
                "failed_at": str(entry.get("failed_at") or ""),
                "reason": str(entry.get("reason") or ""),
            }
        return found

    def entry(self, program: object) -> Dict[str, Any]:
        """The normalised entry of one browser, or ``{}`` when nothing is remembered."""

        return dict(self.entries().get(_program_key(program), {}))

    def requires_no_sandbox(self, program: object) -> bool:
        """Whether this browser is remembered as unable to start its own sandbox."""

        return bool(self.entry(program).get("no_sandbox_required"))

    def describe(self) -> List[Dict[str, Any]]:
        """The memory as «Настройки» shows it: one dict per browser, sorted by name."""

        found = [
            {"program": program, **entry} for program, entry in self.entries().items()
        ]
        found.sort(key=lambda item: str(item.get("program") or ""))
        return found

    # ------------------------------------------------------------------ write
    def _write(self, entries: Dict[str, Dict[str, Any]]) -> None:
        """Persist the whole memory into the one key, atomically, leaving the rest alone.

        ``updated_at`` belongs to the settings store's own writes and is deliberately not
        touched here: this module owns one key, and the time of a failure is the entry's
        own ``failed_at``.
        """

        document = read_settings_document(self._path)
        if entries:
            document[WINDOW_PLAN_MEMORY_KEY] = entries
        else:
            document.pop(WINDOW_PLAN_MEMORY_KEY, None)
        document.setdefault("schema", SETTINGS_SCHEMA_VERSION)
        write_settings_document(self._path, document)

    def record_sandbox_failure(
        self,
        program: object,
        *,
        code: object = None,
        inside_grace: bool = True,
    ) -> SandboxFailure:
        """Record that one browser's **sandboxed** attempt produced no window.

        ``code`` is the exit code the OS reported (``None`` when the attempt was not seen
        to exit); ``inside_grace`` says whether it exited inside
        ``WINDOW_GRACE_SECONDS``. The documented abort marks the browser at once; any other
        exit inside the grace period only counts, and the third one in a row marks it. An
        attempt that neither aborted with the documented code nor exited inside the grace
        period changes nothing -- it is not this memory's kind of failure.

        A browser that is already marked is left exactly as it is, so the timestamp and the
        reason stay the ones that first proved the fact.
        """

        key = _program_key(program)
        if not key:
            return SandboxFailure("", 0, False, False, "", "")
        entries = self.entries()
        previous = entries.get(key, {})
        if previous.get("no_sandbox_required"):
            return SandboxFailure(
                key,
                int(previous.get("sandbox_failures") or 0),
                True,
                False,
                str(previous.get("reason") or ""),
                str(previous.get("failed_at") or ""),
            )
        abort = is_sandbox_abort(code)
        if not abort and not inside_grace:
            return SandboxFailure(
                key,
                int(previous.get("sandbox_failures") or 0),
                False,
                False,
                str(previous.get("reason") or ""),
                str(previous.get("failed_at") or ""),
            )
        failures = int(previous.get("sandbox_failures") or 0) + 1
        marked = bool(abort or failures >= MARK_AFTER_FAILURES)
        reason = exit_code_text(code)
        updated = {
            "no_sandbox_required": marked,
            "sandbox_failures": failures,
            "failed_at": _now_iso(),
            "reason": reason,
        }
        entries[key] = updated
        self._write(entries)
        return SandboxFailure(
            key,
            failures,
            marked,
            marked,
            reason,
            str(updated["failed_at"]),
        )

    def record_sandbox_success(self, program: object) -> bool:
        """Forget one browser's failures: its sandboxed attempt really produced a window.

        The whole entry is dropped -- a counter left behind would mark a machine that just
        proved it works. Returns whether anything was forgotten.
        """

        key = _program_key(program)
        if not key:
            return False
        entries = self.entries()
        if key not in entries:
            return False
        entries.pop(key, None)
        self._write(entries)
        return True

    def forget(self) -> bool:
        """«Забыть»: clear the memory of **every** browser; returns whether it was there.

        The next launch then tries the sandboxed plan again and measures afresh. An empty
        memory is not rewritten: a press of «Забыть» on a machine that remembers nothing
        must not touch the owner's settings file at all.
        """

        document = read_settings_document(self._path)
        if WINDOW_PLAN_MEMORY_KEY not in document:
            return False
        document.pop(WINDOW_PLAN_MEMORY_KEY, None)
        document.setdefault("schema", SETTINGS_SCHEMA_VERSION)
        write_settings_document(self._path, document)
        return True
