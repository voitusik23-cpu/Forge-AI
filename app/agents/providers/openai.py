"""OpenAI Responses API adapter, loaded lazily for explicit inference only."""

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


class OpenAIAuthenticationError(ProviderError):
    """The OpenAI API rejected authentication."""


class OpenAIRateLimitError(ProviderError):
    """The OpenAI API rate limit was reached."""


class OpenAITimeoutError(ProviderError):
    """The OpenAI API request timed out."""


class OpenAIRequestError(ProviderError):
    """The OpenAI API request failed for another expected API reason."""


class OpenAIResponseError(ProviderError):
    """The OpenAI API returned an unusable response."""


class OpenAIProvider(Provider):
    """Call OpenAI's Responses API without retaining its credential in config."""

    PROVIDER_NAME = "openai"
    API_KEY_ENV_VAR = "OPENAI_API_KEY"

    def __init__(
        self,
        config: Optional[ProviderConfig] = None,
        *,
        client: Any = None,
        sdk_module: Any = None,
        timeout: Optional[float] = None,
        secret_store: Optional[SecretStore] = None,
    ) -> None:
        resolved_config = config or ProviderConfig(
            provider_name=self.PROVIDER_NAME,
            api_key_env_var=self.API_KEY_ENV_VAR,
        )
        if resolved_config.provider_name != self.PROVIDER_NAME:
            raise ValueError("Expected config for 'openai'")
        super().__init__(resolved_config, secret_store=secret_store)
        self._client = client
        self._sdk_module = sdk_module
        self._timeout = timeout

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        """Generate text only when explicitly called with a configured model/key."""
        api_key_env_var = self.config.api_key_env_var or self.API_KEY_ENV_VAR
        api_key = self.secret_store.get_secret(api_key_env_var)
        if not api_key or not api_key.strip():
            raise ProviderNotConfiguredError(
                f"Set {api_key_env_var} in the local environment to use OpenAI"
            )
        model_name = request.model_name or self.model_name
        if not model_name or model_name == "unconfigured":
            raise ProviderNotConfiguredError("OpenAI model is not configured")

        sdk = self._load_sdk()
        client = self._client or self._create_client(sdk, api_key)
        api_input = request.prompt
        if request.context:
            api_input = (
                f"{request.prompt}\n\nContext:\n"
                + json.dumps(request.context, ensure_ascii=False, sort_keys=True)
            )

        try:
            kwargs = {"model": model_name, "input": api_input}
            if self._timeout is not None:
                kwargs["timeout"] = self._timeout
            raw_response = client.responses.create(**kwargs)
        except Exception as exc:
            self._translate_sdk_error(exc, sdk)
            raise

        output = getattr(raw_response, "output_text", None)
        if not isinstance(output, str) or not output.strip():
            raise OpenAIResponseError("OpenAI returned an empty or malformed text response")

        response_model = getattr(raw_response, "model", None)
        if not isinstance(response_model, str) or not response_model.strip():
            response_model = model_name
        raw_usage = getattr(raw_response, "usage", None)
        try:
            input_tokens = getattr(raw_usage, "input_tokens", 0) if raw_usage else 0
            output_tokens = getattr(raw_usage, "output_tokens", 0) if raw_usage else 0
            if (
                isinstance(input_tokens, bool)
                or not isinstance(input_tokens, int)
                or input_tokens < 0
                or isinstance(output_tokens, bool)
                or not isinstance(output_tokens, int)
                or output_tokens < 0
            ):
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise OpenAIResponseError("OpenAI returned malformed token usage") from exc

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

    def _load_sdk(self) -> Any:
        if self._sdk_module is not None:
            return self._sdk_module
        try:
            import openai
        except ImportError as exc:
            raise ProviderNotConfiguredError(
                "OpenAI SDK is unavailable; install project requirements"
            ) from exc
        return openai

    def _create_client(self, sdk: Any, api_key: str) -> Any:
        options = {"api_key": api_key}
        if self._timeout is not None:
            options["timeout"] = self._timeout
        return sdk.OpenAI(**options)

    @staticmethod
    def _translate_sdk_error(exc: Exception, sdk: Any) -> None:
        """Translate recognized SDK exceptions without exposing SDK messages."""
        cases = (
            ("AuthenticationError", OpenAIAuthenticationError, "OpenAI authentication failed"),
            ("RateLimitError", OpenAIRateLimitError, "OpenAI rate limit exceeded"),
            ("APITimeoutError", OpenAITimeoutError, "OpenAI request timed out"),
            ("APIConnectionError", OpenAIRequestError, "OpenAI connection failed"),
            ("APIError", OpenAIRequestError, "OpenAI API request failed"),
        )
        for sdk_name, provider_error, message in cases:
            error_type = getattr(sdk, sdk_name, None)
            if isinstance(error_type, type) and isinstance(exc, error_type):
                raise provider_error(message) from None
