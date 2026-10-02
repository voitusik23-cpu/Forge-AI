"""Validated, provider-neutral runtime settings."""

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional, Tuple


class ConfigurationError(ValueError):
    """Raised when runtime settings are invalid."""


class ApplicationEnvironment(str, Enum):
    """Supported application environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


@dataclass(frozen=True)
class RuntimeSettings:
    """Application-wide settings that contain no credentials."""

    environment: ApplicationEnvironment = ApplicationEnvironment.DEVELOPMENT
    debug: bool = False
    default_provider: str = "mock"
    default_model: str = "mock"
    request_timeout: int = 30
    retry_count: int = 2
    log_level: str = "INFO"
    provider_fallback_chain: Tuple[str, ...] = ()
    allow_paid_providers: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.environment, str):
            try:
                environment = ApplicationEnvironment(self.environment.strip().lower())
            except ValueError as exc:
                raise ConfigurationError(
                    "FORGE_ENV must be development, test, or production"
                ) from exc
            object.__setattr__(self, "environment", environment)
        elif not isinstance(self.environment, ApplicationEnvironment):
            raise ConfigurationError(
                "FORGE_ENV must be development, test, or production"
            )
        if not isinstance(self.debug, bool):
            raise ConfigurationError("FORGE_DEBUG must be a boolean")
        if not isinstance(self.allow_paid_providers, bool):
            raise ConfigurationError("FORGE_ALLOW_PAID_PROVIDERS must be a boolean")
        if not isinstance(self.default_provider, str) or not self.default_provider.strip():
            raise ConfigurationError("FORGE_DEFAULT_PROVIDER must not be empty")
        if not isinstance(self.default_model, str) or not self.default_model.strip():
            raise ConfigurationError("FORGE_DEFAULT_MODEL must not be empty")
        if isinstance(self.request_timeout, bool) or not isinstance(
            self.request_timeout, int
        ):
            raise ConfigurationError("FORGE_REQUEST_TIMEOUT must be an integer")
        if self.request_timeout <= 0:
            raise ConfigurationError("FORGE_REQUEST_TIMEOUT must be greater than 0")
        if isinstance(self.retry_count, bool) or not isinstance(self.retry_count, int):
            raise ConfigurationError("FORGE_RETRY_COUNT must be an integer")
        if self.retry_count < 0:
            raise ConfigurationError("FORGE_RETRY_COUNT must not be negative")
        if not isinstance(self.log_level, str):
            raise ConfigurationError("FORGE_LOG_LEVEL must be a valid log level")
        log_level = self.log_level.strip().upper()
        if log_level not in _LOG_LEVELS:
            raise ConfigurationError(
                "FORGE_LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, CRITICAL, or NOTSET"
            )
        object.__setattr__(self, "log_level", log_level)
        object.__setattr__(self, "default_provider", self.default_provider.strip())
        object.__setattr__(self, "default_model", self.default_model.strip())
        chain = self.provider_fallback_chain
        if isinstance(chain, str):
            chain = tuple(name.strip() for name in chain.split(",") if name.strip())
        if not isinstance(chain, (tuple, list)) or any(
            not isinstance(name, str) or not name.strip() for name in chain
        ):
            raise ConfigurationError(
                "FORGE_PROVIDER_FALLBACK_CHAIN must be a comma-separated provider list"
            )
        normalized_chain = tuple(name.strip() for name in chain)
        if len(set(normalized_chain)) != len(normalized_chain):
            raise ConfigurationError(
                "FORGE_PROVIDER_FALLBACK_CHAIN must not contain duplicates"
            )
        object.__setattr__(self, "provider_fallback_chain", normalized_chain)


def _parse_bool(name: str, value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigurationError(f"{name} must be a boolean")
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean (true/false, 1/0, yes/no, on/off)")


def _parse_int(name: str, value: Optional[str], default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc


def load_settings(environ: Optional[Mapping[str, str]] = None) -> RuntimeSettings:
    """Load only Forge runtime variables; provider credentials are never read."""
    source = os.environ if environ is None else environ
    defaults = RuntimeSettings()
    return RuntimeSettings(
        environment=source.get("FORGE_ENV", defaults.environment.value),
        debug=_parse_bool("FORGE_DEBUG", source.get("FORGE_DEBUG"), defaults.debug),
        default_provider=source.get(
            "FORGE_DEFAULT_PROVIDER", defaults.default_provider
        ),
        default_model=source.get("FORGE_DEFAULT_MODEL", defaults.default_model),
        request_timeout=_parse_int(
            "FORGE_REQUEST_TIMEOUT",
            source.get("FORGE_REQUEST_TIMEOUT"),
            defaults.request_timeout,
        ),
        retry_count=_parse_int(
            "FORGE_RETRY_COUNT", source.get("FORGE_RETRY_COUNT"), defaults.retry_count
        ),
        log_level=source.get("FORGE_LOG_LEVEL", defaults.log_level),
        provider_fallback_chain=source.get(
            "FORGE_PROVIDER_FALLBACK_CHAIN", defaults.provider_fallback_chain
        ),
        allow_paid_providers=_parse_bool(
            "FORGE_ALLOW_PAID_PROVIDERS",
            source.get("FORGE_ALLOW_PAID_PROVIDERS"),
            defaults.allow_paid_providers,
        ),
    )
