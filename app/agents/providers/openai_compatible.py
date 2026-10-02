"""Shared OpenAI Chat Completions adapter for compatible provider APIs."""

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


class CompatibleAuthenticationError(ProviderError):
    """A compatible API rejected authentication."""


class CompatibleRateLimitError(ProviderError):
    """A compatible API rate limit was reached."""


class CompatibleTimeoutError(ProviderError):
    """A compatible API request timed out."""


class CompatibleRequestError(ProviderError):
    """A compatible API request failed for an expected reason."""


class CompatibleResponseError(ProviderError):
    """A compatible API returned a malformed or empty response."""


class OpenAICompatibleProvider(Provider):
    """Use the official OpenAI client against a compatible Chat API endpoint."""

    PROVIDER_NAME = ""
    API_KEY_ENV_VAR = ""
    BASE_URL = ""

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        *,
        client: Any = None,
        sdk_module: Any = None,
        secret_store: Optional[SecretStore] = None,
        timeout: Optional[float] = None,
    ) -> None:
        resolved_config = config or ProviderConfig(
            provider_name=self.PROVIDER_NAME,
            api_key_env_var=self.API_KEY_ENV_VAR,
        )
        if resolved_config.provider_name != self.PROVIDER_NAME:
            raise ValueError(f"Expected config for '{self.PROVIDER_NAME}'")
        super().__init__(resolved_config, secret_store=secret_store)
        self._client = client
        self._sdk_module = sdk_module
        self._timeout = timeout

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Call this provider only when the caller explicitly requests output."""
        api_key_env_var = self.config.api_key_env_var or self.API_KEY_ENV_VAR
        api_key = self.secret_store.get_secret(api_key_env_var)
        if not api_key or not api_key.strip():
            raise ProviderNotConfiguredError(
                f"Set {api_key_env_var} in the local environment to use {self.PROVIDER_NAME}"
            )
        model_name = request.model_name or self.model_name
        if not model_name or model_name == "unconfigured":
            raise ProviderNotConfiguredError(
                f"{self.PROVIDER_NAME} model is not configured"
            )

        sdk = self._load_sdk()
        client = self._client or self._create_client(sdk, api_key)
        prompt = request.prompt
        if request.context:
            prompt += "\n\nContext:\n" + json.dumps(
                request.context, ensure_ascii=False, sort_keys=True
            )

        try:
            kwargs = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
            }
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            response = client.chat.completions.create(**kwargs)
        except Exception as exc:
            self._translate_sdk_error(exc, sdk)
            raise

        output = self._extract_output(response)
        if not output:
            raise CompatibleResponseError(
                f"{self.PROVIDER_NAME} returned an empty or malformed text response"
            )
        response_model = getattr(response, "model", None)
        if not isinstance(response_model, str) or not response_model.strip():
            response_model = model_name

        raw_usage = getattr(response, "usage", None)
        input_tokens = getattr(raw_usage, "prompt_tokens", 0) if raw_usage else 0
        output_tokens = getattr(raw_usage, "completion_tokens", 0) if raw_usage else 0
        if not self._valid_token_count(input_tokens) or not self._valid_token_count(
            output_tokens
        ):
            raise CompatibleResponseError(
                f"{self.PROVIDER_NAME} returned malformed token usage"
            )

        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=response_model,
            output=output,
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=None,
            ),
        )

    @staticmethod
    def _extract_output(response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, (list, tuple)) or not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        return content.strip() if isinstance(content, str) else ""

    @staticmethod
    def _valid_token_count(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _load_sdk(self) -> Any:
        if self._sdk_module is not None:
            return self._sdk_module
        try:
            import openai
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "OpenAI-compatible SDK is unavailable; install project requirements"
            ) from exc
        return openai

    def _create_client(self, sdk: Any, api_key: str) -> Any:
        options = {"api_key": api_key, "base_url": self.BASE_URL}
        if self._timeout is not None:
            options["timeout"] = self._timeout
        return sdk.OpenAI(**options)

    def _translate_sdk_error(self, exc: Exception, sdk: Any) -> None:
        """Map known SDK failures to safe errors without exposing SDK messages."""
        cases = (
            ("AuthenticationError", CompatibleAuthenticationError, "authentication failed"),
            ("RateLimitError", CompatibleRateLimitError, "rate limit exceeded"),
            ("APITimeoutError", CompatibleTimeoutError, "request timed out"),
            ("APIConnectionError", CompatibleRequestError, "connection failed"),
            ("APIError", CompatibleRequestError, "API request failed"),
        )
        for sdk_name, provider_error, description in cases:
            error_type = getattr(sdk, sdk_name, None)
            if isinstance(error_type, type) and isinstance(exc, error_type):
                raise provider_error(f"{self.provider_name.title()} {description}") from None
