"""Real-request smoke check for one primary agent and one reviewer pass."""

from app.agents.reviewer import ProviderReviewer
from app.orchestrator.models import Task
from app.orchestrator.multi_agent import MultiAgentExecutor
from app.runtime.bootstrap import create_runtime
from app.smoke_routing import _known_secrets, _safe_text


def _model_name(runtime, provider_name, result_model):
    if result_model:
        return result_model
    if not provider_name:
        return "unknown"
    try:
        return runtime.provider_registry.get(provider_name).model_name
    except LookupError:
        return "unknown"


def main() -> int:
    """Execute one real task and one review via normal routing and providers."""
    try:
        runtime = create_runtime()
        secrets = _known_secrets(runtime)
        reviewer = ProviderReviewer(runtime.orchestrator, runtime.provider_registry)
        executor = MultiAgentExecutor(runtime.orchestrator, reviewer)
        result = executor.execute(
            Task(
                id="multi-agent-smoke",
                description=(
                    "In two short sentences, explain why deterministic task routing "
                    "is useful in an AI application."
                ),
            )
        )
    except Exception:
        print("Multi-agent smoke failed (details suppressed).")
        return 1

    primary = result.primary_result
    primary_provider = primary.provider or primary.agent
    print("primary:")
    print(f"  provider: {_safe_text(primary_provider or 'unknown', secrets)}")
    print(
        "  model: "
        f"{_safe_text(_model_name(runtime, primary_provider, primary.model_name), secrets)}"
    )
    print(f"  status: {'success' if primary.success else 'failure'}")

    revision = result.revision_result
    if revision is not None:
        revision_provider = revision.provider or revision.agent
        print("revision:")
        print(f"  provider: {_safe_text(revision_provider or 'unknown', secrets)}")
        print(
            "  model: "
            f"{_safe_text(_model_name(runtime, revision_provider, revision.model_name), secrets)}"
        )
        print(f"  status: {'success' if revision.success else 'failure'}")

    review = result.review_result
    print("review:")
    if review is None:
        print("  provider: unavailable")
        print("  model: unavailable")
        print("  status: skipped")
    else:
        review_provider = review.provider
        print(f"  provider: {_safe_text(review_provider or 'unknown', secrets)}")
        print(
            "  model: "
            f"{_safe_text(_model_name(runtime, review_provider, review.model_name), secrets)}"
        )
        print(f"  status: {review.status.value}")

    final_review = result.final_review_result
    if final_review is not None:
        final_review_provider = final_review.provider
        print("final review:")
        print(
            f"  provider: {_safe_text(final_review_provider or 'unknown', secrets)}"
        )
        print(
            "  model: "
            f"{_safe_text(_model_name(runtime, final_review_provider, final_review.model_name), secrets)}"
        )
        print(f"  status: {final_review.status.value}")

    print("overall:")
    print(f"  status: {result.status.value}")
    return 0 if result.status.value == "approved" else 1


if __name__ == "__main__":
    raise SystemExit(main())
