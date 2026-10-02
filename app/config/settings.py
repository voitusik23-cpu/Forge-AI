"""Environment-backed settings placeholder for future configuration."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Provider credentials are optional until provider adapters exist."""

    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    xai_api_key: str = ""


def load_settings() -> Settings:
    """Read provider credentials from the process environment, if present."""
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        xai_api_key=os.getenv("XAI_API_KEY", ""),
    )
