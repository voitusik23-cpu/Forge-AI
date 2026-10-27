"""The evidence discipline: a claim is a fact only when the system measured it.

Why this file exists
--------------------
The owner's rule, approved from a curated reverse-engineering prompt library: **every
claim must carry evidence, and facts / inferences / unknowns must be separated**.
Forge used to report «готово» on the strength of its own summary, so a wrong claim was
invisible. The product now builds an evidence block on every result
(``app/reports/evidence.py``) and shows it as one line that opens into three lists
(«Проверено: N · предположения: M · неизвестно: K»).

What is asserted here, in the order the rules are enforced in code:

1. two written files plus a passed verification produce **two SHA-256 proofs and the
   verification command**; the model's summary lands in ``assumed``, marked as
   unchecked; a fully checked run has no unknowns;
2. a zero-file run has an **empty** ``verified`` list and names «файлы не записаны» --
   even when the outcome word says ``COMPLETED``;
3. a provider that answered but proved nothing puts its words in ``assumed`` only;
4. a static project with no verification says «проверка не применима», never
   «проверено»;
5. the UI banner counts are the list lengths, in both languages;
6. the write-mode output contract carries the closing sentence (checked and assumed and
   unknown), and a read-only run's prompt does not.

Rules 1-4 and 6 are unit-level where they can be, and the wiring is proved end to end
over the real HTTP surface with a **fake provider installed at composition time** --
the seam that survives ``ForgeApiService.__init__`` and therefore the only one that
cannot reach the network. No real model is called and no key is read.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

from app.agent_runtime.output_contract import (
    OUTPUT_CONTRACT,
    RETRY_INSTRUCTION,
    append_output_contract,
)
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.config import ProviderConfig
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.projects.service import ProjectService
from app.projects.store import PROJECTS_ROOT_ENV_VAR, ProjectStore
from app.reports.evidence import ASSUMED_BASIS, build_evidence
from app.runtime.run_store import RunStore
from app.tools.workspace import Workspace
from offline_runtime import offline_runtime

REPOSITORY = pathlib.Path(__file__).resolve().parents[1]
UI_SOURCE = REPOSITORY / "ui" / "src"
EVIDENCE_MODULE = UI_SOURCE / "evidence.ts"
EVIDENCE_BLOCK = UI_SOURCE / "components" / "EvidenceBlock.tsx"
I18N = UI_SOURCE / "i18n.ts"
FORGE_SCREEN = UI_SOURCE / "screens" / "Forge.tsx"
PROJECTS_SCREEN = UI_SOURCE / "screens" / "Projects.tsx"

#: The exact sentence the write-mode contract must end with. It is one string here so
#: the contract, the service prompt and the report are asserted against the same words.
DISCIPLINE_SENTENCE = (
    "В конце ответа перечисли: что ты проверил, что предполагаешь, что осталось "
    "неизвестным; не утверждай, что работает, если не проверял."
)

SHA_APP = "a" * 64
SHA_HTML = "b" * 64
SHA_CSS = "c" * 64

#: The gaps a run may honestly report. A test that expects *only* real gaps uses this
#: allow-list; anything outside it would be a manufactured unknown.
REAL_GAPS = frozenset(
    {
        "живой запуск не проверялся",
        "файлы не записаны",
        "файлы записаны без доказательства",
        "версия без файлов",
        "версия проекта не записана",
        "проверка не применима",
        "проверка не выполнялась",
        "проверка не пройдена",
        "проверка ещё не выполнена",
        "проверка без доказательства",
        "проверка в неизвестном состоянии",
        "приёмка не подтверждена",
        "приёмка без доказательства",
    }
)


def _node(script: str) -> str:
    node = shutil.which("node")
    if node is None:  # pragma: no cover - node is present on this host
        raise unittest.SkipTest("node is not installed; the UI rule cannot be executed")
    completed = subprocess.run(
        [node, "--experimental-strip-types", "--input-type=module", "-e", script],
        cwd=str(REPOSITORY),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout


# --------------------------------------------------------------------------- #
# rules 1-4: the block itself, from fakes
# --------------------------------------------------------------------------- #


class VerifiedFactsOnlyTests(unittest.TestCase):
    """``verified`` holds measured facts only; everything else is named."""

    maxDiff = None

    def test_two_files_and_passed_verification_carry_their_proofs(self) -> None:
        evidence = build_evidence(
            outcome="PROJECT_COMPLETED",
            files=[
                {
                    "path": "app.py",
                    "size": 120,
                    "sha256": SHA_APP,
                    "change": "create",
                    "kind": "python",
                },
                {
                    "path": "index.html",
                    "size": 40,
                    "sha256": SHA_HTML,
                    "change": "create",
                    "kind": "markup",
                },
            ],
            verification="passed",
            acceptance="passed",
            model_summary="Готово: добавил сервер и страницу.",
            answer_excerpt="Готово: добавил сервер и страницу.",
            run={
                "run_id": "run-evidence-two-files",
                "url": "http://127.0.0.1:8765/",
                "http_status": 200,
            },
            project={"version": 2},
        )

        verified = evidence["verified"]
        file_facts = [item for item in verified if item["kind"] == "file"]
        self.assertEqual(len(file_facts), 2)
        self.assertEqual(
            [item["claim"] for item in file_facts],
            ["файл записан: app.py", "файл записан: index.html"],
        )

        # Every file proof names its path, its size and its digest -- not just a path.
        proof_files = {item["path"]: item for item in evidence["proof"]["files"]}
        self.assertEqual(proof_files["app.py"]["sha256"], SHA_APP)
        self.assertEqual(proof_files["app.py"]["size"], 120)
        self.assertEqual(proof_files["index.html"]["sha256"], SHA_HTML)
        for item in file_facts:
            path = item["claim"].split(": ", 1)[1]
            self.assertIn(proof_files[path]["sha256"], item["evidence"])
            self.assertIn(str(proof_files[path]["size"]), item["evidence"])

        # The verification is proven by the command that produced it.
        verification_facts = [item for item in verified if item["kind"] == "verification"]
        self.assertEqual(len(verification_facts), 1)
        command = evidence["proof"]["verification"]["command"]
        self.assertIn("py_compile", command)
        self.assertIn("app.py", command)
        self.assertEqual(verification_facts[0]["evidence"], command)
        self.assertIn(command, evidence["proof"]["commands"])

        # The acceptance verdict and the version are proven too.
        self.assertEqual(
            [item["kind"] for item in verified],
            ["file", "file", "verification", "acceptance", "version", "server"],
        )

        # The model's sentence is quoted, not checked.
        self.assertEqual(
            [item["claim"] for item in evidence["assumed"]],
            ["Готово: добавил сервер и страницу."],
        )
        self.assertEqual(evidence["assumed"][0]["basis"], ASSUMED_BASIS)
        self.assertEqual(evidence["assumed"][0]["basis"], "сказано моделью, не проверено")

        self.assertEqual(evidence["proof"]["run_id"], "run-evidence-two-files")
        self.assertEqual(evidence["unknown"], [])

    def test_a_zero_file_run_has_no_verified_claim_whatever_the_outcome(self) -> None:
        evidence = build_evidence(
            outcome="PROJECT_COMPLETED",
            files=(),
            verification="",
            acceptance="failed",
            model_summary="Проект готов, всё проверено.",
            answer_excerpt="Проект готов, всё проверено.",
            run={"run_id": "run-evidence-zero"},
            project={"version": 4},
        )

        self.assertEqual(evidence["verified"], [])
        self.assertFalse(
            any("файл" in item["claim"] and "записан" in item["claim"] for item in evidence["verified"])
        )
        items = [item["item"] for item in evidence["unknown"]]
        self.assertIn("файлы не записаны", items)
        self.assertIn("версия без файлов", items)
        # The summary said "everything is checked"; the block does not believe it.
        self.assertEqual(
            [item["claim"] for item in evidence["assumed"]],
            ["Проект готов, всё проверено."],
        )

    def test_an_answer_with_nothing_verified_is_only_assumed(self) -> None:
        evidence = build_evidence(
            outcome="SPECIFICATION_READY",
            files=(),
            verification="",
            acceptance="",
            model_summary="",
            answer_excerpt="Я всё исправил и проверил, файлы уже на месте.",
            run={"run_id": "run-evidence-answer"},
            project=None,
        )

        self.assertEqual(evidence["verified"], [])
        self.assertEqual(len(evidence["assumed"]), 1)
        self.assertEqual(
            evidence["assumed"][0]["claim"],
            "Я всё исправил и проверил, файлы уже на месте.",
        )
        self.assertEqual(evidence["assumed"][0]["basis"], ASSUMED_BASIS)

    def test_a_static_project_says_verification_does_not_apply(self) -> None:
        evidence = build_evidence(
            outcome="PROJECT_COMPLETED",
            files=[
                {"path": "index.html", "size": 649, "sha256": SHA_HTML, "kind": "markup"},
                {"path": "styles.css", "size": 1094, "sha256": SHA_CSS, "kind": "style"},
            ],
            verification="not_applicable",
            acceptance="passed",
            model_summary="",
            answer_excerpt="",
            run={"run_id": "run-evidence-static"},
            project={"version": 1},
        )

        items = [item["item"] for item in evidence["unknown"]]
        self.assertIn("проверка не применима", items)
        self.assertFalse(any(item["kind"] == "verification" for item in evidence["verified"]))
        claims = [item["claim"] for item in evidence["verified"]]
        self.assertNotIn("проверка пройдена", claims)
        self.assertFalse(any("проверено" in claim for claim in claims))
        # The two files are still proven facts, with their digests.
        self.assertEqual(len(evidence["proof"]["files"]), 2)

    def test_a_file_without_a_digest_is_not_a_verified_file(self) -> None:
        evidence = build_evidence(
            outcome="COMPLETED",
            files=[{"path": "app.py", "size": 10}],
            verification="passed",
            acceptance="passed",
            model_summary="",
            answer_excerpt="",
            run={"run_id": "run-evidence-unproven"},
            project={"version": 1},
        )

        self.assertEqual(evidence["verified"], [])
        items = [item["item"] for item in evidence["unknown"]]
        self.assertIn("файлы записаны без доказательства", items)
        # A "passed" verification with no proven file cannot be a verified fact either.
        self.assertIn("проверка без доказательства", items)

    def test_every_unknown_is_a_named_real_gap(self) -> None:
        evidence = build_evidence(
            outcome="FAILED",
            files=(),
            verification="not_run",
            acceptance="not_evaluated",
            model_summary="",
            answer_excerpt="",
            run={"run_id": "run-evidence-gaps"},
            project={"version": 0},
        )

        self.assertTrue(evidence["unknown"])
        for item in evidence["unknown"]:
            self.assertIn(item["item"], REAL_GAPS)
            self.assertTrue(item["why"].strip())


# --------------------------------------------------------------------------- #
# the composed application over a throwaway store and a fake provider
# --------------------------------------------------------------------------- #

FAKE_PROVIDER = "deepseek"
FAKE_MODEL = "deepseek-chat"
WORKSPACE_PROJECT_ID = "evidence-workspace"
GOAL = "Локальный сервер и одна страница, без внешних сервисов."

SUMMARY = "Готово: добавил сервер и страницу."
APP_PY = '"""The evidence test server."""\n\nVALUE = 1\n'
INDEX_HTML = "<html><body>ok</body></html>\n"
ANSWER = (
    SUMMARY
    + "\n\n```python path=app.py\n"
    + APP_PY
    + "```\n\n```html path=index.html\n"
    + INDEX_HTML
    + "```\n"
)
TEXT_ANSWER = "Я всё исправил и проверил: файлы уже на месте."


class _ScriptedProvider(Provider):
    """One canned answer per call; the last repeats. No socket, no key, no cost."""

    def __init__(
        self,
        answers: list[str],
        *,
        name: str = FAKE_PROVIDER,
        model: str = FAKE_MODEL,
    ) -> None:
        super().__init__(ProviderConfig(provider_name=name, model_name=model))
        self.answers = list(answers)
        self.requests: list[ProviderRequest] = []

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.answers) - 1)
        return ProviderResponse(
            provider_name=self.provider_name,
            model_name=request.model_name or self.model_name,
            output=self.answers[index],
        )


class _LifecycleCase(unittest.TestCase):
    """A real project, a real app and a fake provider installed at composition time."""

    maxDiff = None

    def setUp(self) -> None:
        self.base = pathlib.Path(
            tempfile.mkdtemp(prefix="evidence-", dir=str(REPOSITORY / ".forge"))
        )
        self.addCleanup(lambda: shutil.rmtree(self.base, ignore_errors=True))
        self.root = self.base / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        (self.base / "run-workspace").mkdir(parents=True, exist_ok=True)
        (self.base / "runs").mkdir(parents=True, exist_ok=True)
        previous = os.environ.get(PROJECTS_ROOT_ENV_VAR)
        os.environ[PROJECTS_ROOT_ENV_VAR] = str(self.root)
        self.addCleanup(self._restore_root, previous)

        self.store = ProjectStore(root=self.root)
        self.projects = ProjectService(self.store)
        project = self.projects.create_from_discovery(self._session())
        project = self.projects.confirm(project.project_id)
        self.project_id = project.project_id

        runtime = offline_runtime()
        self.fake = _ScriptedProvider([ANSWER, TEXT_ANSWER])
        # BEFORE the service exists: its constructor rebuilds the executor, so a fake
        # patched in afterwards is discarded and the real provider would be asked.
        runtime.agent_registry._agents[FAKE_PROVIDER] = ProviderAgent(self.fake)
        service = ForgeApiService(
            runtime=runtime,
            run_store=RunStore(root=self.base / "runs"),
            workspace=Workspace(self.base / "run-workspace"),
            authorized_project_id=WORKSPACE_PROJECT_ID,
        )
        from fastapi.testclient import TestClient

        self.app = create_api_app(service, project_service=self.projects)
        self.addCleanup(self._stop_everything)
        self.client = TestClient(self.app)

    def _restore_root(self, previous) -> None:
        if previous is None:
            os.environ.pop(PROJECTS_ROOT_ENV_VAR, None)
        else:
            os.environ[PROJECTS_ROOT_ENV_VAR] = previous

    def _stop_everything(self) -> None:
        state = getattr(getattr(self, "app", None), "state", None)
        for name in ("forge_runner", "forge_previews"):
            target = getattr(state, name, None) if state is not None else None
            if target is not None:
                try:
                    target.stop_all()
                except Exception:  # noqa: BLE001 - shutdown is best effort
                    pass

    def _session(self) -> SimpleNamespace:
        class _Specification:
            def to_dict(self) -> dict:
                return {
                    "goal": GOAL,
                    "project_type": "web app",
                    "core_features": ["a page", "a server"],
                }

        return SimpleNamespace(
            session_id="disc-evidence-0001",
            initial_goal=GOAL,
            specification=_Specification(),
        )

    def _dispatch(self, *, write_mode: bool, project_id: str = "") -> dict:
        payload: dict = {
            "description": "Сделай локальный сервер и одну страницу.",
            "category": "code",
            "provider_name": FAKE_PROVIDER,
            "model_name": FAKE_MODEL,
        }
        if write_mode:
            payload["write_mode"] = True
        if project_id:
            payload["lifecycle_project_id"] = project_id
        response = self.client.post("/api/tasks/run", json=payload)
        self.assertLess(response.status_code, 400, response.text)
        return response.json()

    def _workspace_file(self, relative: str) -> pathlib.Path:
        path = self.store.workspace_path(self.project_id) / relative
        self.assertTrue(path.is_file(), f"the run did not write {relative}")
        return path

    def _digest_of(self, relative: str) -> str:
        return hashlib.sha256(self._workspace_file(relative).read_bytes()).hexdigest()


class RunResponseEvidenceTests(_LifecycleCase):
    """The run response and the project detail publish the block the owner reads."""

    def test_the_run_response_proves_the_two_files_and_the_command(self) -> None:
        run = self._dispatch(write_mode=True, project_id=self.project_id)
        self.assertEqual(run["files_created"], 2, run.get("error"))
        self.assertTrue(run["success"])

        evidence = run["evidence"]
        file_facts = [item for item in evidence["verified"] if item["kind"] == "file"]
        self.assertEqual(len(file_facts), 2)

        # The digests are the bytes on disk, not a number the writer reported.
        proof = {item["path"]: item for item in evidence["proof"]["files"]}
        self.assertEqual(proof["app.py"]["sha256"], self._digest_of("app.py"))
        self.assertEqual(proof["index.html"]["sha256"], self._digest_of("index.html"))
        self.assertEqual(proof["app.py"]["size"], self._workspace_file("app.py").stat().st_size)

        verification = [item for item in evidence["verified"] if item["kind"] == "verification"]
        self.assertEqual(len(verification), 1)
        command = evidence["proof"]["verification"]["command"]
        self.assertIn("py_compile", command)
        self.assertIn(command, evidence["proof"]["commands"])

        # The model's own first line is assumed, and labelled as such.
        self.assertIn(SUMMARY, [item["claim"] for item in evidence["assumed"]])
        self.assertTrue(
            all(item["basis"] == ASSUMED_BASIS for item in evidence["assumed"])
        )

        # A change run does not start the project, so the live run is a real gap.
        self.assertEqual(
            [item["item"] for item in evidence["unknown"]],
            ["живой запуск не проверялся"],
        )
        self.assertEqual(
            [item["kind"] for item in evidence["verified"]],
            ["file", "file", "verification", "acceptance", "version"],
        )

    def test_the_project_detail_publishes_evidence_for_the_current_version(self) -> None:
        run = self._dispatch(write_mode=True, project_id=self.project_id)
        self.assertTrue(run["success"], run.get("error"))
        detail = self.client.get(f"/api/projects/{self.project_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        body = detail.json()

        evidence = body["evidence"]
        self.assertEqual(len(evidence["proof"]["files"]), 2)
        recorded = {entry["path"]: entry for entry in evidence["proof"]["files"]}
        for item in body["files"]:
            self.assertEqual(recorded[item["path"]]["sha256"], item["sha256"])

        claims = [item["claim"] for item in evidence["verified"]]
        self.assertIn("проверка пройдена", claims)
        self.assertIn("приёмка пройдена", claims)
        self.assertIn(f"версия v{body['project']['current_version']} записана", claims)
        # The detail route has no status code for a served URL, so it does not claim one.
        self.assertIn("живой запуск не проверялся", [i["item"] for i in evidence["unknown"]])

    def test_a_zero_file_run_is_never_a_verified_success(self) -> None:
        self.fake.answers = [TEXT_ANSWER]
        run = self._dispatch(write_mode=True, project_id=self.project_id)

        self.assertFalse(run["success"])
        self.assertEqual(run["files_created"], 0)
        evidence = run["evidence"]
        self.assertEqual(evidence["verified"], [])
        self.assertIn("файлы не записаны", [item["item"] for item in evidence["unknown"]])
        self.assertIn(TEXT_ANSWER, [item["claim"] for item in evidence["assumed"]])

    def test_the_write_prompt_carries_the_discipline_and_a_read_only_one_does_not(
        self,
    ) -> None:
        self._dispatch(write_mode=False)
        self.assertTrue(self.fake.requests)
        self.assertFalse(
            any(DISCIPLINE_SENTENCE in request.prompt for request in self.fake.requests)
        )

        self._dispatch(write_mode=True, project_id=self.project_id)
        self.assertTrue(
            any(DISCIPLINE_SENTENCE in request.prompt for request in self.fake.requests)
        )


# --------------------------------------------------------------------------- #
# rule 5: the banner counts what it opens, in both languages
# --------------------------------------------------------------------------- #


def _counts_from_node(payload: dict) -> dict:
    script = (
        f"import {{ evidenceCounts }} from {json.dumps(EVIDENCE_MODULE.as_uri())};"
        "process.stdout.write(JSON.stringify(evidenceCounts("
        + json.dumps(payload)
        + ")));"
    )
    return json.loads(_node(script))


class UiBannerTests(unittest.TestCase):
    """«Проверено: N · предположения: M · неизвестно: K» -- N is the list's length."""

    maxDiff = None

    def test_the_banner_counts_equal_the_list_lengths(self) -> None:
        payload = {
            "verified": [{"claim": "a"}, {"claim": "b"}],
            "assumed": [{"claim": "c"}],
            "unknown": [{"item": "d"}, {"item": "e"}, {"item": "f"}],
        }
        counts = _counts_from_node(payload)
        self.assertEqual(counts["verified"], len(payload["verified"]))
        self.assertEqual(counts["assumed"], len(payload["assumed"]))
        self.assertEqual(counts["unknown"], len(payload["unknown"]))

        # A missing block is three zeros in the pure function, and the component
        # renders nothing for it (`hasEvidence`), so "all clear" is never shown.
        self.assertEqual(_counts_from_node({}), {"verified": 0, "assumed": 0, "unknown": 0})

    def test_both_languages_print_the_counts_from_the_lists(self) -> None:
        counts = {"verified": 2, "assumed": 1, "unknown": 3}
        templates = re.findall(
            r'"evidence\.banner":\s*"([^"]+)"', I18N.read_text(encoding="utf-8")
        )
        self.assertEqual(len(templates), 2, "the banner must exist in both languages")
        for template in templates:
            for token in ("{verified}", "{assumed}", "{unknown}"):
                self.assertIn(token, template)

        ru, en = templates
        rendered_ru = (
            ru.replace("{verified}", str(counts["verified"]))
            .replace("{assumed}", str(counts["assumed"]))
            .replace("{unknown}", str(counts["unknown"]))
        )
        rendered_en = (
            en.replace("{verified}", str(counts["verified"]))
            .replace("{assumed}", str(counts["assumed"]))
            .replace("{unknown}", str(counts["unknown"]))
        )
        self.assertEqual(
            rendered_ru, "Проверено: 2 · предположения: 1 · неизвестно: 3"
        )
        self.assertEqual(rendered_en, "Verified: 2 · assumptions: 1 · unknown: 3")

    def test_the_block_counts_the_lists_it_renders_and_sits_in_both_cards(self) -> None:
        component = EVIDENCE_BLOCK.read_text(encoding="utf-8")
        self.assertIn("evidenceCounts(evidence)", component)
        self.assertIn('t(lang, "evidence.banner")', component)
        self.assertIn("hasEvidence(evidence)", component)

        projects = PROJECTS_SCREEN.read_text(encoding="utf-8")
        self.assertIn("<EvidenceBlock", projects)
        self.assertIn('testId="change-evidence"', projects)
        self.assertIn('testId="project-evidence"', projects)
        forge = FORGE_SCREEN.read_text(encoding="utf-8")
        self.assertIn("<EvidenceBlock", forge)
        self.assertIn('testId="forge-evidence"', forge)


# --------------------------------------------------------------------------- #
# rule 6: the model is asked for the same discipline, in write mode only
# --------------------------------------------------------------------------- #


class OutputContractTests(unittest.TestCase):
    """The write-mode contract ends with the checked / assumed / unknown sentence."""

    def test_the_contract_carries_the_sentence(self) -> None:
        self.assertIn(DISCIPLINE_SENTENCE, OUTPUT_CONTRACT)
        prompt = append_output_contract("Задача: сделай страницу.")
        self.assertIn(DISCIPLINE_SENTENCE, prompt)
        # Idempotent: the sentence is not appended twice.
        self.assertEqual(
            append_output_contract(prompt).count(DISCIPLINE_SENTENCE), 1
        )

    def test_the_retry_and_a_read_only_prompt_do_not_carry_it(self) -> None:
        self.assertNotIn(DISCIPLINE_SENTENCE, RETRY_INSTRUCTION)
        read_only = "Задача: опиши, что уже есть в проекте."
        self.assertNotIn(DISCIPLINE_SENTENCE, read_only)
        # An empty prompt is returned unchanged, so no caller can turn a missing task
        # into a bare instruction.
        self.assertEqual(append_output_contract(""), "")


if __name__ == "__main__":
    unittest.main()
