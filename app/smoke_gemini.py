"""Explicit real-request smoke check for Gemini through Forge AI."""

from app.orchestrator.models import Task, TaskCategory
from app.runtime.bootstrap import create_runtime


_EXPECTED_MARKER = "FORGE_GEMINI_SMOKE_OK"


def main() -> int:
    """Send one request through Forge's runtime and Gemini provider adapter."""
    try:
        runtime = create_runtime()
        result = runtime.orchestrator.dispatch(
            Task(
                id="gemini-smoke",
                description=f"Reply with exactly: {_EXPECTED_MARKER}",
                category=TaskCategory.LARGE_CONTEXT,
            ),
            provider_name="google",
        )
    except Exception:
        print("Gemini smoke test failed (details suppressed).")
        return 1

    if not result.success or _EXPECTED_MARKER not in result.output:
        print("Gemini smoke test failed (details suppressed).")
        return 1

    print("Gemini -> Forge AI smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
