"""End-to-end smoke of the persistent project lifecycle, offline and repeatable.

Run it with::

    .\\.venv\\Scripts\\python.exe -m app.smoke_project_lifecycle

Exit code 0 means every step below really ran. Nothing here calls a paid provider:
the only provider the composed runtime enables is the repository's offline ``mock``
provider, and the one run that has to write a file is served by an in-process
stand-in executor whose answer is a fixed fenced block. No socket is opened.

The sequence, in the order the product uses it:

1. **Discovery** -- a session is opened, answered, and confirmed (``app.discovery``);
2. **Plan** -- the existing ``Planner`` derives the plan from the confirmed goal;
3. **Project** -- the confirmed session materialises its durable project record,
   which the confirmation advances from ``WAITING_FOR_CONFIRMATION`` to ``PLANNED``;
4. **Run** -- the existing task path executes the confirmed specification on the
   offline provider, and the real journal of that run is printed;
5. **Verification / acceptance / artifacts / status** -- the run is attached to the
   project and the lifecycle's own judgement is printed;
6. **New version** -- a change request stages version 2 and a second run fills it;
7. **Archive / restore** -- the record is frozen and then returned to its status.

Everything is written under ``.forge/smoke-project-lifecycle`` (wiped at start), so
the smoke never touches the deployment's own project store.
"""

from __future__ import annotations

import dataclasses
import hashlib
import shutil
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence

from app.agent_runtime.code_writer import render_write_report
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.config.settings import OFFLINE_PROVIDER_NAME, RuntimeSettings
from app.discovery.models import DiscoverySession
from app.discovery.service import DiscoveryService, apply_specification
from app.discovery.store import DiscoveryStore
from app.orchestrator.models import Run, RunState, TaskResult
from app.planning.planner import Planner
from app.projects.lifecycle import LifecycleEvent, advance
from app.projects.models import ArtifactRef, Project, ProjectStatus, ProjectVersion
from app.projects.service import ProjectService
from app.projects.store import ProjectStore, repository_root
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace

__all__ = ["main"]


GOAL = (
    "Хочу небольшой веб-инструмент для учёта домашних расходов: "
    "добавление трат, категории и месячный отчёт."
)

#: From where the smoke writes. Inside the repository, gitignored, wiped at start.
SMOKE_ROOT = repository_root() / ".forge" / "smoke-project-lifecycle"

#: The answer the stand-in executor returns for the second run. It is a fenced
#: block with a path header, which is the shape the guarded writer applies.
WRITE_ANSWER = """The change adds one small module.

```python path=report_export.py
\"\"\"Export a month of expenses as CSV text.\"\"\"


def export_month(rows):
    \"\"\"Return CSV text for the given (category, amount) rows.\"\"\"
    lines = [\"category,amount\"]
    for category, amount in rows:
        lines.append(f\"{category},{amount}\")
    return \"\\n\".join(lines) + \"\\n\"
```
"""


class _StandInRunExecutor:
    """An in-process stand-in for the provider-path executor.

    It exists only so one run can produce a real model *answer* to write without
    opening a socket or spending credits: the writer, the run store, the service's
    write mode and every validation below are the real implementations. It is the
    same seam ``tests/test_offline_task_execution.py`` uses.
    """

    def __init__(self, answer: str, provider: str = "stand-in") -> None:
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
            model_name="deterministic-stand-in",
        )
        self.answered += 1
        return run


def _offline_runtime() -> Any:
    """A TEST runtime whose only enabled provider is the offline ``mock`` one.

    This is the same narrowing ``tests/offline_runtime.py`` performs, composed here
    because a module under ``app/`` must not import the test tree.
    """

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


def _confirm_session(discovery: DiscoveryService, session: DiscoverySession) -> DiscoverySession:
    """Answer every question the layer asks, then confirm.

    An answer is canned text, so the specification is a real derived value and the
    run's task text is produced by the same ``apply_specification`` the API uses. If
    an essential area is still open after answering, the session is skipped
    explicitly -- which is the product's own path to ``READY_FOR_PLANNING``.
    """

    answers = []
    defaults = {
        "users": "один пользователь, ведущий домашний бюджет",
        "core_features": "добавление трат, категории, месячный отчёт",
        "platform": "веб-приложение в браузере",
        "integrations": "нет внешних интеграций",
        "data_storage": "локальный JSON-файл",
        "auth": "не требуется",
        "notifications": "не требуются",
        "constraints": "без внешних зависимостей",
        "acceptance_criteria": "трата сохраняется, отчёт строится за месяц",
        "priorities": "простота и офлайн-работа",
    }
    for question in session.questions:
        answers.append(
            {
                "question_id": question.id,
                "text": defaults.get(question.field, f"уточнение по {question.field}"),
            }
        )
    if answers:
        session = discovery.answer(session.session_id, answers)
    if session.state.value == "NEEDS_CLARIFICATION":
        session = discovery.skip(session.session_id, "остальное не критично для проверки")
    return discovery.confirm(session.session_id)


def _print_journal(store: RunStore, run_id: str, *, limit: int = 40) -> int:
    """Print the run's real journal, oldest first. Returns how many events exist."""

    events = store.read_events(run_id)
    for event in events[:limit]:
        name = getattr(event.event_type, "name", str(event.event_type))
        print(f"    #{event.sequence_number:>3}  {name}")
    if len(events) > limit:
        print(f"    ... {len(events) - limit} more event(s)")
    return len(events)


def _print_artifacts(project: Project, artifacts: Sequence[ArtifactRef]) -> None:
    if not artifacts:
        print("  Artifacts: none (the run wrote no file)")
        return
    for artifact in artifacts:
        location = f"{project.project_path}/{artifact.path}"
        print(
            f"  Artifact: {location} "
            f"({artifact.kind}, {artifact.change}, {artifact.size} bytes, "
            f"sha256={artifact.sha256[:16]}..., {artifact.verification_relevance})"
        )


def _run_source(service: ForgeApiService, response: Any) -> dict:
    """The run's durable record, with the attribution only the response carries.

    ``provider_used`` and ``model_used`` are fields of the task response; the
    persisted record keeps the classification and the write outcome. Merging the
    two here means the version records the same provider/model the caller was told,
    without inventing either.
    """

    record = dict(service.get_run_record(getattr(response, "run_id", "")) or {})
    record["provider_used"] = (
        record.get("provider_used") or getattr(response, "provider_used", "") or ""
    )
    record["model_used"] = (
        record.get("model_used") or getattr(response, "model_used", "") or ""
    )
    return record


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = SMOKE_ROOT
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    for name in ("discovery", "projects", "runs", "run-workspace"):
        (root / name).mkdir(parents=True, exist_ok=True)

    print("FORGE PROJECT LIFECYCLE SMOKE")
    print(f"  scratch root: {_relative(root)}")
    print(f"  offline provider: {OFFLINE_PROVIDER_NAME} (no socket is opened)")
    failures: List[str] = []

    # ---------------------------------------------------------------- 1. discovery
    discovery = DiscoveryService(DiscoveryStore(root=root / "discovery"))
    session = discovery.start(GOAL)
    print("\n[1] DISCOVERY")
    _line("session", session.session_id)
    _line("state", session.state.value)
    _line("questions", len(session.questions))
    session = _confirm_session(discovery, session)
    specification = session.specification
    assert specification is not None
    _line("state after confirm", session.state.value)
    _line("specification.goal", specification.goal)

    # ------------------------------------------------------------------- 2. plan
    plan = Planner().create_plan(specification.goal)
    print("\n[2] PLANNER (existing)")
    _line("plan_id", plan.plan_id)
    _line("title", plan.title)
    _line("tasks", len(plan.tasks))

    # ---------------------------------------------------------------- 3. project
    projects = ProjectService(ProjectStore(root=root / "projects"))
    project = projects.create_from_discovery(session, plan_ref=plan.plan_id)
    print("\n[3] PROJECT RECORD")
    _line("project_id", project.project_id)
    _line("status (as created)", project.status.value)
    _line("project_path (repo-relative)", project.project_path)
    _line("workspace exists", (root / "projects" / project.project_id / "workspace").is_dir())
    project = projects.confirm(project.project_id, plan_ref=plan.plan_id)
    _line("status after confirm", project.status.value)
    _line("plan_ref", project.plan_ref)

    # ------------------------------------------------------- 4. the existing run
    run_store = RunStore(root=root / "runs")
    service = ForgeApiService(
        runtime=_offline_runtime(),
        run_store=run_store,
        workspace=Workspace(root / "run-workspace"),
        authorized_project_id="smoke-project-lifecycle",
    )
    task_text = apply_specification(specification, "перечислить содержимое корня проекта")
    print("\n[4] RUN (existing path, offline provider)")
    outcome = service.run_task_detailed(
        TaskRunRequest(description=task_text, category="code", provider_name=OFFLINE_PROVIDER_NAME)
    )
    response = outcome.response
    _line("run_id", response.run_id)
    _line("state", response.state)
    _line("success", response.success)
    _line("provider_used", response.provider_used)
    _line("duration_seconds", response.duration_seconds)
    _line("tokens", response.tokens)
    print("  Journal (real events from the run store):")
    event_count = _print_journal(run_store, response.run_id)
    _line("events recorded", event_count)

    # ------------------------------------------ 5. attach, verify, accept, status
    print("\n[5] ATTACH -> VERIFICATION -> ACCEPTANCE -> ARTIFACTS -> STATUS")
    record = _run_source(service, response)
    project = projects.attach_run(
        project.project_id, record, source_root=service.workspace.root
    )
    version = project.current()
    assert version is not None
    _line("version", version.version)
    _line("run_id recorded on the version", version.run_id)
    _line("verification (from code_write)", version.verification)
    _line("acceptance (from the run classification)", version.acceptance)
    _line("classification", (record.get("classification") or {}).get("outcome"))
    _print_artifacts(project, version.files)
    _line("status", project.status.value)
    if project.status is ProjectStatus.COMPLETED:
        failures.append("a run that wrote nothing must not be COMPLETED")

    # ---------------------------------------------------------- 6. new version
    print("\n[6] NEW VERSION")
    staged = projects.new_version(
        project.project_id, "добавить экспорт месячного отчёта в CSV"
    )
    project = staged.project
    _line("staged version", staged.version)
    _line("status after staging", project.status.value)
    _line("change_request", staged.change_request.text)
    print("  task_text submitted to the existing run path:")
    for line in staged.task_text.splitlines():
        print(f"    | {line}")

    # The second run has to *write* something, which the offline provider never
    # does (its answer is not a change). A stand-in executor supplies a fixed
    # answer; everything downstream of it is the real write path.
    stand_in = _StandInRunExecutor(WRITE_ANSWER)
    service.runtime = dataclasses.replace(service.runtime, run_executor=stand_in)
    outcome2 = service.run_task_detailed(
        TaskRunRequest(
            description=staged.task_text,
            category="code",
            provider_name="stand-in-writer",
            write_mode=True,
        )
    )
    response2 = outcome2.response
    _line("run_id (version 2)", response2.run_id)
    _line("success", response2.success)
    _line("provider_used", response2.provider_used)
    print("  Write report (produced by app.agent_runtime.code_writer):")
    for line in response2.output.splitlines():
        if line.startswith("CODE WRITER") or line.startswith("  create") or line.startswith("  update") or line.startswith("Verification") or line.startswith("Reason"):
            print(f"    | {line}")
    record2 = _run_source(service, response2)
    project = projects.attach_run(
        project.project_id, record2, source_root=service.workspace.root
    )
    version2 = project.current()
    assert version2 is not None
    _line("version", version2.version)
    _line("verification", version2.verification)
    _line("acceptance", version2.acceptance)
    _line("provider / model", f"{version2.provider} / {version2.model}")
    _print_artifacts(project, version2.files)
    _line("status", project.status.value)
    if project.status is not ProjectStatus.COMPLETED:
        failures.append(
            "a terminal-success, verified, accepted run with an artifact must be COMPLETED"
        )
    _line("current_version", project.current_version)
    _line("last_run_at", project.last_run_at)

    # The earlier version is untouched, byte for byte.
    history = projects.history(project.project_id)
    _line("history (newest first)", [item.version for item in history])
    first = project.version(1)
    if first is None or first.run_id != response.run_id:
        failures.append("version 1 must still point at the first run")
    _line("version 1 files", len(first.files) if first else -1)
    _line("version 2 files", len(version2.files))

    # ------------------------------------------------------ 7. archive / restore
    print("\n[7] ARCHIVE / RESTORE")
    before_archive = project.status.value
    project = projects.archive(project.project_id)
    _line("status", project.status.value)
    _line("archived", project.archived)
    _line("previous_status", project.previous_status.value if project.previous_status else "")
    _line("versions still present", [item.version for item in project.versions])
    workspace_files = sorted(
        path.name
        for path in (root / "projects" / project.project_id / "workspace").iterdir()
        if path.is_file()
    )
    _line("workspace files (archive deleted nothing)", workspace_files)
    project = projects.restore(project.project_id)
    _line("status after restore", project.status.value)
    _line("archived after restore", project.archived)
    if project.status.value != before_archive:
        failures.append("restore must return the project to its previous status")

    # ------------------------------------------------------------------- summary
    print("\nSUMMARY")
    _line("project", project.project_id)
    _line("project_path", project.project_path)
    _line("specification.goal", project.specification_goal())
    _line("current_version", project.current_version)
    _line("files", [artifact.path for artifact in projects.files(project.project_id)])
    _line("status", project.status.value)
    _line("store files", sorted(path.name for path in (root / "projects").iterdir()))

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: OK (exit 0)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the module
    raise SystemExit(main(sys.argv[1:]))
