"""DeepSeek provider using its OpenAI-compatible Chat Completions API."""

from app.agents.providers.openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """Call DeepSeek through the existing OpenAI SDK and common adapter."""

    PROVIDER_NAME = "deepseek"
    API_KEY_ENV_VAR = "DEEPSEEK_API_KEY"
    BASE_URL = "https://api.deepseek.com"
