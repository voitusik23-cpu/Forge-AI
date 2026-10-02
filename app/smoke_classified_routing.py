"""Real-request smoke check for classification-aware automatic routing."""

import sys

from app.orchestrator.classification import classify_task
from app.orchestrator.models import Task
from app.runtime.bootstrap import create_runtime
from app.smoke_routing import _known_secrets, _safe_text


_SMOKE_TASKS = (
    (
        "coding",
        "Implement a small Python function that reverses a string.",
    ),
    (
        "analysis",
        "Analyze why deterministic routing improves task dispatch.",
    ),
    (
        "review",
        "Review this code and find bugs: def add(a, b): return a - b",
    ),
)


def _console_safe(value: str) -> str:
    """Replace characters that the current terminal encoding cannot display."""
    encoding = getattr(sys.stdout, "encoding", None)
    if not encoding:
        return value
    return value.encode(encoding, errors="replace").decode(encoding)


def main() -> int:
    """Dispatch one real request per intent without selecting providers manually."""
    try:
        runtime = create_runtime()
        secrets = _known_secrets(runtime)
    except Exception:
        print("Classified routing smoke failed (details suppressed).")
        return 1

    all_succeeded = True
    for task_id, description in _SMOKE_TASKS:
        task = Task(id=f"classified-routing-{task_id}", description=description)
        category = classify_task(task)
        print(f"task: {task_id}")
        print(f"detected category: {category.value}")
        try:
            result = runtime.orchestrator.dispatch(task)
            provider_name = result.provider or result.agent or "unknown"
            try:
                model_name = runtime.provider_registry.get(provider_name).model_name
            except LookupError:
                model_name = "unknown"
            status = "success" if result.success else "failure"
            print(f"selected provider: {_safe_text(provider_name, secrets)}")
            print(f"selected model: {_safe_text(model_name, secrets)}")
            print(f"status: {status}")
            if result.success:
                answer = _safe_text(result.output, secrets)
                answer = answer.replace("\r", " ").replace("\n", " ")
                answer = _console_safe(answer)
                print(f"response: {answer[:240]}")
            else:
                print("response: unavailable")
                all_succeeded = False
        except Exception:
            print("selected provider: unknown")
            print("selected model: unknown")
            print("status: failure")
            print("response: unavailable")
            all_succeeded = False
    return 0 if all_succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
