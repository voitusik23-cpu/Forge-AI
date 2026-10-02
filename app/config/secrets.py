"""Lazy access to local secrets without copying them into runtime settings."""

import os
import re
from pathlib import Path
from typing import Mapping, Optional


_SECRET_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")


class SecretStore:
    """Resolve secrets from process environment, then the repo-root ``.env``.

    Values are read only when requested. They are never added to RuntimeSettings
    or ProviderConfig, and this class deliberately has no value-bearing repr.
    """

    def __init__(
        self,
        env_file: Optional[Path] = None,
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._env_file = (
            Path(env_file)
            if env_file is not None
            else Path(__file__).resolve().parents[2] / ".env"
        )
        self._environ = os.environ if environ is None else environ
        self._file_values: Optional[dict[str, str]] = None

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
        return value if value and value.strip() else None

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
