"""xAI provider placeholder; no network integration is configured."""

from app.agents.providers.base import UnconfiguredProvider


class XAIProvider(UnconfiguredProvider):
    """Provider metadata and interface placeholder for xAI/Grok."""

    PROVIDER_NAME = "xai"
    API_KEY_ENV_VAR = "XAI_API_KEY"
