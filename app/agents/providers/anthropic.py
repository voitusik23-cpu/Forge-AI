"""Anthropic Messages API adapter with lazy SDK and secret loading."""

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


class AnthropicAuthenticationError(ProviderError):
    """Anthropic rejected the configured credentials."""


class AnthropicRateLimitError(ProviderError):
    """Anthropic rate limit was reached."""


class AnthropicTimeoutError(ProviderError):
    """The Anthropic request timed out."""


class AnthropicRequestError(ProviderError):
    """An expected Anthropic API or connection error occurred."""


class AnthropicResponseError(ProviderError):
    """Anthropic returned a malformed or empty response."""


class AnthropicProvider(Provider):
    """Call Anthropic's Messages API only when ``generate`` is explicitly used."""

    PROVIDER_NAME = "anthropic"
    API_KEY_ENV_VAR = "ANTHROPIC_API_KEY"
    DEFAULT_MAX_TOKENS = 1024

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        *,
        client: Any = None,
        sdk_module: Any = None,
        secret_store: Optional[SecretStore] = None,
        timeout: Optional[float] = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        resolved_config = config or ProviderConfig(
            provider_name=self.PROVIDER_NAME,
            api_key_env_var=self.API_KEY_ENV_VAR,
        )
        if resolved_config.provider_name != self.PROVIDER_NAME:
            raise ValueError("Expected config for 'anthropic'")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError("max_tokens must be a positive integer")
        super().__init__(resolved_config, secret_store=secret_store)
        self._client = client
        self._sdk_module = sdk_module
        self._timeout = timeout
        self._max_tokens = max_tokens

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate a provider-neutral response using the Anthropic SDK."""
        api_key_env_var = self.config.api_key_env_var or self.API_KEY_ENV_VAR
        api_key = self.secret_store.get_secret(api_key_env_var)
        if not api_key or not api_key.strip():
            raise ProviderNotConfiguredError(
                f"Set {api_key_env_var} in the local environment to use Anthropic"
            )
        if not self.model_name or self.model_name == "unconfigured":
            raise ProviderNotConfiguredError("Anthropic model is not configured")

        sdk = self._load_sdk()
        client = self._client or self._create_client(sdk, api_key)
        prompt = request.prompt
        if request.context:
            prompt += "\n\nContext:\n" + json.dumps(
                request.context, ensure_ascii=False, sort_keys=True
            )

        try:
            kwargs = {
                "model": self.model_name,
                "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            message = client.messages.create(**kwargs)
        except Exception as exc:
            self._translate_sdk_error(exc, sdk)
            raise

        output = self._extract_text(getattr(message, "content", None))
        if not output:
            raise AnthropicResponseError(
                "Anthropic returned an empty or malformed text response"
            )

        response_model = getattr(message, "model", None)
        if not isinstance(response_model, str) or not response_model.strip():
            response_model = self.model_name
        raw_usage = getattr(message, "usage", None)
        input_tokens = getattr(raw_usage, "input_tokens", None)
        output_tokens = getattr(raw_usage, "output_tokens", None)
        if not self._valid_token_count(input_tokens) or not self._valid_token_count(
            output_tokens
        ):
            raise AnthropicResponseError("Anthropic returned malformed token usage")

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
    def _extract_text(content: Any) -> str:
        if not isinstance(content, (list, tuple)):
            return ""
        text_parts = []
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            text = (
                block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            )
            if block_type == "text" and isinstance(text, str) and text.strip():
                text_parts.append(text)
        return "\n".join(text_parts).strip()

    @staticmethod
    def _valid_token_count(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _load_sdk(self) -> Any:
        if self._sdk_module is not None:
            return self._sdk_module
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "Anthropic SDK is unavailable; install project requirements"
            ) from exc
        return anthropic

    def _create_client(self, sdk: Any, api_key: str) -> Any:
        options = {"api_key": api_key}
        if self._timeout is not None:
            options["timeout"] = self._timeout
        return sdk.Anthropic(**options)

    @staticmethod
    def _translate_sdk_error(exc: Exception, sdk: Any) -> None:
        """Map known SDK failures to safe errors without retaining SDK messages."""
        cases = (
            ("AuthenticationError", AnthropicAuthenticationError, "Anthropic authentication failed"),
            ("RateLimitError", AnthropicRateLimitError, "Anthropic rate limit exceeded"),
            ("APITimeoutError", AnthropicTimeoutError, "Anthropic request timed out"),
            ("APIConnectionError", AnthropicRequestError, "Anthropic connection failed"),
            ("APIError", AnthropicRequestError, "Anthropic API request failed"),
        )
        for sdk_name, provider_error, safe_message in cases:
            error_type = getattr(sdk, sdk_name, None)
            if isinstance(error_type, type) and isinstance(exc, error_type):
                raise provider_error(safe_message) from None
