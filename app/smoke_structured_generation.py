"""Smoke of the structured-intent parser and its truth rules, offline and repeatable.

Run it with::

    .\\\\.venv\\\\Scripts\\\\python.exe -m app.smoke_structured_generation

Exit code 0 means every step below really ran. **No provider is called and no socket
is opened**: the parser is pure, and the one dispatch at the end uses the repository's
offline ``mock`` provider (``app/agents/providers/mock.py``), whose answer is a
deterministic string.

For each of four answers it prints, in the owner's order of interest:

1. the original task the answer was given for;
2. the detected intent;
3. the summary the answer declared;
4. the next action the intent asks for;
5. whether a false ``COMPLETED`` was refused -- the same answer, handed every
   completion fact a finished project could have, is put through the one classifier
   (:func:`app.agent_runtime.outcome.derive_outcome`), and the smoke fails if it says
   ``PROJECT_COMPLETED`` for anything other than a generation *with written files*.

The four scenarios are the four branches that matter: a clarification request, a plan,
a generation that declares (fake) files, and a malformed envelope. The last one proves
the rule that the answer cannot break a run: it is reported, it is not trusted, and it
is never a completed project.

Everything is written under ``.forge/smoke-structured-intent`` (wiped at start), so the
smoke never touches the deployment's own run store.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from app.agent_runtime.intent import parse_model_intent
from app.agent_runtime.outcome import RunOutcome, derive_outcome
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.config.settings import OFFLINE_PROVIDER_NAME, RuntimeSettings
from app.projects.store import repository_root
from app.runtime.bootstrap import create_runtime
from app.runtime.run_store import RunStore

__all__ = ["main"]


#: From where the smoke writes. Inside the repository, gitignored, wiped at start.
SMOKE_ROOT = repository_root() / ".forge" / "smoke-structured-intent"

#: The task every scenario is an answer to.
TASK = (
    "Хочу небольшое локальное веб-приложение для учёта домашних расходов: "
    "добавление трат, категории и месячный отчёт."
)

#: Every completion fact a finished project could have. The classifier must still
#: refuse completion for every intent that is not a generation.
COMPLETE_FACTS: Dict[str, Any] = dict(
    run_state="COMPLETED",
    run_success=True,
    project_exists=True,
    project_workspace_exists=True,
    artifact_count=2,
    artifact_manifest=("app.py", "README.md"),
    verification="passed",
    acceptance="passed",
    specification_confirmed=True,
)

SCENARIOS = (
    (
        "CLARIFICATION_REQUIRED",
        json.dumps(
            {
                "intent_type": "CLARIFICATION_REQUIRED",
                "summary": "Two answers are needed before planning.",
                "questions": [
                    "Какая валюта должна быть в учёте?",
                    "Нужен ли экспорт расходов в Excel?",
                ],
            },
            ensure_ascii=False,
        ),
    ),
    (
        "PLAN_READY",
        json.dumps(
            {
                "intent_type": "PLAN_READY",
                "summary": "A three-step plan.",
                "plan": [
                    "write app.py with the local server",
                    "add the monthly report",
                    "verify the project",
                ],
            },
            ensure_ascii=False,
        ),
    ),
    (
        "GENERATE_PROJECT",
        json.dumps(
            {
                "intent_type": "GENERATE_PROJECT",
                "summary": "Two files: the server and the readme.",
                # Fake files, on purpose: the intent *declares* them, and the smoke
                # proves that a declaration alone still cannot complete a project.
                "files": {"app.py": "print('fake file')\n", "README.md": "# fake\n"},
            },
            ensure_ascii=False,
        ),
    ),
    (
        "MALFORMED",
        '{"intent_type": "GENERATE_PROJECT", "summary": "truncated", "files": {',
    ),
)


def _line(label: str, value: object) -> None:
    print(f"  {label}: {value}")


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(repository_root()).as_posix()
    except ValueError:
        return path.as_posix()


def _offline_runtime() -> Any:
    """A runtime whose only enabled provider is the offline ``mock`` one."""

    return create_runtime(
        settings=RuntimeSettings(
            default_provider=OFFLINE_PROVIDER_NAME,
            enabled_providers=(OFFLINE_PROVIDER_NAME,),
            provider_fallback_chain=(),
        ),
        test_mode=True,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    root = SMOKE_ROOT
    shutil.rmtree(root, ignore_errors=True)
    (root / "runs").mkdir(parents=True, exist_ok=True)
    previous_run_root = os.environ.get("FORGE_RUN_STORE_ROOT")
    os.environ["FORGE_RUN_STORE_ROOT"] = str(root / "runs")

    print("FORGE STRUCTURED INTENT SMOKE")
    print(f"  scratch root: {_relative(root)}")
    print(f"  original task: {TASK}")
    failures: List[str] = []
    false_completed_count = 0

    try:
        # ------------------------------------------------------------ the parser
        for label, answer in SCENARIOS:
            intent = parse_model_intent(answer)
            print(f"\n[{label}]")
            _line("answer shape", "malformed envelope" if label == "MALFORMED" else "envelope")
            _line("detected intent", intent.intent_type.value)
            _line("summary", intent.summary or "(none declared)")
            _line("next action", intent.next_action)
            _line("valid", intent.valid)
            _line("validation errors", list(intent.errors))
            # What the run response would publish for this answer.
            _line("response row", json.dumps(intent.response_payload(), ensure_ascii=False))
            _line("raw answer preserved", bool(intent.raw_text == answer))

            # The truth rule: the same answer with every completion fact must not
            # become a completed project -- except a real generation, and even then
            # only because the facts above say the files really exist.
            outcome = derive_outcome(intent_type=intent.intent_type.value, **COMPLETE_FACTS)
            _line("outcome with every completion fact", outcome.value)
            false_completed = outcome is RunOutcome.PROJECT_COMPLETED
            if false_completed and intent.intent_type.value != "GENERATE_PROJECT":
                # Only a generation may be completed; the positive control below is
                # counted separately.
                false_completed_count += 1
            _line("false COMPLETED refused", not false_completed or (
                intent.intent_type.value == "GENERATE_PROJECT"
            ))

            if label == "MALFORMED":
                if intent.intent_type.value != "FINAL_ANSWER":
                    failures.append(
                        "a malformed envelope must be reported as FINAL_ANSWER, got "
                        f"{intent.intent_type.value}"
                    )
                if intent.valid:
                    failures.append("a malformed envelope must not be valid")
                if false_completed:
                    failures.append("a malformed envelope reached PROJECT_COMPLETED")
            elif label == "GENERATE_PROJECT":
                # Declared files with no fact that they were written: still no project.
                declared_only = derive_outcome(
                    intent_type=intent.intent_type.value,
                    **{
                        **COMPLETE_FACTS,
                        "artifact_count": 0,
                        "artifact_manifest": (),
                    },
                )
                _line("declared files, nothing written", declared_only.value)
                if declared_only is RunOutcome.PROJECT_COMPLETED:
                    failures.append(
                        "a generation that wrote nothing reached PROJECT_COMPLETED"
                    )
                if outcome is not RunOutcome.PROJECT_COMPLETED:
                    failures.append(
                        "a generation with real facts must be allowed to complete"
                    )
            else:
                if false_completed:
                    failures.append(f"{label} reached PROJECT_COMPLETED")

            if not intent.raw_text == answer:
                failures.append(f"{label}: the raw answer was not preserved")

        # ------------------------------------- the same rule on the real run path
        # One dispatch, offline: the response publishes the parsed intent, and the
        # published state is the classifier's word -- never "COMPLETED".
        print("\n[DISPATCH: the real run path, offline provider]")
        service = ForgeApiService(
            runtime=_offline_runtime(),
            run_store=RunStore(root=root / "runs"),
            authorized_project_id="smoke-structured-intent",
        )
        result = service.run_task_detailed(
            TaskRunRequest(
                description=TASK,
                category="analysis",
                provider_name=OFFLINE_PROVIDER_NAME,
            )
        )
        response = result.response
        _line("run state", response.state)
        _line("run outcome", response.outcome)
        _line("provider used", response.provider_used)
        if response.model_intent:
            _line("model intent", json.dumps(response.model_intent, ensure_ascii=False))
        else:
            # This entry point parses no model answer: its "answer" is a
            # deterministic offline report, so the row is empty rather than
            # invented. An empty row is honest; a fabricated one would not be.
            _line("model intent", "(empty: this entry point parses no model answer)")
        _line("files created", response.files_created)
        _line("project created", response.project_created)
        if response.state == "COMPLETED":
            failures.append("an offline text answer was reported as COMPLETED")
        if response.outcome == "PROJECT_COMPLETED":
            failures.append("an offline text answer reached PROJECT_COMPLETED")
        if response.model_intent and not response.model_intent.get("intent_type"):
            failures.append("a published model_intent row has no intent_type")

        print("\nSUMMARY")
        _line("scenarios", [label for label, _answer in SCENARIOS])
        _line("false COMPLETED events", false_completed_count)
        _line("network used", False)
    finally:
        if previous_run_root is None:
            os.environ.pop("FORGE_RUN_STORE_ROOT", None)
        else:
            os.environ["FORGE_RUN_STORE_ROOT"] = previous_run_root

    if failures:
        print("\nFAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nRESULT: OK (exit 0)")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by running the module
    raise SystemExit(main(sys.argv[1:]))
