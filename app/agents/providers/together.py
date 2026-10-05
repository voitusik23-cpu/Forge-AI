"""Together AI provider using its OpenAI-compatible Chat Completions API."""

from app.agents.providers.openai_compatible import OpenAICompatibleProvider


class TogetherProvider(OpenAICompatibleProvider):
    """Call any configured Together model ID through the common adapter."""

    PROVIDER_NAME = "together"
    API_KEY_ENV_VAR = "TOGETHER_API_KEY"
    BASE_URL = "https://api.together.ai/v1"
