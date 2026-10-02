"""Gemini provider using Google's official Gen AI Python SDK."""

import json
from typing import Any, Optional

from app.agents.providers.base import (
    Provider,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRequest,
    ProviderResponse,
)
from app.agents.providers.config import ProviderConfig
from app.config.secrets import SecretStore
from app.usage import Usage


class GeminiRequestError(ProviderError):
    """A Gemini request failed without exposing SDK error details."""


class GeminiClientError(GeminiRequestError):
    """Gemini SDK client initialization failed safely."""


class GeminiResponseError(ProviderError):
    """Gemini returned a malformed or empty text response."""


class GoogleProvider(Provider):
    """Call Gemini only when explicitly dispatched through the provider layer."""

    PROVIDER_NAME = "google"
    API_KEY_ENV_VAR = "GEMINI_API_KEY"
    DEFAULT_MODEL_ID = "gemini-3.8-flash"

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        *,
        client: Any = None,
        sdk_module: Any = None,
        secret_store: Optional[SecretStore] = None,
    ) -> None:
        resolved_config = config or ProviderConfig(
            provider_name=self.PROVIDER_NAME,
            model_name=self.DEFAULT_MODEL_ID,
            api_key_env_var=self.API_KEY_ENV_VAR,
        )
        if resolved_config.provider_name != self.PROVIDER_NAME:
            raise ValueError("Expected config for 'google'")
        super().__init__(resolved_config, secret_store=secret_store)
        self._client = client
        self._sdk_module = sdk_module

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate text through the official SDK with a lazily resolved key."""
        api_key_env = self.config.api_key_env_var or self.API_KEY_ENV_VAR
        api_key = self.secret_store.get_secret(api_key_env)
        if not api_key or not api_key.strip():
            raise ProviderNotConfiguredError(
                f"Set {api_key_env} in the local environment to use Gemini"
            )
        model_name = request.model_name or self.model_name
        if not model_name or model_name == "unconfigured":
            raise ProviderNotConfiguredError("Gemini model is not configured")

        sdk = self._load_sdk()
        try:
            client = self._client or sdk.Client(api_key=api_key)
        except Exception:
            raise GeminiClientError("Gemini client initialization failed") from None
        prompt = request.prompt
        if request.context:
            prompt += "\n\nContext:\n" + json.dumps(
                request.context, ensure_ascii=False, sort_keys=True
            )
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
        except Exception:
            raise GeminiRequestError("Gemini API request failed") from None

        output = getattr(response, "text", None)
        if not isinstance(output, str) or not output.strip():
            raise GeminiResponseError("Gemini returned an empty or malformed text response")
        actual_model = getattr(response, "model_version", None)
        if not isinstance(actual_model, str) or not actual_model.strip():
            actual_model = model_name
        metadata = getattr(response, "usage_metadata", None)
        input_tokens = getattr(metadata, "prompt_token_count", 0) if metadata else 0
        output_tokens = getattr(metadata, "candidates_token_count", 0) if metadata else 0
        if not self._valid_count(input_tokens) or not self._valid_count(output_tokens):
            raise GeminiResponseError("Gemini returned malformed token usage")
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=actual_model,
            output=output.strip(),
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=None,
            ),
        )

    def _load_sdk(self) -> Any:
        if self._sdk_module is not None:
            return self._sdk_module
        try:
            from google import genai
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "Google Gen AI SDK is unavailable; install project requirements"
            ) from exc
        return genai

    @staticmethod
    def _valid_count(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
