"""Real-request smoke check for Forge's automatic provider routing."""

import re
import sys
from typing import Iterable

from app.orchestrator.models import Task, TaskCategory
from app.runtime.bootstrap import create_runtime


def _use_mock(argv: "list[str]") -> bool:
    """Whether this invocation asks for the offline provider explicitly.

    ``python -m app.smoke_routing --mock`` keeps the same assertions -- one task,
    automatic routing, a reported provider and model -- while making no network
    call at all, which is what a CI run or an offline session needs.
    """

    return any(argument.strip() == "--mock" for argument in argv)


def _runtime(mock: bool):
    """The ambient runtime, or a TEST runtime narrowed to the offline provider."""

    if not mock:
        return create_runtime()
    from app.config.settings import RuntimeSettings

    return create_runtime(
        settings=RuntimeSettings(
            default_provider="mock",
            enabled_providers=("mock",),
            provider_fallback_chain=(),
        ),
        test_mode=True,
    )


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
    mock = _use_mock(sys.argv[1:])
    try:
        runtime = _runtime(mock)
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

    if mock:
        print("mode: offline (mock provider, no network)")
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
