"""Local provider account email metadata, kept separate from API secrets."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


_ENV_FIELDS = {
    "openai": "PROVIDER_OPENAI_EMAIL",
    "anthropic": "PROVIDER_ANTHROPIC_EMAIL",
    "gemini": "PROVIDER_GEMINI_EMAIL",
    "xai": "PROVIDER_XAI_EMAIL",
    "deepseek": "PROVIDER_DEEPSEEK_EMAIL",
    "openrouter": "PROVIDER_OPENROUTER_EMAIL",
    "groq": "PROVIDER_GROQ_EMAIL",
}


@dataclass(frozen=True, repr=False)
class ProviderAccountConfig:
    """Optional account email references; no API keys or provider results."""

    openai_email: Optional[str] = None
    anthropic_email: Optional[str] = None
    gemini_email: Optional[str] = None
    xai_email: Optional[str] = None
    deepseek_email: Optional[str] = None
    openrouter_email: Optional[str] = None
    groq_email: Optional[str] = None

    def email_for(self, provider_name: str) -> Optional[str]:
        """Return configured account metadata for a canonical provider name."""
        if provider_name not in _ENV_FIELDS:
            return None
        return getattr(self, f"{provider_name}_email")

    def __repr__(self) -> str:
        return "ProviderAccountConfig(<local account metadata>)"


def load_provider_account_config(
    env_file: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> ProviderAccountConfig:
    """Load only allowlisted provider email fields from env and local `.env`.

    Process environment values take precedence. The parser ignores every
    non-email entry, so API credentials stay in the separate SecretStore.
    """
    source = os.environ if environ is None else environ
    path = (
        Path(env_file)
        if env_file is not None
        else Path(__file__).resolve().parents[2] / ".env"
    )
    file_values = _read_account_values(path)
    values = {}
    for provider_name, env_name in _ENV_FIELDS.items():
        value = source.get(env_name)
        if not isinstance(value, str) or not value.strip():
            value = file_values.get(env_name)
        values[f"{provider_name}_email"] = value.strip() if value and value.strip() else None
    return ProviderAccountConfig(**values)


def _read_account_values(path: Path) -> dict[str, str]:
    try:
        contents = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        raise RuntimeError("Unable to read provider account metadata file") from None

    allowed_names = set(_ENV_FIELDS.values())
    values = {}
    for line in contents.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        name, separator, value = entry.partition("=")
        name = name.strip()
        if not separator or name not in allowed_names:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name] = value
    return values
