"""Explicit real-request smoke check for OpenRouter through Forge AI."""

from app.orchestrator.models import Task, TaskCategory
from app.runtime.bootstrap import create_runtime


_EXPECTED_MARKER = "FORGE_OPENROUTER_SMOKE_OK"


def main() -> int:
    """Send one request through Forge's normal runtime and OpenRouter adapter."""
    try:
        runtime = create_runtime()
        result = runtime.orchestrator.dispatch(
            Task(
                id="openrouter-smoke",
                description=f"Reply with exactly: {_EXPECTED_MARKER}",
                category=TaskCategory.OTHER,
            ),
            provider_name="openrouter",
        )
    except Exception:
        print("OpenRouter smoke test failed (details suppressed).")
        return 1

    if not result.success or _EXPECTED_MARKER not in result.output:
        print("OpenRouter smoke test failed (details suppressed).")
        return 1

    print("OpenRouter -> Cohere -> Forge AI smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
