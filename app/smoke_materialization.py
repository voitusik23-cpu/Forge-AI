"""End-to-end smoke of project materialisation, offline and repeatable.

Run it with::

    .\\.venv\\Scripts\\python.exe -m app.smoke_materialization

Exit code 0 means every section below really ran. Nothing here calls a paid
provider: the only enabled provider is the repository's offline ``mock`` one, and
the write mode it exercises is ``MOCK_DETERMINISTIC``, whose answer is produced by
Forge itself (``app.projects.mock_answer``). The only socket opened is the local
preview server on ``127.0.0.1`` -- that is the feature under test -- and it is
stopped before the smoke returns.

The sequence, in the order the product uses it:

1. **Specification** -- a discovery session is opened, answered and confirmed;
2. **Plan** -- the existing ``Planner`` derives the plan from the confirmed goal;
3. **Write approval** -- the project record is created, and the two write modes are
   demonstrated: a run without the flag writes nothing; a ``MOCK_DETERMINISTIC`` run
   writes into the project's own folder;
4. **Files created** -- the files that really appeared under
   ``<store>/<project_id>/workspace``;
5. **Artifact manifest** -- path, kind, size, SHA-256, create/update, verification
   relevance, and no contents;
6. **Verification** -- the writer's own ``py_compile`` verdict;
7. **Entry point** -- detected from the manifest only;
8. **Local preview status** -- started, fetched over loopback, and stopped;
9. **Final project status** -- the lifecycle's own word plus the derived stage.

Everything is written under ``.forge/smoke-materialization`` (wiped at start), so
the smoke never touches the deployment's own project store.
"""

from __future__ import annotations

import dataclasses
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional, Sequence

from app.api.models import TaskRunRequest
from app.api.service import (
    MATERIALIZATION_PROJECT_STATE_KEY,
    WRITE_MODE_MOCK_DETERMINISTIC,
    WRITE_MODE_REAL_PROVIDER_EXPLICIT,
    ForgeApiService,
)
from app.config.settings import OFFLINE_PROVIDER_NAME, RuntimeSettings
from app.discovery.models import DiscoverySession
from app.discovery.service import DiscoveryService
from app.discovery.store import DiscoveryStore
from app.planning.planner import Planner
from app.projects.entrypoint import detect_entry_point
from app.projects.preview import PreviewRegistry
from app.projects.service import ProjectService
from app.projects.stage import project_stage
from app.projects.store import ProjectStore, repository_root
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace

__all__ = ["main"]


GOAL = (
    "Хочу небольшой веб-инструмент для учёта домашних расходов: "
    "добавление трат, категории и месячный отчёт."
)

#: The task the offline run executes. It names read-only work the offline loop can
#: really perform, so the run reaches its own terminal success; the *answer* that is
#: materialised comes from the MOCK_DETERMINISTIC scaffold, not from this sentence.
TASK_TEXT = "перечислить содержимое корня проекта"

#: From where the smoke writes. Inside the repository, gitignored, wiped at start.
SMOKE_ROOT = repository_root() / ".forge" / "smoke-materialization"


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
    """Answer every question the layer asks, then confirm (the product's own path)."""

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
    record = dict(service.get_run_record(getattr(response, "run_id", "")) or {})
    record["provider_used"] = (
        record.get("provider_used") or getattr(response, "provider_used", "") or ""
    )
    record["model_used"] = (
        record.get("model_used") or getattr(response, "model_used", "") or ""
    )
    return record


def _fetch(url: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, b""
    except urllib.error.URLError:
        return 0, b""


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = SMOKE_ROOT
    shutil.rmtree(root, ignore_errors=True)
    for name in ("discovery", "projects", "runs", "run-workspace"):
        (root / name).mkdir(parents=True, exist_ok=True)

    print("FORGE MATERIALIZATION SMOKE")
    print(f"  scratch root: {_relative(root)}")
    print(f"  offline provider: {OFFLINE_PROVIDER_NAME} (no provider socket is opened)")
    failures: List[str] = []

    # ------------------------------------------------------------- 1. specification
    discovery = DiscoveryService(DiscoveryStore(root=root / "discovery"))
    session = discovery.start(GOAL)
    print("\n[1] SPECIFICATION (discovery)")
    _line("session", session.session_id)
    _line("questions", len(session.questions))
    session = _confirm_session(discovery, session)
    specification = session.specification
    if specification is None:
        failures.append("the confirmed session carries no specification")
        print("\nRESULT: FAILED (exit 1)")
        return 1
    _line("state", session.state.value)
    _line("goal", specification.goal)
    _line("platform", (specification.to_dict() or {}).get("platform", ""))

    # -------------------------------------------------------------------- 2. plan
    plan = Planner().create_plan(specification.goal)
    print("\n[2] PLAN (existing Planner)")
    _line("plan_id", plan.plan_id)
    _line("title", plan.title)
    _line("tasks", len(plan.tasks))

    # ----------------------------------------------------------- 3. write approval
    projects = ProjectService(ProjectStore(root=root / "projects"))
    project = projects.create_from_discovery(session, plan_ref=plan.plan_id)
    project = projects.confirm(project.project_id, plan_ref=plan.plan_id)
    project_workspace = projects.store.workspace_path(project.project_id, create=True)
    project_path = projects.store.project_path_relative(project.project_id)
    service = ForgeApiService(
        runtime=_offline_runtime(),
        run_store=RunStore(root=root / "runs"),
        workspace=Workspace(root / "run-workspace"),
        authorized_project_id="smoke-materialization",
    )
    print("\n[3] WRITE APPROVAL")
    _line("project_id", project.project_id)
    _line("project folder (repo-relative)", project_path)
    _line("write modes", f"{WRITE_MODE_MOCK_DETERMINISTIC}, {WRITE_MODE_REAL_PROVIDER_EXPLICIT}")

    # (a) no flag: the run must not write, and says so with an empty mode.
    no_flag = service.run_task_detailed(
        TaskRunRequest(
            description=TASK_TEXT,
            category="code",
            provider_name=OFFLINE_PROVIDER_NAME,
            write_mode=False,
        ),
        project_workspace=project_workspace,
        lifecycle_project_id=project.project_id,
        lifecycle_project_path=project_path,
    ).response
    _line("run without write_mode -> write_mode", f"'{no_flag.write_mode}'")
    _line("run without write_mode -> files_created", no_flag.files_created)
    if no_flag.write_mode != "" or no_flag.files_created != 0:
        failures.append("a run without write_mode wrote files")

    # (b) MOCK_DETERMINISTIC: the offline answer is the deterministic scaffold.
    approved = service.run_task_detailed(
        TaskRunRequest(
            description=TASK_TEXT,
            category="code",
            provider_name=OFFLINE_PROVIDER_NAME,
            write_mode=True,
        ),
        project_workspace=project_workspace,
        lifecycle_project_id=project.project_id,
        lifecycle_project_path=project_path,
    ).response
    _line("run with write_mode -> write_mode", approved.write_mode)
    _line("run with write_mode -> success", approved.success)
    _line("run with write_mode -> error", approved.error or "")
    if approved.write_mode != WRITE_MODE_MOCK_DETERMINISTIC:
        failures.append("the offline write run did not report MOCK_DETERMINISTIC")
    if not approved.success:
        failures.append(f"the write run failed: {approved.error}")

    # ------------------------------------------------------------- 4. files created
    print("\n[4] FILES CREATED")
    files_on_disk = sorted(
        path.relative_to(project_workspace).as_posix()
        for path in project_workspace.rglob("*")
        if path.is_file() and ".forge" not in path.relative_to(project_workspace).parts
    )
    _line("under the project workspace", files_on_disk)
    _line("response.files_created", approved.files_created)
    if "index.html" not in files_on_disk or "app.py" not in files_on_disk:
        failures.append("the project workspace does not hold the two written files")
    _line("run workspace files (must stay empty)", sorted(
        path.name for path in (root / "run-workspace").rglob("*") if path.is_file()
    ))

    # ----------------------------------------------------------- 5. artifact manifest
    attached = projects.attach_run(
        project.project_id,
        _run_source(service, approved),
        source_root=project_workspace,
    )
    version = attached.current()
    if version is None:
        failures.append("the run was not recorded as a version")
        print("\nRESULT: FAILED (exit 1)")
        return 1
    record = service.get_run_record(approved.run_id) or {}
    materialization = (record.get("project_state") or {}).get(
        MATERIALIZATION_PROJECT_STATE_KEY
    ) or {}
    print("\n[5] ARTIFACT MANIFEST")
    _line("materialization status", materialization.get("status", ""))
    _line("materialization files_created", materialization.get("files_created", 0))
    for artifact in version.files:
        print(
            f"    {artifact.path}: {artifact.kind}, {artifact.size} bytes, "
            f"sha256={artifact.sha256[:16]}..., {artifact.change}, "
            f"{artifact.verification_relevance}"
        )
    serialized = str(materialization)
    if "<!doctype html>" in serialized or "def describe" in serialized:
        failures.append("the manifest carries file contents")
    _line("manifest carries contents", "нет")

    # -------------------------------------------------------------- 6. verification
    print("\n[6] VERIFICATION")
    _line("version.verification", version.verification)
    _line("version.acceptance", version.acceptance)
    _line("materialization verification", materialization.get("verification", ""))
    if version.verification != "passed":
        failures.append(f"py_compile did not pass: {version.verification!r}")

    # --------------------------------------------------------------- 7. entry point
    print("\n[7] ENTRY POINT (from the manifest only)")
    entry = detect_entry_point(version.files, workspace=project_workspace)
    _line("entry point", entry.to_dict() if entry is not None else None)
    if entry is None or entry.path != "index.html":
        failures.append("the entry point was not detected from the manifest")
    elif entry.kind != "static_html" or entry.serve_kind != "static":
        failures.append(f"unexpected entry kind: {entry.kind}/{entry.serve_kind}")

    # ----------------------------------------------------------- 8. local preview
    print("\n[8] LOCAL PREVIEW (127.0.0.1, read-only, ephemeral port)")
    registry = PreviewRegistry()
    started = registry.start(project.project_id, project_workspace, entry)
    _line("available", started.get("available", False))
    _line("running", started.get("running", False))
    _line("url", started.get("url", ""))
    if not started.get("running", False):
        failures.append(f"the static preview did not start: {started.get('reason', '')}")
    else:
        status, body = _fetch(started["url"])
        _line("GET the preview URL", f"HTTP {status}, {len(body)} bytes")
        if status != 200:
            failures.append("the preview URL did not return the file")
        elif b"<!doctype html>" not in body:
            failures.append("the preview served an unexpected body")
        base = started["url"].rsplit("/", 1)[0]
        refused, _ = _fetch(base + "/%2e%2e/AGENTS.md")
        _line("GET a traversal probe", f"HTTP {refused}")
        if refused == 200:
            failures.append("the preview served a path outside the project workspace")
        stopped = registry.stop(project.project_id)
        _line("stopped", not stopped.get("running", True))
        after, _ = _fetch(started["url"])
        _line("GET after stop", f"HTTP {after}" if after else "connection refused")
        if after == 200:
            failures.append("the preview kept serving after stop")
    registry.stop_all()

    # -------------------------------------------------------- 9. final project status
    print("\n[9] FINAL PROJECT STATUS")
    stage = project_stage(
        files_count=version.file_count,
        verification=version.verification,
        acceptance=version.acceptance,
        lifecycle_status=attached.status.value,
        archived=bool(attached.archived),
        preview_available=False,
        preview_not_required=bool(version.preview_not_required),
    )
    _line("project status", attached.status.value)
    _line("stage", stage)
    _line("current_version", attached.current_version)
    _line("last_run_at", attached.last_run_at)
    if attached.status.value != "COMPLETED":
        failures.append(
            f"a verified, accepted run with artifacts must be COMPLETED "
            f"(got {attached.status.value})"
        )
    if stage != "VERIFIED":
        failures.append(f"without a running preview the stage must be VERIFIED, got {stage!r}")

    print("\nSUMMARY")
    _line("project", attached.project_id)
    _line("folder (repo-relative)", attached.project_path)
    _line("versions", [item.version for item in attached.versions])
    _line("files", [artifact.path for artifact in version.files])
    _line("write mode", approved.write_mode)

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: OK (exit 0)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the module
    raise SystemExit(main(sys.argv[1:]))
