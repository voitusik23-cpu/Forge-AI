"""OpenRouter provider using its OpenAI-compatible Chat Completions API."""

from app.agents.providers.openai_compatible import OpenAICompatibleProvider


class OpenRouterProvider(OpenAICompatibleProvider):
    """Call any configured OpenRouter model ID through the common adapter."""

    PROVIDER_NAME = "openrouter"
    API_KEY_ENV_VAR = "OPENROUTER_API_KEY"
    BASE_URL = "https://openrouter.ai/api/v1"
    FREE_MODEL_ID = "openrouter/free"
    DEFAULT_MODEL_ID = "cohere/north-mini-code:free"
