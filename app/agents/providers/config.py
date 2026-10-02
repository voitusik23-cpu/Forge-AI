"""Safe provider settings containing credential references, never secrets."""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProviderConfig:
    """Provider metadata and an optional environment-variable name reference."""

    provider_name: str
    model_name: str = "unconfigured"
    enabled: bool = False
    api_key_env_var: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.provider_name.strip():
            raise ValueError("provider_name must not be empty")
        if not self.model_name.strip():
            raise ValueError("model_name must not be empty")
        if self.api_key_env_var is not None and not re.fullmatch(
            r"[A-Z][A-Z0-9_]*", self.api_key_env_var
        ):
            raise ValueError("api_key_env_var must be an environment variable name")
