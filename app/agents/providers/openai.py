"""OpenAI provider placeholder; no network integration is configured."""

from app.agents.providers.base import UnconfiguredProvider


class OpenAIProvider(UnconfiguredProvider):
    """Provider metadata and interface placeholder for OpenAI."""

    PROVIDER_NAME = "openai"
    API_KEY_ENV_VAR = "OPENAI_API_KEY"
