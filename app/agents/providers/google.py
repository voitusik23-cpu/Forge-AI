"""Gemini provider using Google's official Gen AI Python SDK."""

import json
import re
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


class GeminiAPIError(GeminiRequestError):
    """A sanitized Google API failure with structured status information."""

    def __init__(
        self,
        *,
        http_status: Optional[int],
        google_code: Optional[int],
        google_status: Optional[str],
        safe_message: str,
    ) -> None:
        self.http_status = http_status
        self.google_code = google_code
        self.google_status = google_status
        self.safe_message = safe_message
        details = []
        if http_status is not None:
            details.append(f"HTTP {http_status}")
        if google_status:
            details.append(google_status)
        elif google_code is not None:
            details.append(f"Google code {google_code}")
        prefix = ", ".join(details) or "Google API error"
        super().__init__(f"Gemini API error ({prefix}): {safe_message}")


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
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"automatic_function_calling": {"disable": True}},
            )
        except Exception as exc:
            if self._is_google_api_error(exc, sdk):
                raise self._api_error(exc, api_key) from None
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
    def _is_google_api_error(error: Exception, sdk: Any) -> bool:
        errors = getattr(sdk, "errors", None)
        api_error = getattr(errors, "APIError", None)
        if isinstance(api_error, type) and isinstance(error, api_error):
            return True
        # SDK releases expose APIError from google.genai.errors. Keep this
        # fallback lazy so offline provider tests do not require the SDK.
        try:
            from google.genai.errors import APIError
        except ImportError:
            return False
        return isinstance(error, APIError)

    @classmethod
    def _api_error(cls, error: Exception, api_key: str) -> GeminiAPIError:
        response = getattr(error, "response", None)
        response_status = getattr(response, "status_code", None)
        code = cls._status_code(getattr(error, "code", None))
        http_status = cls._status_code(response_status) or code
        status = getattr(error, "status", None)
        status = status if isinstance(status, str) and status else None
        message = getattr(error, "message", None)
        if not isinstance(message, str) or not message.strip():
            message = "Google API request failed"
        safe_message = cls._sanitize_message(message, api_key)
        return GeminiAPIError(
            http_status=http_status,
            google_code=code,
            google_status=status,
            safe_message=safe_message,
        )

    @staticmethod
    def _status_code(value: Any) -> Optional[int]:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.isdecimal():
            return int(value)
        return None

    @staticmethod
    def _sanitize_message(message: str, api_key: str) -> str:
        """Remove credentials and URLs before retaining an SDK error message."""
        safe = message.replace(api_key, "[REDACTED]") if api_key else message
        safe = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", safe)
        safe = re.sub(
            r"(?im)\b(authorization|x-goog-api-key)\s*[:=]\s*[^\r\n,;]+",
            r"\1: [REDACTED]",
            safe,
        )
        safe = re.sub(
            r"(?i)(?:AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,})",
            "[REDACTED]",
            safe,
        )
        safe = re.sub(r"https?://\S+", "[URL REDACTED]", safe)
        return safe.strip() or "Google API request failed"

    @staticmethod
    def _valid_count(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
