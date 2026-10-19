"""End-to-end smoke check for the Discovery / Clarification layer.

Run it with::

    python -m app.smoke_discovery

It prints the whole front of the pipeline -- the idea, the questions Forge asked,
the answers it got, the resulting project specification, a plan from the EXISTING
``Planner``, and the status of the session -- and exits 0.

Two deliberate properties:

* **no network**: the plan and the one dispatch shown in the ``PLAN`` section run
  on the ``mock`` provider, in a TEST runtime, so no credential is read and no
  request leaves the machine;
* **no second executor**: the plan comes from ``app.planning.Planner`` and the
  dispatch goes through the existing ``Orchestrator``/``Dispatcher`` path. The
  discovery layer owns neither.
"""

from __future__ import annotations

import sys

from app.discovery import DiscoveryService
from app.orchestrator.models import Task

#: The idea, exactly as a user would type it. It states the platform and the
#: project type, and leaves users, integrations, storage, auth, notifications and
#: constraints open -- which is what makes the questions meaningful.
FIXTURE_GOAL = "Хочу сайт для бронирования отелей."

#: Auto-answered fixture: the answers a hotel owner would give, one line each.
FIXTURE_ANSWERS = {
    "users": "гости отеля и администратор стойки",
    "integrations": "оплата картой, email",
    "data_storage": "PostgreSQL",
    "auth": "вход по email для администратора",
    "notifications": "подтверждение брони по email",
    "constraints": "бюджет ограничен, срок — 6 недель",
}


def _offline_runtime():
    """The existing runtime, narrowed to the offline provider (no network)."""

    from app.config.settings import RuntimeSettings
    from app.runtime.bootstrap import create_runtime

    return create_runtime(
        settings=RuntimeSettings(
            default_provider="mock",
            enabled_providers=("mock",),
            provider_fallback_chain=(),
        ),
        test_mode=True,
    )


def _print_questions(questions) -> None:
    if not questions:
        print("  (nothing essential is missing)")
        return
    for index, question in enumerate(questions, start=1):
        required = "required" if question.required else "optional"
        print(f"  {index}. [{required}] {question.text}")
        print(f"     field: {question.field} — {question.why}")


def main() -> int:
    discovery = DiscoveryService()

    print("INITIAL GOAL")
    print(f"  {FIXTURE_GOAL}")

    session = discovery.start(FIXTURE_GOAL)
    print(f"  session: {session.session_id}")
    print(f"  state: {session.state.value}")

    print("")
    print("QUESTIONS")
    _print_questions(session.questions)

    print("")
    print("ANSWERS")
    answers = [
        {"question_id": question.id, "text": FIXTURE_ANSWERS[question.field]}
        for question in session.questions
        if question.field in FIXTURE_ANSWERS
    ]
    for item in answers:
        print(f"  {item['question_id']}: {item['text']}")
    session = discovery.answer(session.session_id, answers)
    print(f"  state after answers: {session.state.value}")

    print("")
    print("PROJECT SPECIFICATION")
    specification = discovery.specification(session.session_id)
    for line in specification.as_task_text().splitlines()[1:]:
        print(f"  {line}")

    print("")
    print("PLAN")
    from app.planning import Planner

    plan = Planner().create_plan(specification.as_task_text())
    print(f"  planner: app.planning.Planner (deterministic, no provider call)")
    print(f"  plan id: {plan.plan_id}")
    for task in plan.tasks:
        dependencies = ", ".join(task.dependencies) or "none"
        print(f"  - {task.title} [{task.category.value}] deps: {dependencies}")

    try:
        runtime = _offline_runtime()
        first = plan.tasks[0]
        result = runtime.orchestrator.dispatch(
            Task(
                id="smoke-discovery-plan-step",
                description=first.description,
                category=first.category,
            )
        )
        provider = result.provider or "unknown"
        print(f"  first step dispatched through the existing pipeline: {provider}")
        preview = (result.output or "").replace("\n", " ")[:120]
        print(f"  mock answer: {preview}")
    except Exception:  # noqa: BLE001 - a smoke command reports, it does not crash
        print("  first step dispatch: unavailable in this composition")
        return 1

    print("")
    print("STATUS")
    session = discovery.get(session.session_id)
    print(f"  state: {session.state.value} (confirmation is a separate step)")
    print(f"  questions left: {len(session.questions)}")
    print(f"  specification fields known: {_known_count(specification)}/12")
    print("  ok")
    return 0


def _known_count(specification) -> int:
    data = specification.to_dict()
    return sum(
        1
        for key, value in data.items()
        if key != "source_answers" and bool(value)
    )


if __name__ == "__main__":
    sys.exit(main())
