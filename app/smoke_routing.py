"""Real-request smoke check for Forge's automatic provider routing."""

import re
from typing import Iterable

from app.orchestrator.models import Task, TaskCategory
from app.runtime.bootstrap import create_runtime


def _known_secrets(runtime) -> list[str]:
    """Read configured credentials only for output redaction; never display them."""
    secrets = []
    for capability in runtime.provider_capabilities.list_capabilities():
        if not capability.api_key_env:
            continue
        try:
            provider = runtime.provider_registry.get(capability.provider_name)
            secret = provider.secret_store.get_secret(capability.api_key_env)
        except (LookupError, OSError, RuntimeError):
            continue
        if secret:
            secrets.append(secret)
    return secrets


def _safe_text(value: str, secrets: Iterable[str]) -> str:
    """Redact known credentials and common credential formats from output."""
    safe = value
    for secret in secrets:
        safe = safe.replace(secret, "[REDACTED]")
    safe = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", safe)
    safe = re.sub(
        r"(?i)(?:AIza[0-9A-Za-z_-]{20,}|sk-[0-9A-Za-z_-]{16,})",
        "[REDACTED]",
        safe,
    )
    safe = re.sub(r"https?://\S+", "[URL REDACTED]", safe)
    return safe


def main() -> int:
    """Dispatch an ordinary task without a provider override and report routing."""
    try:
        runtime = create_runtime()
        task = Task(
            id="automatic-routing-smoke",
            description=(
                "In one short sentence, explain what an AI task dispatcher does."
            ),
            category=TaskCategory.OTHER,
        )
        result = runtime.orchestrator.dispatch(task)
        secrets = _known_secrets(runtime)
        provider_name = result.provider or result.agent or "unknown"
        try:
            model_name = runtime.provider_registry.get(provider_name).model_name
        except LookupError:
            model_name = "unknown"
        provider_name = _safe_text(provider_name, secrets)
        model_name = _safe_text(model_name, secrets)
    except Exception:
        print("Automatic routing smoke failed (details suppressed).")
        return 1

    print(f"provider: {provider_name}")
    print(f"model: {model_name}")
    print(f"status: {'success' if result.success else 'failure'}")
    if result.success:
        answer = _safe_text(result.output, secrets).replace("\r", " ").replace("\n", " ")
        print(f"response: {answer[:240]}")
        return 0
    print("response: unavailable")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
