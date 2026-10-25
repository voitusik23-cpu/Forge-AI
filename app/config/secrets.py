"""Lazy access to local secrets without copying them into runtime settings."""

import os
import re
from pathlib import Path
from typing import Mapping, Optional

from app.config.windows_creds import CredentialBackend, default_credential_backend

_SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")


class SecretStore:
    """Resolve secrets from process environment, then the repo-root ``.env``, then the vault.

    Values are read only when requested. They are never added to RuntimeSettings
    or ProviderConfig, and this class deliberately has no value-bearing repr.

    The third source -- the operating system's credential vault
    (:mod:`app.config.windows_creds`) -- is what makes a key the owner added in
    «Настройки → Провайдеры и ключи» visible to the *existing* provider layer without a
    restart: every provider and every availability derivation resolves its key through
    this one method, so a new vault entry is found on the next call, and no second
    provider registry exists. The lookup is uncached and ordered, so a deployment's
    ``.env`` still wins over a key typed into the interface.
    """

    def __init__(
        self,
        env_file: Optional[Path] = None,
        environ: Optional[Mapping[str, str]] = None,
        credential_backend: Optional[CredentialBackend] = None,
    ) -> None:
        self._env_file = (
            Path(env_file)
            if env_file is not None
            else Path(__file__).resolve().parents[2] / ".env"
        )
        self._environ = os.environ if environ is None else environ
        self._file_values: Optional[dict[str, str]] = None
        # Injected by a test; otherwise resolved per call, never cached across calls: a
        # cached backend would hide the key the owner (or a test) just added.
        self._credential_backend = credential_backend

    def _vault(self) -> CredentialBackend:
        """The credential vault for this call, or a backend that holds nothing."""

        if self._credential_backend is not None:
            return self._credential_backend
        try:
            return default_credential_backend()
        except Exception:  # noqa: BLE001 - an unusable vault means "no key", never a crash
            from app.config.windows_creds import UnavailableCredentialBackend

            return UnavailableCredentialBackend()

    def get_secret(self, name: str) -> Optional[str]:
        """Return a non-empty secret by variable name, preferring process env."""
        if not isinstance(name, str) or not _SECRET_NAME.fullmatch(name):
            raise ValueError("Secret name must be an uppercase environment variable name")

        value = self._environ.get(name)
        if isinstance(value, str) and value.strip():
            return value

        if self._file_values is None:
            self._file_values = self._read_env_file()
        value = self._file_values.get(name)
        if value and value.strip():
            return value

        # THE KEY THE OWNER ADDED IN THE INTERFACE, read on every call. Any failure is
        # "no key here", which the availability derivation already reports honestly as
        # ``NO_CREDENTIALS``; a read must never raise into a status payload.
        try:
            stored = self._vault().get_secret(name)
        except Exception:  # noqa: BLE001 - see above
            return None
        return stored if isinstance(stored, str) and stored.strip() else None

    def secret_source(self, name: str) -> str:
        """Where :meth:`get_secret` finds ``name``: ``environment``, ``vault`` or ``""``.

        A fixed word, never a value or a part of one. It exists so the interface can tell
        the owner *where* his key comes from without any payload carrying it.
        """

        if not isinstance(name, str) or not _SECRET_NAME.fullmatch(name):
            raise ValueError("Secret name must be an uppercase environment variable name")

        value = self._environ.get(name)
        if isinstance(value, str) and value.strip():
            return "environment"
        if self._file_values is None:
            self._file_values = self._read_env_file()
        value = self._file_values.get(name)
        if value and value.strip():
            return "environment"
        try:
            stored = self._vault().get_secret(name)
        except Exception:  # noqa: BLE001 - an unreadable vault is "no source"
            return ""
        return "vault" if isinstance(stored, str) and stored.strip() else ""

    def has_secret(self, name: str) -> bool:
        """Presence only: one boolean, never the value."""

        return self.get_secret(name) is not None

    def secret_names(self) -> tuple[str, ...]:
        """Every secret *name* this store can resolve, sorted, and no value.

        A caller that needs to enumerate a numbered family (``RADOX_API_KEY_1``,
        ``RADOX_API_KEY_2``, ...) cannot do it by probing alone: the count is the
        owner's data, not a constant. This reports the names only -- the values
        stay behind :meth:`get_secret`, so a caller cannot accidentally serialize
        what is returned here.
        """

        names = {
            name for name in self._environ if _SECRET_NAME.fullmatch(str(name))
        }
        if self._file_values is None:
            self._file_values = self._read_env_file()
        names.update(self._file_values)
        # The vault's *names* only, through the same rule as every other source: a
        # numbered family (``RADOX_API_KEY_1``) added in the interface is enumerable
        # without the store ever handing out a value here.
        try:
            names.update(
                name
                for name in self._vault().list_secret_names()
                if _SECRET_NAME.fullmatch(str(name))
            )
        except Exception:  # noqa: BLE001 - an unreadable vault contributes no names
            pass
        return tuple(sorted(names))

    def _read_env_file(self) -> dict[str, str]:
        """Read simple KEY=VALUE entries; ignore comments and malformed lines."""
        try:
            contents = self._env_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError:
            # Do not include file contents or parser details in the exception.
            raise RuntimeError("Unable to read the local secret file") from None

        values: dict[str, str] = {}
        for line in contents.splitlines():
            entry = line.strip()
            if not entry or entry.startswith("#"):
                continue
            if entry.startswith("export "):
                entry = entry[7:].lstrip()
            name, separator, value = entry.partition("=")
            name = name.strip()
            if not separator or not _SECRET_NAME.fullmatch(name):
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[name] = value
        return values
