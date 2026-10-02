"""Anthropic provider placeholder; no network integration is configured."""

from app.agents.providers.base import UnconfiguredProvider


class AnthropicProvider(UnconfiguredProvider):
    """Provider metadata and interface placeholder for Anthropic/Claude."""

    PROVIDER_NAME = "anthropic"
    API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
