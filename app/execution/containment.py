"""OS-level containment for one launched project process (Windows, no administrator).

What this module **really does**, with no administrator rights and no new dependency:

* **A job object with limits.** ``CreateJobObjectW`` +
  ``SetInformationJobObject(JobObjectExtendedLimitInformation)`` with
  ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` (the tree dies when this process closes the
  handle, even after a crash), ``JOB_OBJECT_LIMIT_PROCESS_MEMORY`` (a per-process
  memory ceiling) and ``JOB_OBJECT_LIMIT_ACTIVE_PROCESS`` (a process-count ceiling).
  The values are read back with ``QueryInformationJobObject``: a limit that could not
  be set is reported as zero, never as "in effect".
* **A low-integrity child token.** The child is created suspended (``Popen`` with
  ``CREATE_SUSPENDED``), ``OpenProcessToken`` + ``SetTokenInformation``
  (``TokenIntegrityLevel``, ``S-1-16-4096``) lowers its primary token before it runs a
  single instruction, the level is read back with ``GetTokenInformation``, and the
  thread is resumed (Thread32First/OpenThread/ResumeThread, with ``NtResumeProcess``
  as a fallback). Lowering the integrity level needs no privilege: the process it
  applies to is this user's own child, and Windows lets an owner write its own token
  down.
* **A labelled project folder.** Before the child is lowered, its own folder is
  labelled low integrity with ``AddMandatoryAce`` + ``SetNamedSecurityInfoW``
  (``LABEL_SECURITY_INFORMATION``, inheritable, no-write-up), so a low-integrity child
  can still write **its own** files. Without this step a low child could not write
  even its own folder on a machine where Forge runs at medium integrity; that is why
  the label is prepared first and the token is only lowered when the folder really
  is writable at that level.
* **A measured write boundary.** A short-lived probe process is launched under the
  *same* containment (same job limits, same token treatment, same environment, same
  working directory) and tries to create a file in the project's own folder, to
  create a new file in the project store root, and to overwrite a record placed there
  by Forge. The result is recorded as ``writes_inside`` / ``writes_outside`` and is a
  measurement, not an assumption.

What it deliberately does **not** do -- and never claims:

* **No network isolation.** A default-deny firewall rule needs administrator rights
  (Windows Firewall / WFP), so the child inherits the machine's network. The payload
  says ``network: "inherited (not isolated)"``. ``--offline`` is a *declaration* only.
* **No administrator-only layers.** ``CreateProcessAsUser``/``CreateProcessWithTokenW``
  with a Safer-computed restricted token needs ``SeAssignPrimaryTokenPrivilege``;
  AppContainer needs the profile's capabilities and ACLs on every DLL the runtime
  loads; a network rule needs the firewall. The upgrade path is documented, not faked.
* **No protection at all when Forge already runs at the same integrity level as its
  own data.** Integrity levels separate a *lower* child from *higher* objects. If the
  whole tree (Forge, its store, its interpreters) is already low integrity, a low
  child can still write it -- and ``writes_outside`` then honestly reports
  ``allowed``.
* **No path virtualization, no sandbox for build steps.** The execution plane's build
  steps are unchanged; this module contains the project process that Forge starts for
  preview/run.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass, field
import os
from pathlib import Path
import secrets
import subprocess
from typing import Any, Dict, Mapping, Optional, Tuple

from app.execution.adapter import (
    assign_process_tree_job,
    close_process_tree_job,
    create_process_tree_job,
    process_tree_job_limits,
    set_process_tree_job_limits,
    terminate_process_tree,
)

__all__ = [
    "DEFAULT_MAX_PROCESSES",
    "DEFAULT_MEMORY_MB",
    "INTEGRITY_RESTRICTED",
    "INTEGRITY_UNCHANGED",
    "NETWORK_INHERITED",
    "SANDBOX_ENV_VAR",
    "SandboxLimits",
    "SandboxReport",
    "directory_integrity_level",
    "prepare_low_integrity_workspace",
    "probe_write_boundary",
    "process_integrity_level",
    "lower_process_integrity",
    "resume_process",
    "sandbox_settings_from_environment",
]

#: Passed to every launched child so a program can tell it runs under Forge's
#: containment. It is a **marker**, not a boundary.
SANDBOX_ENV_VAR = "FORGE_SANDBOX"

#: Integrity level words. The payload only ever uses
#: ``"restricted_token"`` / ``"unchanged"``; these are the measured levels.
INTEGRITY_RESTRICTED = "restricted_token"
INTEGRITY_UNCHANGED = "unchanged"

#: The one honest network fact. There is no default-deny here.
NETWORK_INHERITED = "inherited (not isolated)"

#: Defaults for the job object. Generous enough for a normal development server and
#: a build tool, small enough that one runaway child cannot take the machine down.
DEFAULT_MEMORY_MB = 1024
DEFAULT_MAX_PROCESSES = 16

#: Low mandatory level, ``WinLowLabelSid`` (``S-1-16-4096``).
_LOW_INTEGRITY_SID = "S-1-16-4096"
_LOW_INTEGRITY_RID = 0x1000
_MANDATORY_LABEL_RIDS = {
    0x0000: "untrusted",
    0x1000: "low",
    0x2000: "medium",
    0x3000: "high",
    0x4000: "system",
    0x5000: "protected",
}

_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_DEFAULT = 0x0080
_TOKEN_INTEGRITY_LEVEL = 25
_SE_GROUP_INTEGRITY = 0x00000020

_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

_CREATE_SUSPENDED = 0x00000004

_TH32CS_SNAPTHREAD = 0x00000004
_THREAD_SUSPEND_RESUME = 0x0002

_LABEL_SECURITY_INFORMATION = 0x00000010
_SE_FILE_OBJECT = 1
_ACL_REVISION = 2
_OBJECT_INHERIT_ACE = 0x1
_CONTAINER_INHERIT_ACE = 0x2
_SYSTEM_MANDATORY_LABEL_NO_WRITE_UP = 0x1
_SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11

#: How long the write-boundary probe may take. A probe that hangs is killed and the
#: boundary is reported as ``unknown`` -- never as "blocked".
PROBE_TIMEOUT_SECONDS = 8.0

#: Marker the probe prints so its answer cannot be confused with the child's output.
_PROBE_MARKER = "FORGE-SANDBOX-PROBE "

_PROBE_CODE = r"""
import json
import os
import sys

findings = {"pid": os.getpid()}
for name, target in (("inside", sys.argv[1]), ("outside_new", sys.argv[2]), ("outside_record", sys.argv[3])):
    try:
        handle = open(target, "a" if name == "outside_record" else "w", encoding="utf-8")
        try:
            handle.write("forge-sandbox-probe")
        finally:
            handle.close()
        findings[name] = "allowed"
    except OSError as exc:
        findings[name] = "blocked"
        findings[name + "_error"] = type(exc).__name__
    else:
        try:
            if name != "outside_record":
                os.unlink(target)
        except OSError:
            pass
print("FORGE-SANDBOX-PROBE " + json.dumps(findings))
"""


# --------------------------------------------------------------------------- #
# the reported facts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SandboxLimits:
    """What the job object is asked to enforce."""

    memory_mb: int = DEFAULT_MEMORY_MB
    max_processes: int = DEFAULT_MAX_PROCESSES

    def sanitized(self) -> "SandboxLimits":
        memory = int(self.memory_mb) if isinstance(self.memory_mb, int) else 0
        processes = int(self.max_processes) if isinstance(self.max_processes, int) else 0
        return SandboxLimits(memory_mb=max(0, memory), max_processes=max(0, processes))


@dataclass
class SandboxReport:
    """The truthful state of one run's containment, filled as the facts are known.

    Every field starts at its most conservative value: no job, no token change,
    ``writes_outside``/``writes_inside`` unknown. A run that never spawned a process
    (a refusal, a blocked kind) therefore cannot publish a contained state by default.
    """

    job_object: bool = False
    restricted_token: bool = False
    integrity: str = INTEGRITY_UNCHANGED
    integrity_level: str = "unknown"
    integrity_level_before: str = "unknown"
    writes_outside: str = "unknown"
    writes_inside: str = "unknown"
    network: str = NETWORK_INHERITED
    memory_mb: int = 0
    max_processes: int = 0
    active_process_limit: int = 0
    process_memory_limit_mb: int = 0
    kill_on_close: bool = False
    mechanism: str = ""
    workspace_label: str = "unknown"
    workspace_label_applied: bool = False
    probe_error: str = ""
    disabled: bool = False
    server_lifetime_seconds: float = 0.0
    notes: list = field(default_factory=list)

    @property
    def green(self) -> bool:
        """The one combination that may be shown as fully contained.

        It is deliberately strict: a job object alone, a token change alone, or an
        unmeasured write boundary is never "contained". The UI mirrors this rule.
        """

        return bool(
            self.job_object
            and self.restricted_token
            and self.writes_outside == "blocked"
        )

    def to_payload(self) -> Dict[str, Any]:
        """The ``sandbox`` block of the run payload."""

        return {
            "job_object": bool(self.job_object),
            "restricted_token": bool(self.restricted_token),
            "integrity": self.integrity,
            "writes_outside": self.writes_outside,
            "network": self.network,
            "limits": {
                "memory_mb": int(self.memory_mb),
                "max_processes": int(self.max_processes),
                "active_process_limit": int(self.active_process_limit),
            },
            "detail": {
                "integrity_level": self.integrity_level,
                "integrity_level_before": self.integrity_level_before,
                "writes_inside": self.writes_inside,
                "mechanism": self.mechanism,
                "kill_on_close": bool(self.kill_on_close),
                "process_memory_limit_mb": int(self.process_memory_limit_mb),
                "workspace_label": self.workspace_label,
                "probe_error": self.probe_error,
                "disabled": bool(self.disabled),
                "server_lifetime_seconds": float(self.server_lifetime_seconds),
                "notes": list(self.notes),
            },
        }


# --------------------------------------------------------------------------- #
# Win32 prototypes, declared once
# --------------------------------------------------------------------------- #

_KERNEL32: Optional["ctypes.WinDLL"] = None
_ADVAPI32: Optional["ctypes.WinDLL"] = None
_NTDLL: Optional["ctypes.WinDLL"] = None


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _Acl(ctypes.Structure):
    _fields_ = [
        ("AclRevision", ctypes.c_ubyte),
        ("Sbz1", ctypes.c_ubyte),
        ("AclSize", wintypes.WORD),
        ("AceCount", wintypes.WORD),
        ("Sbz2", wintypes.WORD),
    ]


class _AceHeader(ctypes.Structure):
    _fields_ = [
        ("AceType", ctypes.c_ubyte),
        ("AceFlags", ctypes.c_ubyte),
        ("AceSize", wintypes.WORD),
    ]


class _ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", wintypes.LONG),
        ("tpDeltaPri", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
    ]


def _kernel32() -> "ctypes.WinDLL":
    global _KERNEL32
    if _KERNEL32 is None:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        library.OpenProcess.restype = ctypes.c_void_p
        library.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        library.OpenProcessToken.restype = wintypes.BOOL
        library.OpenProcessToken.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.CloseHandle.restype = wintypes.BOOL
        library.CloseHandle.argtypes = [ctypes.c_void_p]
        library.LocalFree.restype = ctypes.c_void_p
        library.LocalFree.argtypes = [ctypes.c_void_p]
        library.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        library.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        library.Thread32First.restype = wintypes.BOOL
        library.Thread32First.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.Thread32Next.restype = wintypes.BOOL
        library.Thread32Next.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        library.OpenThread.restype = ctypes.c_void_p
        library.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        library.ResumeThread.restype = wintypes.DWORD
        library.ResumeThread.argtypes = [ctypes.c_void_p]
        _KERNEL32 = library
    return _KERNEL32


def _advapi32() -> "ctypes.WinDLL":
    global _ADVAPI32
    if _ADVAPI32 is None:
        library = ctypes.WinDLL("advapi32", use_last_error=True)
        library.GetTokenInformation.restype = wintypes.BOOL
        library.GetTokenInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        library.SetTokenInformation.restype = wintypes.BOOL
        library.SetTokenInformation.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        library.ConvertStringSidToSidW.restype = wintypes.BOOL
        library.ConvertStringSidToSidW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.GetNamedSecurityInfoW.restype = wintypes.DWORD
        library.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        library.SetNamedSecurityInfoW.restype = wintypes.DWORD
        library.SetNamedSecurityInfoW.argtypes = [
            wintypes.LPWSTR,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        library.InitializeAcl.restype = wintypes.BOOL
        library.InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
        library.AddMandatoryAce.restype = wintypes.BOOL
        library.AddMandatoryAce.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        _ADVAPI32 = library
    return _ADVAPI32


def _ntdll() -> "ctypes.WinDLL":
    global _NTDLL
    if _NTDLL is None:
        library = ctypes.WinDLL("ntdll")
        library.NtResumeProcess.restype = ctypes.c_long
        library.NtResumeProcess.argtypes = [ctypes.c_void_p]
        _NTDLL = library
    return _NTDLL


# --------------------------------------------------------------------------- #
# token integrity
# --------------------------------------------------------------------------- #


def _rid_of_integrity_sid(sid_pointer: int) -> Optional[int]:
    """The integrity RID encoded in a ``S-1-16-…`` SID, or ``None``.

    The SID is read in two steps -- the eight-byte header first, then exactly the
    bytes the header announces -- so the read can never run past the SID.
    """

    if not sid_pointer:
        return None
    try:
        header = ctypes.string_at(sid_pointer, 8)
        count = header[1]
        if count < 1:
            return None
        raw = ctypes.string_at(sid_pointer, 8 + 4 * count)
    except (OSError, ValueError):  # pragma: no cover - a damaged SID
        return None
    offset = 8 + 4 * (count - 1)
    return int.from_bytes(raw[offset : offset + 4], "little")


def _read_token_integrity(token: int) -> Optional[int]:
    size = wintypes.DWORD(0)
    advapi = _advapi32()
    advapi.GetTokenInformation(
        ctypes.c_void_p(token), _TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size)
    )
    if not size.value:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if not advapi.GetTokenInformation(
        ctypes.c_void_p(token),
        _TOKEN_INTEGRITY_LEVEL,
        buffer,
        size.value,
        ctypes.byref(size),
    ):
        return None
    label = ctypes.cast(buffer, ctypes.POINTER(_SidAndAttributes)).contents
    return _rid_of_integrity_sid(label.Sid)


def process_integrity_level(pid: object) -> str:
    """The integrity level word for ``pid`` (``low``/``medium``/…/``unknown``)."""

    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return "unknown"
    kernel = _kernel32()
    handle = kernel.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        return "unknown"
    try:
        token = ctypes.c_void_p()
        if not kernel.OpenProcessToken(
            ctypes.c_void_p(handle), _TOKEN_QUERY, ctypes.byref(token)
        ):
            return "unknown"
        try:
            rid = _read_token_integrity(token.value)
        finally:
            kernel.CloseHandle(token)
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))
    if rid is None:
        return "unknown"
    return _MANDATORY_LABEL_RIDS.get(rid, "unknown")


def lower_process_integrity(process: "subprocess.Popen[bytes]") -> Tuple[bool, str]:
    """Lower a **suspended** child's primary token to low integrity.

    Returns ``(applied, level_after)``. ``applied`` is only true when
    ``SetTokenInformation`` accepted the change *and* ``GetTokenInformation`` read the
    low level back: a call that failed is never reported as a contained token.
    """

    if os.name != "nt":
        return False, "unknown"
    before = process_integrity_level(getattr(process, "pid", 0))
    kernel = _kernel32()
    advapi = _advapi32()
    handle = kernel.OpenProcess(
        _PROCESS_QUERY_INFORMATION | _PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        int(process.pid),
    )
    if not handle:
        return False, before
    try:
        token = ctypes.c_void_p()
        if not kernel.OpenProcessToken(
            ctypes.c_void_p(handle),
            _TOKEN_QUERY | _TOKEN_ADJUST_DEFAULT,
            ctypes.byref(token),
        ):
            return False, before
        try:
            sid = ctypes.c_void_p()
            if not advapi.ConvertStringSidToSidW(
                _LOW_INTEGRITY_SID, ctypes.byref(sid)
            ):
                return False, before
            try:
                label = _SidAndAttributes(sid, _SE_GROUP_INTEGRITY)
                if not advapi.SetTokenInformation(
                    token,
                    _TOKEN_INTEGRITY_LEVEL,
                    ctypes.byref(label),
                    ctypes.sizeof(label),
                ):
                    return False, before
            finally:
                kernel.LocalFree(sid)
            rid = _read_token_integrity(token.value)
        finally:
            kernel.CloseHandle(token)
    finally:
        kernel.CloseHandle(ctypes.c_void_p(handle))
    level = _MANDATORY_LABEL_RIDS.get(rid, "unknown") if rid is not None else "unknown"
    return (rid == _LOW_INTEGRITY_RID, level)


def resume_process(process: "subprocess.Popen[bytes]") -> bool:
    """Resume every thread of a ``CREATE_SUSPENDED`` child.

    ``Thread32First``/``OpenThread``/``ResumeThread`` are the documented APIs;
    ``NtResumeProcess`` is the documented-free fallback. A child that cannot be
    resumed is killed by the caller instead of being left suspended forever.
    """

    if os.name != "nt":
        return True
    pid = int(getattr(process, "pid", 0) or 0)
    if pid <= 0:
        return False
    kernel = _kernel32()
    snapshot = kernel.CreateToolhelp32Snapshot(_TH32CS_SNAPTHREAD, 0)
    invalid = ctypes.c_void_p(-1).value
    resumed = 0
    if snapshot and snapshot != invalid:
        try:
            entry = _ThreadEntry32()
            entry.dwSize = ctypes.sizeof(_ThreadEntry32)
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if int(entry.th32OwnerProcessID) == pid:
                    thread = kernel.OpenThread(
                        _THREAD_SUSPEND_RESUME, False, int(entry.th32ThreadID)
                    )
                    if thread:
                        if int(kernel.ResumeThread(ctypes.c_void_p(thread))) != 0xFFFFFFFF:
                            resumed += 1
                        kernel.CloseHandle(ctypes.c_void_p(thread))
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
    if resumed:
        return True
    try:
        return int(_ntdll().NtResumeProcess(ctypes.c_void_p(int(process._handle)))) == 0
    except Exception:  # noqa: BLE001 - no documented way left to resume
        return False


# --------------------------------------------------------------------------- #
# mandatory label of a directory
# --------------------------------------------------------------------------- #


def directory_integrity_level(path: object) -> str:
    """The mandatory label word of ``path`` (``low``/``medium``/``unknown``)."""

    if os.name != "nt":
        return "unknown"
    try:
        name = str(Path(str(path)))
    except (TypeError, ValueError):
        return "unknown"
    advapi = _advapi32()
    kernel = _kernel32()
    sacl = ctypes.c_void_p()
    descriptor = ctypes.c_void_p()
    code = advapi.GetNamedSecurityInfoW(
        name,
        _SE_FILE_OBJECT,
        _LABEL_SECURITY_INFORMATION,
        None,
        None,
        None,
        ctypes.byref(sacl),
        ctypes.byref(descriptor),
    )
    if code != 0:
        return "unknown"
    try:
        if not sacl.value:
            # No label at all: the object is treated as medium by the kernel's
            # default policy, and saying "unknown" here would be less useful than
            # saying what an integrity check actually does with it.
            return "medium"
        acl = ctypes.cast(sacl, ctypes.POINTER(_Acl)).contents
        offset = ctypes.sizeof(_Acl)
        base = ctypes.cast(sacl, ctypes.POINTER(ctypes.c_ubyte))
        for _ in range(int(acl.AceCount)):
            header = ctypes.cast(
                ctypes.byref(base.contents, offset), ctypes.POINTER(_AceHeader)
            ).contents
            if int(header.AceType) == _SYSTEM_MANDATORY_LABEL_ACE_TYPE:
                sid = ctypes.addressof(base.contents) + offset + 8
                rid = _rid_of_integrity_sid(sid)
                return _MANDATORY_LABEL_RIDS.get(rid, "unknown") if rid is not None else "unknown"
            offset += int(header.AceSize)
        return "unknown"
    finally:
        kernel.LocalFree(ctypes.c_void_p(descriptor.value))


def _label_directory_low(path: str) -> bool:
    """Write a low-integrity, inheritable, no-write-up label onto ``path``."""

    advapi = _advapi32()
    kernel = _kernel32()
    sid = ctypes.c_void_p()
    if not advapi.ConvertStringSidToSidW(_LOW_INTEGRITY_SID, ctypes.byref(sid)):
        return False
    try:
        # ACL header (8) + ACE header (4) + ACCESS_MASK (4) + SID (12) + slack.
        buffer = ctypes.create_string_buffer(128)
        acl = ctypes.cast(buffer, ctypes.c_void_p)
        if not advapi.InitializeAcl(acl, 128, _ACL_REVISION):
            return False
        if not advapi.AddMandatoryAce(
            acl,
            _ACL_REVISION,
            _OBJECT_INHERIT_ACE | _CONTAINER_INHERIT_ACE,
            _SYSTEM_MANDATORY_LABEL_NO_WRITE_UP,
            sid,
        ):
            return False
        code = advapi.SetNamedSecurityInfoW(
            path,
            _SE_FILE_OBJECT,
            _LABEL_SECURITY_INFORMATION,
            None,
            None,
            None,
            None,
            acl,
        )
        return code == 0
    finally:
        kernel.LocalFree(sid)


def prepare_low_integrity_workspace(path: object) -> Dict[str, Any]:
    """Make the project's own folder writable by a low-integrity child.

    Returns ``{"low": bool, "label": str, "applied": bool}``. ``low`` is true only
    when the folder's mandatory label really is low afterwards -- the token is only
    lowered when this is true, because a low child that cannot write its own folder
    is a broken run, not a contained one.
    """

    result: Dict[str, Any] = {"low": False, "label": "unknown", "applied": False}
    if os.name != "nt":
        return result
    try:
        target = str(Path(str(path)))
    except (TypeError, ValueError):
        return result
    if not target or not os.path.isdir(target):
        return result
    current = directory_integrity_level(target)
    result["label"] = current
    if current == "low":
        result["low"] = True
        return result
    if _label_directory_low(target):
        result["applied"] = True
        result["label"] = directory_integrity_level(target)
        result["low"] = result["label"] == "low"
    return result


# --------------------------------------------------------------------------- #
# the measured write boundary
# --------------------------------------------------------------------------- #


def probe_write_boundary(
    *,
    interpreter: str,
    cwd: object,
    environment: Mapping[str, str],
    workspace: object,
    outside_directory: object,
    limits: SandboxLimits,
    drop_integrity: bool,
) -> Dict[str, Any]:
    """Measure what a process under this containment can write, and report it.

    The probe is a **separate, short-lived process launched with the same job limits,
    the same token treatment, the same environment and the same working directory as
    the child**. It tries three writes:

    * ``inside`` -- a new file in the project's own folder (must work);
    * ``outside_new`` -- a new file in the project store root (Forge's own data);
    * ``outside_record`` -- an existing record in that root, placed there by Forge.

    The answer is a measurement. ``unknown`` means "the probe could not answer", never
    "blocked".
    """

    reply: Dict[str, Any] = {
        "writes_inside": "unknown",
        "writes_outside": "unknown",
        "outside_new": "unknown",
        "outside_record": "unknown",
        "integrity_level": "unknown",
        "probe_error": "",
    }
    if os.name != "nt":
        reply["probe_error"] = "unsupported_platform"
        return reply
    try:
        workspace_path = Path(str(workspace))
        outside_path = Path(str(outside_directory))
        working = Path(str(cwd))
    except (TypeError, ValueError):
        reply["probe_error"] = "bad_paths"
        return reply
    if not working.is_dir() or not outside_path.is_dir():
        reply["probe_error"] = "missing_directory"
        return reply
    token = secrets.token_hex(6)
    inside_target = workspace_path / f".forge-sandbox-probe-{token}.txt"
    outside_new = outside_path / f".forge-sandbox-probe-{token}.txt"
    outside_record = outside_path / f".forge-sandbox-probe-{token}.json"
    try:
        outside_record.write_text("{}", encoding="utf-8")
    except OSError:
        reply["probe_error"] = "record_not_created"
        return reply

    job: Optional[int] = None
    process: Optional["subprocess.Popen[bytes]"] = None
    try:
        job = create_process_tree_job()
        if job is not None:
            applied_limits = set_process_tree_job_limits(
                job,
                memory_mb=limits.memory_mb,
                max_processes=limits.max_processes,
            )
            if applied_limits is None:
                # Limits that cannot be set must not silently look like limits.
                close_process_tree_job(job)
                job = None
        creationflags = _CREATE_SUSPENDED if drop_integrity else 0
        process = subprocess.Popen(
            [
                str(interpreter),
                "-c",
                _PROBE_CODE,
                str(inside_target),
                str(outside_new),
                str(outside_record),
            ],
            cwd=str(working),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
            start_new_session=False,
        )
        if job is not None and not assign_process_tree_job(job, process):
            close_process_tree_job(job)
            job = None
        if drop_integrity:
            applied, level = lower_process_integrity(process)
            reply["integrity_level"] = level
            if not applied:
                reply["probe_error"] = "integrity_not_lowered"
            if not resume_process(process):
                reply["probe_error"] = "resume_failed"
        stdout, _stderr = process.communicate(timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        reply["probe_error"] = "probe_timeout"
    except Exception as exc:  # noqa: BLE001 - a failed probe is "unknown", not a claim
        reply["probe_error"] = type(exc).__name__
    finally:
        if process is not None and process.poll() is None:
            terminate_process_tree(process, job=job)
        if process is not None:
            try:
                process.communicate(timeout=2)
            except Exception:  # noqa: BLE001 - the process is already gone
                pass
        if job is not None:
            # KILL_ON_JOB_CLOSE: closing the last handle also kills anything left.
            close_process_tree_job(job)
        for leftover in (inside_target, outside_new, outside_record):
            try:
                leftover.unlink()
            except OSError:
                pass

    text = (stdout or b"").decode("utf-8", "replace") if isinstance(stdout, bytes) else (stdout or "")
    line = next(
        (item for item in text.splitlines() if item.startswith(_PROBE_MARKER)), ""
    )
    if not line:
        if not reply["probe_error"]:
            reply["probe_error"] = "no_answer"
        return reply
    try:
        import json as _json

        findings = _json.loads(line[len(_PROBE_MARKER) :])
        if not isinstance(findings, dict):
            raise ValueError("not an object")
    except ValueError:
        reply["probe_error"] = "unparsable_answer"
        return reply
    inside = str(findings.get("inside", "unknown"))
    new_outside = str(findings.get("outside_new", "unknown"))
    record_outside = str(findings.get("outside_record", "unknown"))
    if inside not in ("allowed", "blocked"):
        inside = "unknown"
    if new_outside not in ("allowed", "blocked"):
        new_outside = "unknown"
    if record_outside not in ("allowed", "blocked"):
        record_outside = "unknown"
    reply["writes_inside"] = inside
    reply["outside_new"] = new_outside
    reply["outside_record"] = record_outside
    if "blocked" in (new_outside, record_outside) and "allowed" not in (
        new_outside,
        record_outside,
    ):
        reply["writes_outside"] = "blocked"
    elif "allowed" in (new_outside, record_outside):
        reply["writes_outside"] = "allowed"
    return reply


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #


def _positive_int(name: str) -> Optional[int]:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def _positive_float(name: str) -> Optional[float]:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value > 0 else None


def sandbox_settings_from_environment() -> Dict[str, Any]:
    """The configurable limits, read once at composition.

    ``FORGE_SANDBOX_MEMORY_MB`` and ``FORGE_SANDBOX_MAX_PROCESSES`` bound the job
    object (defaults: 1024 MiB and 16). ``FORGE_SANDBOX_SERVER_LIFETIME_S`` is a
    maximum wall-clock lifetime for a *serving* process; unset means none, and a
    one-shot command keeps its own run timeout.
    """

    return {
        "memory_mb": _positive_int("FORGE_SANDBOX_MEMORY_MB") or DEFAULT_MEMORY_MB,
        "max_processes": _positive_int("FORGE_SANDBOX_MAX_PROCESSES")
        or DEFAULT_MAX_PROCESSES,
        "server_lifetime": _positive_float("FORGE_SANDBOX_SERVER_LIFETIME_S"),
    }
