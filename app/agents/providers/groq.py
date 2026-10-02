"""Groq provider using the existing OpenAI-compatible chat adapter."""

from app.agents.providers.openai_compatible import OpenAICompatibleProvider


class GroqProvider(OpenAICompatibleProvider):
    """Call Groq through the shared OpenAI client and Chat Completions API."""

    PROVIDER_NAME = "groq"
    API_KEY_ENV_VAR = "GROQ_API_KEY"
    BASE_URL = "https://api.groq.com/openai/v1"
