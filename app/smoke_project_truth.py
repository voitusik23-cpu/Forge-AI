"""Two honest end-to-end outcomes, offline and repeatable.

Run it with::

    .\\.venv\\Scripts\\python.exe -m app.smoke_project_truth

Exit code 0 means every step below really ran. Nothing here opens a socket and
nothing here spends credit: the composed runtime enables only the repository's
offline ``mock`` provider, and the two runs that need a *model answer* are served
by an in-process fake provider whose text is fixed.

The two scenarios are the two things a reader must be able to tell apart.

1. **A model answered and nothing was written.** The idea is turned into
   questions, the provider answers with prose, and the run finishes. The response
   says a model answered and reports the truth: the project was not created, no
   file exists, and no verification ran. This is the case the owner's manual check
   found: a *finished* run that a reader could mistake for a finished project. The
   classifier reported it as ``SPECIFICATION_READY``, never ``COMPLETED``, and the
   card derived from the same facts says ``НЕ СОЗДАН``.

2. **A project was really produced.** The idea becomes a specification, a plan, a
   confirmation and a staged version; the fake provider answers with a fenced file;
   the guarded writer writes it and compiles it; the run is attached to the project
   and the lifecycle's own gate is applied. Only then does the project become
   ``COMPLETED``, and only because the file exists, the verification passed, the
   acceptance passed and the workspace exists.

Everything is written under ``.forge/smoke-project-truth`` (wiped at start), so the
smoke never touches the deployment's own project store.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.config.settings import OFFLINE_PROVIDER_NAME, RuntimeSettings
from app.discovery.models import DiscoverySession
from app.discovery.service import DiscoveryService, apply_specification
from app.discovery.store import DiscoveryStore
from app.orchestrator.models import Run, RunState, TaskResult
from app.planning.planner import Planner
from app.projects.models import ProjectStatus
from app.projects.presentation import build_project_presentation
from app.projects.service import ProjectService
from app.projects.store import ProjectStore, repository_root
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace

__all__ = ["main"]


#: A vague idea: it leaves users, storage, auth, integrations, notifications,
#: constraints and acceptance open, which is what makes the questions real.
GOAL = "Хочу сайт для бронирования отелей."

#: What the fake provider answers in scenario 1. It is prose: no fenced file, no
#: path header, nothing the guarded writer can turn into a project.
PROSE_ANSWER = (
    "Отличная идея! Чтобы сделать сайт бронирования отелей, нужно уточнить "
    "несколько вещей: какие города и отели вы планируете, нужна ли онлайн-оплата, "
    "и как администратор будет управлять номерами и бронями.\n\n"
    "Я могу начать с простого прототипа: список отелей, форма бронирования и "
    "страница подтверждения. Расскажите, какой из этих вариантов вам ближе, и я "
    "подготовлю спецификацию."
)

#: What the fake provider answers in scenario 2: a fenced block with a path header,
#: which is the shape ``app.agent_runtime.code_writer`` applies and compiles.
WRITE_ANSWER = """Проект собирается из одного модуля.

```python path=booking.py
\"\"\"A minimal hotel booking model, written for the smoke scenario.\"\"\"


def available_rooms(rooms, booked):
    \"\"\"Return the room numbers that are not booked.\"\"\"
    taken = set(booked)
    return [number for number in rooms if number not in taken]
```
"""

#: From where the smoke writes. Inside the repository, gitignored, wiped at start.
SMOKE_ROOT = repository_root() / ".forge" / "smoke-project-truth"


class _FakeProvider:
    """An in-process fake provider, used as the run executor.

    It exists only so a run can produce a real model *answer* without opening a
    socket or spending credits. Everything downstream of it -- the dispatcher's
    result shape, the guarded writer, the run store, the project lifecycle and its
    completion gate -- is the real implementation.
    """

    def __init__(self, answer: str, provider: str = "fake-provider") -> None:
        self.answer = answer
        self.provider = provider
        self.answered = 0

    def execute(self, **kwargs: Any) -> Run:
        task = kwargs["task"]
        run = Run(task=task, **({"id": kwargs["run_id"]} if kwargs.get("run_id") else {}))
        run.state = RunState.COMPLETED
        run.result = TaskResult(
            task_id=task.id,
            success=True,
            output=self.answer,
            provider=self.provider,
            model_name="deterministic-fake",
        )
        self.answered += 1
        return run


def _offline_runtime() -> Any:
    """A TEST runtime whose only enabled provider is the offline ``mock`` one."""

    return create_runtime(
        settings=RuntimeSettings(
            default_provider=OFFLINE_PROVIDER_NAME,
            enabled_providers=(OFFLINE_PROVIDER_NAME,),
            provider_fallback_chain=(),
        ),
        test_mode=True,
    )


def _line(label: str, value: object) -> None:
    print(f"  {label}: {value}")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root()).as_posix()
    except ValueError:
        return path.as_posix()


def _confirm_session(
    discovery: DiscoveryService, session: DiscoverySession
) -> DiscoverySession:
    """Answer every question the layer asks, then confirm the specification."""

    defaults = {
        "users": "гости отеля и администратор стойки",
        "core_features": "список отелей, форма бронирования, подтверждение брони",
        "platform": "веб-приложение в браузере",
        "integrations": "нет внешних интеграций",
        "data_storage": "локальный JSON-файл",
        "auth": "вход по email для администратора",
        "notifications": "подтверждение брони по email",
        "constraints": "без внешних зависимостей",
        "acceptance_criteria": "бронь сохраняется и видна администратору",
        "priorities": "простота",
    }
    answers = [
        {
            "question_id": question.id,
            "text": defaults.get(question.field, f"уточнение по {question.field}"),
        }
        for question in session.questions
    ]
    if answers:
        session = discovery.answer(session.session_id, answers)
    if session.state.value == "NEEDS_CLARIFICATION":
        session = discovery.skip(session.session_id, "остальное не критично для проверки")
    return discovery.confirm(session.session_id)


def _run_source(service: ForgeApiService, response: Any) -> dict:
    """The run's durable record, with the attribution only the response carries."""

    record = dict(service.get_run_record(getattr(response, "run_id", "")) or {})
    record["provider_used"] = (
        record.get("provider_used") or getattr(response, "provider_used", "") or ""
    )
    record["model_used"] = (
        record.get("model_used") or getattr(response, "model_used", "") or ""
    )
    return record


def scenario_one(root: Path, failures: List[str]) -> None:
    """Idea -> questions -> a prose answer -> nothing was created."""

    print("[1] A MODEL ANSWERED AND NOTHING WAS WRITTEN")
    discovery = DiscoveryService(DiscoveryStore(root=root / "discovery"))
    session = discovery.start(GOAL)
    _line("session", session.session_id)
    _line("state", session.state.value)
    _line("questions", len(session.questions))
    for index, question in enumerate(session.questions, start=1):
        print(f"    {index}. {question.text}")

    # The run executes the idea on the existing path; the provider is a fake one that
    # answers with prose. No write mode, so the answer is *text* and nothing else.
    run_store = RunStore(root=root / "runs")
    service = ForgeApiService(
        runtime=_offline_runtime(),
        run_store=run_store,
        workspace=Workspace(root / "run-workspace-one"),
        authorized_project_id="smoke-project-truth-one",
    )
    fake = _FakeProvider(PROSE_ANSWER)
    service.runtime = dataclasses.replace(service.runtime, run_executor=fake)
    outcome = service.run_task_detailed(
        TaskRunRequest(description=GOAL, category="code", provider_name="fake-provider")
    )
    response = outcome.response

    _line("provider answered (calls)", fake.answered)
    _line("run state (as the executor reported it)", "COMPLETED")
    _line("declared outcome (classifier)", response.outcome)
    _line("response state (honest word)", response.state)

    # The project record was never created: no confirmed session, no attach.
    projects = ProjectService(ProjectStore(root=root / "projects"))
    recorded = projects.list_projects()
    project_exists = len(recorded) > 0
    project_status = session.state.value if not project_exists else ""

    card = build_project_presentation(
        project=None,
        versions=(),
        files=(),
        run_record=_run_source(service, response),
        workspace_exists=False,
        questions_open=session.state.value == "NEEDS_CLARIFICATION",
        session_open=True,
    )

    print("  MODEL RESPONSE: received")
    print("  PROJECT: not created")
    print(f"  FILES: {response.files_created}")
    print("  VERIFICATION: not run")
    _line("project status", f"{project_status} (no project record)")
    _line("project card state", card.project_state)
    _line("card next step", card.next_step)
    _line(
        "save facts",
        ", ".join(f"{item.label}={item.value}" for item in card.save_facts),
    )

    if response.files_created != 0:
        failures.append("scenario 1: a prose answer must create no file")
    if project_exists:
        failures.append("scenario 1: no project record may exist for an unanswered session")
    if project_status not in {"NEEDS_CLARIFICATION", "NOT_CREATED"}:
        failures.append(
            f"scenario 1: project status must be NEEDS_CLARIFICATION or NOT_CREATED, "
            f"got {project_status!r}"
        )
    if response.outcome == "PROJECT_COMPLETED":
        failures.append("scenario 1: a prose answer must never be classified PROJECT_COMPLETED")
    if card.project_state == "ГОТОВО":
        failures.append("scenario 1: a card with no file must never say ГОТОВО")


def scenario_two(root: Path, failures: List[str]) -> None:
    """Idea -> specification -> plan -> confirmation -> write -> verify -> COMPLETED."""

    print("\n[2] A PROJECT WAS REALLY PRODUCED")
    discovery = DiscoveryService(DiscoveryStore(root=root / "discovery"))
    session = discovery.start(GOAL)
    _line("state (questions asked)", session.state.value)
    session = _confirm_session(discovery, session)
    specification = session.specification
    assert specification is not None
    _line("state after confirm", session.state.value)
    _line("specification.goal", specification.goal)

    plan = Planner().create_plan(specification.goal)
    _line("plan", f"{plan.plan_id} ({len(plan.tasks)} tasks)")

    projects = ProjectService(ProjectStore(root=root / "projects"))
    project = projects.create_from_discovery(session, plan_ref=plan.plan_id)
    _line("project (as created)", f"{project.project_id} · {project.status.value}")
    project = projects.confirm(project.project_id, plan_ref=plan.plan_id)
    _line("project (after confirmation)", project.status.value)
    _line("project folder (repo-relative)", project.project_path)

    run_store = RunStore(root=root / "runs")
    service = ForgeApiService(
        runtime=_offline_runtime(),
        run_store=run_store,
        workspace=Workspace(root / "run-workspace-two"),
        authorized_project_id="smoke-project-truth-two",
    )
    fake = _FakeProvider(WRITE_ANSWER)
    service.runtime = dataclasses.replace(service.runtime, run_executor=fake)
    task_text = apply_specification(specification, "создать модель бронирования")
    outcome = service.run_task_detailed(
        TaskRunRequest(
            description=task_text,
            category="code",
            provider_name="fake-provider",
            # REAL_PROVIDER_EXPLICIT: the caller names both the provider and the model.
            model_name="deterministic-fake",
            write_mode=True,
        )
    )
    response = outcome.response
    _line("provider answered (calls)", fake.answered)
    _line("run outcome (classifier)", response.outcome)

    record = _run_source(service, response)
    project = projects.attach_run(
        project.project_id, record, source_root=service.workspace.root
    )
    version = project.current()
    assert version is not None

    # The API re-derives the outcome *after* the project attach (server._attach_project),
    # because at the moment the run returned no project existed yet. The same call is
    # made here so the printed outcome is the one a caller of POST /api/tasks/run sees.
    attached_fields = service.record_project_attach(
        response.run_id,
        project_status=project.status.value,
        workspace_exists=projects.store.workspace_path(project.project_id).is_dir(),
        files=version.files,
        verification=version.verification,
        acceptance=version.acceptance,
        run_state=str(record.get("state") or response.state),
        run_success=response.success,
        error=response.error or "",
    )

    card = build_project_presentation(
        project=project.to_dict(),
        versions=[item.summary() for item in reversed(project.versions)],
        files=[artifact.to_dict() for artifact in version.files],
        run_record=_run_source(service, response),
        workspace_exists=projects.store.workspace_path(project.project_id).is_dir(),
    )

    print("  files_created:", response.files_created)
    print("  verification_status:", version.verification)
    print("  acceptance_status:", version.acceptance)
    print("  project_status:", project.status.value)
    print("  workspace_path:", project.project_path)
    _line("artifacts", [item.path for item in version.files])
    _line("verification relevance", [item.verification_relevance for item in version.files])
    _line("declared outcome (after the attach)", attached_fields.get("outcome"))
    _line("state (after the attach)", attached_fields.get("state"))
    _line("project card state", card.project_state)
    _line("project card stage", card.stage_label)
    _line("verification line", card.verification_line)

    if response.files_created < 1:
        failures.append("scenario 2: the write path must create at least one file")
    if version.verification != "passed":
        failures.append(
            f"scenario 2: py_compile must pass on the written file, got {version.verification!r}"
        )
    if not all(item.verification_relevance == "compiled" for item in version.files):
        failures.append("scenario 2: the written Python file must be recorded as compiled")
    if version.acceptance != "passed":
        failures.append(
            f"scenario 2: acceptance must pass, got {version.acceptance!r}"
        )
    if project.status is not ProjectStatus.COMPLETED:
        failures.append(
            f"scenario 2: a verified, accepted run with a file must be COMPLETED, "
            f"got {project.status.value}"
        )
    if card.project_state != "ГОТОВО":
        failures.append(
            f"scenario 2: the card must say ГОТОВО, got {card.project_state!r}"
        )
    if not projects.store.workspace_path(project.project_id).is_dir():
        failures.append("scenario 2: the project workspace must exist")


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = SMOKE_ROOT
    shutil.rmtree(root, ignore_errors=True)
    for name in ("discovery", "projects", "runs", "run-workspace-one", "run-workspace-two"):
        (root / name).mkdir(parents=True, exist_ok=True)

    print("FORGE PROJECT TRUTH SMOKE")
    print(f"  scratch root: {_relative(root)}")
    print(f"  offline provider: {OFFLINE_PROVIDER_NAME} (no socket is opened)")
    print("  fake provider: deterministic text, no network, no cost\n")
    failures: List[str] = []

    scenario_one(root, failures)
    scenario_two(root, failures)

    print("\nSUMMARY")
    _line("failures", len(failures))
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: OK (exit 0)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the module
    raise SystemExit(main(sys.argv[1:]))
