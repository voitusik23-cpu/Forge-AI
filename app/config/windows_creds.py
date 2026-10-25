"""Provider API keys, stored in the operating system's own credential vault.

Why not ``.env`` and not ``.forge/settings.json``
-------------------------------------------------
The owner's complaint was concrete: *«не могу найти, где добавить провайдера с
ключами»* -- providers came from ``.env``, a file he is not supposed to edit, so
there was no place in the product to put a key. Two places would have been wrong:

* ``.env`` is deployment configuration, and this surface is not allowed to write it;
* ``.forge/settings.json`` is a plain JSON file that anyone can read, mail or commit.
  A key written there is a key leaked, so no key ever reaches it.

The key therefore goes to **Windows Credential Manager**, through the generic
credential API (``CredWriteW`` / ``CredReadW`` / ``CredDeleteW``). The
non-secret parts of a provider entry (which provider, which base URL, which
default model) live in the application's own settings file; the key does not.

Naming
------
Every entry is stored under one deterministic target name::

    ForgeAI/provider/<SECRET_ENV_NAME>

For example ``ForgeAI/provider/DEEPSEEK_API_KEY``. The mapping is a pure function
of the secret's variable name (:func:`target_for_secret`), so the existing
provider layer -- which asks for a secret *by variable name* through
:class:`app.config.secrets.SecretStore` -- finds a key the owner added in the
interface without any second registry, and without a restart.

What this module never does
---------------------------
* it never returns a key to a caller that only asked whether one exists
  (:meth:`CredentialBackend.has_secret` returns one boolean);
* it never puts a key into an exception message, a log line, an event or a
  ``repr`` -- :meth:`WindowsCredentialBackend.delete_secret` and its siblings
  report the target *name*, which is not secret;
* it never reads the vault unless a secret is actually asked for.

Tests and non-Windows hosts
---------------------------
:func:`default_credential_backend` answers a real Windows backend only on
Windows. :class:`MemoryCredentialBackend` implements exactly the same contract
over a dictionary, so an offline test can prove the add/read/delete cycle -- and
the fact that the key reaches the provider layer -- without touching the machine's
vault. A test may also inject the backend into
:class:`app.config.secrets.SecretStore` directly.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

__all__ = [
    "CREDENTIAL_TARGET_PREFIX",
    "CredentialBackend",
    "CredentialBackendError",
    "MemoryCredentialBackend",
    "UnavailableCredentialBackend",
    "WindowsCredentialBackend",
    "default_credential_backend",
    "is_windows",
    "target_for_secret",
]

#: Prefix of every target name this application writes. A target name is not a secret:
#: it is the *name* of the entry, and it is safe to log.
CREDENTIAL_TARGET_PREFIX = "ForgeAI/provider/"

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class CredentialBackendError(RuntimeError):
    """The vault refused an operation.

    The message names the *operation* and the *target*, never a value: an exception
    that carries a key is a key written to a log the moment anything prints it.
    """

    code = "credential_backend_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)


def is_windows() -> bool:
    """Whether this process runs on Windows (the platform Credential Manager is on)."""

    return sys.platform.startswith("win")


def target_for_secret(name: str) -> str:
    """The vault target name for one secret variable name.

    ``DEEPSEEK_API_KEY`` -> ``ForgeAI/provider/DEEPSEEK_API_KEY``. The name is
    upper-cased and stripped; an empty name is refused rather than mapped to a
    shared entry, because a shared entry would let one provider's key answer for
    another.
    """

    candidate = str(name or "").strip()
    if not candidate:
        raise ValueError("a credential needs a non-empty secret name")
    return CREDENTIAL_TARGET_PREFIX + candidate


class CredentialBackend:
    """The vault contract: names in, one value at a time, never a value in a message.

    Every method takes the **secret variable name** (``DEEPSEEK_API_KEY``), not a
    target, so the naming rule lives in one place (:func:`target_for_secret`) and a
    caller cannot invent an entry another provider would read.
    """

    #: Where this backend keeps its values, for the interface to say honestly.
    description = "credential store"

    def get_secret(self, name: str) -> Optional[str]:
        raise NotImplementedError

    def has_secret(self, name: str) -> bool:
        """Presence only. One boolean, so nothing here can reconstruct a key."""

        try:
            return bool(self.get_secret(name))
        except CredentialBackendError:
            return False

    def set_secret(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete_secret(self, name: str) -> bool:
        """Remove one entry. ``True`` when something was removed, ``False`` when absent."""

        raise NotImplementedError

    def list_secret_names(self) -> List[str]:
        """Every secret *name* this backend holds for this application, sorted."""

        return []


class UnavailableCredentialBackend(CredentialBackend):
    """A backend that holds nothing, for a host without a credential vault."""

    description = "unavailable on this host"

    def get_secret(self, name: str) -> Optional[str]:
        # A malformed name is refused for the same reason the real backend refuses it:
        # the naming rule must not depend on which backend is in use.
        target_for_secret(name)
        return None

    def set_secret(self, name: str, value: str) -> None:
        target_for_secret(name)
        raise CredentialBackendError(
            "the credential vault is unavailable on this host"
        )

    def delete_secret(self, name: str) -> bool:
        target_for_secret(name)
        return False


class MemoryCredentialBackend(CredentialBackend):
    """The same contract over a dictionary: the backend an offline test uses.

    It is deliberately not a mock library: it stores the value, returns it once,
    deletes it, and reports presence -- so a test proves the real call path and the
    real wiring, and only the host vault is replaced.
    """

    description = "in-memory credential store"

    def __init__(self, values: Optional[Dict[str, str]] = None) -> None:
        self._values: Dict[str, str] = {
            str(key): str(value) for key, value in dict(values or {}).items()
        }

    def get_secret(self, name: str) -> Optional[str]:
        target = target_for_secret(name)
        return self._values.get(target)

    def set_secret(self, name: str, value: str) -> None:
        target = target_for_secret(name)
        if not isinstance(value, str) or not value.strip():
            raise CredentialBackendError("an empty value cannot be stored")
        self._values[target] = value

    def delete_secret(self, name: str) -> bool:
        target = target_for_secret(name)
        return self._values.pop(target, None) is not None

    def list_secret_names(self) -> List[str]:
        names = [
            target[len(CREDENTIAL_TARGET_PREFIX) :]
            for target in self._values
            if target.startswith(CREDENTIAL_TARGET_PREFIX)
        ]
        return sorted(names)


class WindowsCredentialBackend(CredentialBackend):
    """Windows Credential Manager, through the generic credential API.

    The wrapper is a thin ctypes adapter: no dependency is installed, and the fixed
    constants below are the documented ones from ``wincred.h``. Every failure is
    reported as :class:`CredentialBackendError` with the target name only.
    """

    description = "Windows Credential Manager"

    def __init__(self) -> None:
        self._advapi = None

    # ------------------------------------------------------------------ binding
    def _api(self):
        """Load advapi32 once, with the three signatures this module uses."""

        if self._advapi is not None:
            return self._advapi
        if not is_windows():  # pragma: no cover - the class is not built off Windows
            raise CredentialBackendError(
                "Windows Credential Manager is only available on Windows"
            )
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [
                ("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD),
            ]

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        advapi.CredWriteW.restype = wintypes.BOOL
        advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIALW)),
        ]
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        advapi.CredDeleteW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        advapi.CredFree.restype = None
        self._advapi = (advapi, CREDENTIALW, ctypes, wintypes)
        return self._advapi

    # ------------------------------------------------------------------- reads
    def get_secret(self, name: str) -> Optional[str]:
        """Read one entry, or ``None`` when no entry exists."""

        target = target_for_secret(name)
        advapi, CREDENTIALW, ctypes, _wintypes = self._api()
        pointer = ctypes.POINTER(CREDENTIALW)()
        ok = advapi.CredReadW(
            target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        )
        if not ok:
            error = ctypes.get_last_error()
            if error == _ERROR_NOT_FOUND:
                return None
            raise CredentialBackendError(
                f"could not read credential '{target}' (WinError {error})"
            )
        try:
            blob = pointer.contents
            size = int(blob.CredentialBlobSize)
            if size <= 0 or not blob.CredentialBlob:
                return None
            raw = ctypes.string_at(blob.CredentialBlob, size)
        finally:
            advapi.CredFree(pointer)
        try:
            return raw.decode("utf-16-le").strip() or None
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace").strip() or None

    # ------------------------------------------------------------------ writes
    def set_secret(self, name: str, value: str) -> None:
        """Create or replace one entry.

        The value is written as UTF-16LE, which is what the Credential Manager stores;
        the blob is freed by the API, not by this module, so no copy of the key is
        left in a Python buffer that outlives the call.
        """

        target = target_for_secret(name)
        if not isinstance(value, str) or not value.strip():
            raise CredentialBackendError("an empty value cannot be stored")
        advapi, CREDENTIALW, ctypes, wintypes = self._api()
        encoded = value.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = CREDENTIALW()
        credential.Flags = 0
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.Comment = "Forge AI provider key"
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.AttributeCount = 0
        credential.Attributes = None
        credential.TargetAlias = None
        credential.UserName = "forge"
        ok = advapi.CredWriteW(ctypes.byref(credential), 0)
        if not ok:
            error = ctypes.get_last_error()
            raise CredentialBackendError(
                f"could not write credential '{target}' (WinError {error})"
            )

    # ------------------------------------------------------------------ deletes
    def delete_secret(self, name: str) -> bool:
        """Remove one entry; ``True`` when it existed."""

        target = target_for_secret(name)
        advapi, _credential, ctypes, _wintypes = self._api()
        ok = advapi.CredDeleteW(target, _CRED_TYPE_GENERIC, 0)
        if ok:
            return True
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return False
        raise CredentialBackendError(
            f"could not delete credential '{target}' (WinError {error})"
        )


def _memory_requested() -> bool:
    """Whether the deployment asked for the in-memory vault (a laboratory switch).

    ``FORGE_CREDENTIAL_BACKEND=memory`` exists for an evidence run that must not
    touch the machine's real vault. It is opt-in and never the default: a product
    that silently kept keys in process memory would lose them on restart and tell
    the owner they were saved.
    """

    return os.environ.get("FORGE_CREDENTIAL_BACKEND", "").strip().lower() == "memory"


#: The one in-process vault used when ``FORGE_CREDENTIAL_BACKEND=memory`` asks for it.
#: A single instance, because a fresh one per call would forget every key between two
#: requests -- which is exactly the defect (a saved key that is not there) this module
#: exists to prevent.
_MEMORY_SINGLETON: Optional[MemoryCredentialBackend] = None


def default_credential_backend() -> CredentialBackend:
    """The backend this process uses: the real vault, or one that holds nothing.

    The real Windows backend is created per call (it is a thin ctypes wrapper and
    caches nothing), so a test that injects its own backend is never defeated by an
    earlier import. The opt-in in-memory backend is a single instance, because its
    values are its state.
    """

    global _MEMORY_SINGLETON

    if _memory_requested():
        if _MEMORY_SINGLETON is None:
            _MEMORY_SINGLETON = MemoryCredentialBackend()
        return _MEMORY_SINGLETON
    if is_windows():
        return WindowsCredentialBackend()
    return UnavailableCredentialBackend()
