"""Google provider placeholder; no network integration is configured."""

from app.agents.providers.base import UnconfiguredProvider


class GoogleProvider(UnconfiguredProvider):
    """Provider metadata and interface placeholder for Google/Gemini."""

    PROVIDER_NAME = "google"
    API_KEY_ENV_VAR = "GEMINI_API_KEY"
