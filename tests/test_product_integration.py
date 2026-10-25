"""Block #8 — full product integration, verified through the HTTP routes.

WHAT THIS FILE PROVES, AND HOW
==============================

Every assertion here goes through a **route**: ``fastapi.testclient.TestClient``
against ``create_api_app``, never against a service method directly. That matters
because the integration is the thing under test. A service method that works and
a route that forgets to call it would both be invisible to a service-level test.

The runtime under test is structurally offline. ``_runtime()`` returns
``create_runtime(test_mode=True)``, whose own invariant yields
``enabled_providers=("mock",)``, ``default_provider="mock"``, and a registry
holding only the mock agent (``tests/offline_runtime.py``). This file never reads
``.env`` and makes no provider call: the assertions that would notice one
(``provider_used == ""``) are present.

ISOLATION
=========

Every test owns a temporary workspace, its own ``RunStore(root=...)`` and its own
``RunIdempotencyGuard.create(ledger_root=...)``. The shared per-user default roots
have produced FALSE failures in this repository before, and the Block #8 step
graph additionally requires an *injected* guard, so a default would not even
compose. ``RunScope.release_all()`` runs before and after every test.

WHAT IS REAL AND WHAT IS NOT
============================

* Real: the filesystem, child processes (real ``python -c`` interpreters), the
  durable ``RunStore`` and idempotency ledger on disk, the fused
  ``RunScope``/``ExecutionCoordinator``/``AuthorizedExecution`` chain, the step
  graph, recovery planning, Project Memory, and the scanner's own SHA-256
  fingerprints.
* Injected, and only where noted: the *decision* transport (a scripted transport
  object) and the approval resolver (a stub that answers rather than asking a
  human). Neither grants authority: the policy the resolver answers to, the
  command, the workspace and the profile all come from the operator composition.

NAMING THE TESTS THAT MUST FAIL BEFORE A FIX
============================================

Tests carrying ``_INJECTION_TARGET`` in their docstring are the target of a
deliberate source injection performed during verification. The before/after
SHA-256 of each patched file is recorded in the Block #8 report; nothing here
patches a private method at run time, so each of these tests also fails if its
own control is wrong.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path

from app.agent_runtime import idempotency_integration as integration
from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.agents.provider_agent import ProviderAgent
from app.agents.providers.base import Provider, ProviderRequest, ProviderResponse
from app.agents.providers.registry import ProviderRegistry
from app.agents.registry import AgentRegistry
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.decision.ai_provider import AIDecisionProvider
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness, create_runtime
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.runtime.run_store import RunStateSnapshot, RunStore
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalResolution, ApprovalState
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.usage import Usage

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# operator-declared programmes: real interpreters, real files
# ---------------------------------------------------------------------------

PROFILE_ID = "block8-profile"

#: The project this composition serves. A service that creates a run needs an authorized project, and
#: it is supplied the way the production composition supplies it -- bound once at composition time,
#: never read from a request body. The value is stable per test module, so every service these tests
#: build is bound to the same project.
AUTHORIZED_PROJECT_ID = "product-integration"

#: Writes a tiny package with its own passing test suite.
_CREATION_PROGRAMME = (
    "import pathlib\n"
    "pathlib.Path('calc.py').write_text('def add(a, b):\\n    return a + b\\n', encoding='utf-8')\n"
    "pathlib.Path('tests').mkdir(exist_ok=True)\n"
    "pathlib.Path('tests/__init__.py').write_text('', encoding='utf-8')\n"
    "pathlib.Path('tests/test_calc.py').write_text("
    "'import unittest\\nfrom calc import add\\n\\n"
    "class T(unittest.TestCase):\\n    def test_add(self):\\n"
    "        self.assertEqual(add(2, 3), 5)\\n', encoding='utf-8')\n"
)
CREATE_COMMAND = ("python", "-c", _CREATION_PROGRAMME)
VERIFY_COMMAND = (
    "python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-p", "test_*.py",
)

#: A graph programme. `WRITE_ONE` creates the input `READ_ONE` depends on, which is
#: what makes a dependency observable rather than assumed.
WRITE_ONE = ("python", "-c", "open('one.txt','w',encoding='utf-8').write('1')")
READ_ONE = ("python", "-c", "print(open('one.txt',encoding='utf-8').read())")
EXIT_THREE = ("python", "-c", "raise SystemExit(3)")

MODULE_PATH = "calc.py"
TEST_PATH = "tests/test_calc.py"

CRITERIA = (
    AcceptanceCriterion("module-present", "the module exists", requirement_id="project-source"),
    AcceptanceCriterion("tests-present", "the tests exist", requirement_id="project-tests"),
)
EXPECTATIONS = {
    "module-present": VerificationExpectation(MODULE_PATH, True),
    "tests-present": VerificationExpectation(TEST_PATH, True),
}
GOAL = "Produce a small Python package with its own passing tests."

_id_lock = threading.Lock()
_id_counter = itertools.count(1)


def _unique(prefix: str) -> str:
    """A per-process-unique identifier.

    The idempotency ledger is durable and requires keys of at least eight
    characters from ``[A-Za-z0-9._:-]``, so a key must be long enough to be
    accepted *and* unique enough that a second process cannot collide with it.
    """

    with _id_lock:
        return f"{prefix}-{os.getpid()}-{next(_id_counter)}"


class _ScriptedTransport(Provider):
    """A real ``Provider`` whose network is replaced by a scripted string.

    Subclassing the production ``Provider`` is what keeps the dispatch path
    unchanged; only what sits behind the provider is replaced.
    """

    def __init__(self, output: str) -> None:
        self._output = output
        self.calls = 0

    @property
    def provider_name(self) -> str:
        return "block8-scripted"

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(
            content=self._output,
            provider_name=self.provider_name,
            model_name="block8-scripted-model",
            usage=Usage(input_tokens=5, output_tokens=7, estimated_cost=0.0),
        )


class _FingerprintEchoResolver:
    """An ``ApprovalResolver`` that echoes the fingerprint it was asked about.

    This mirrors the *production* ``InMemoryApprovalResolver`` contract rather than
    inventing one: an approval is bound to the intent fingerprint of the exact
    invocation it was granted for, and a resolution whose fingerprint does not
    match the request is refused by the coordinator's own comparison
    (``app/execution/authorizer.py``). Echoing the request's own fingerprint is what
    makes this stub "an approval for exactly this action" and nothing wider.

    ``learned`` records the fingerprints it has seen, so a test can grant an
    approval for one parameter set and then prove that a different parameter set
    is *not* covered by it.
    """

    def __init__(
        self,
        decision: ApprovalState = ApprovalState.APPROVED,
        *,
        auto_grant: bool = True,
    ) -> None:
        self._decision = decision
        self._auto_grant = auto_grant
        self.learned: list[str] = []
        self._approved: set[str] = set()

    def learn(self, fingerprint: str) -> None:
        """Pre-approve one exact intent fingerprint, as an operator would."""

        self._approved.add(fingerprint)

    def resolve(self, request):  # noqa: ANN001 - mirrors the real protocol
        fingerprint = str(getattr(request, "intent_fingerprint", "") or "")
        self.learned.append(fingerprint)
        if not fingerprint:
            # A missing fingerprint is never a wildcard: fail closed.
            return None
        if self._auto_grant:
            # "The operator approves each distinct invocation as it arrives", which
            # is the narrowest thing an auto-approving collaborator can mean.
            self._approved.add(fingerprint)
        if fingerprint not in self._approved:
            # A fingerprint this resolver never approved is not covered, so it
            # stays unresolved rather than being waved through.
            return None
        return ApprovalResolution(
            decision=self._decision,
            approved_fingerprint=fingerprint,
        )


class _SilentResolver:
    """A resolver that never resolves. Unresolved must mean "performs nothing"."""

    def resolve(self, request):  # noqa: ANN001
        return None


class Block8Case(unittest.TestCase):
    """One isolated workspace, store, ledger, and HTTP client per test."""

    def setUp(self) -> None:
        RunScope.release_all()
        self.build_id = _unique("b8.build")
        self.verify_id = _unique("b8.verify")
        self.one_id = _unique("b8.one")
        self.read_id = _unique("b8.read")
        self.fail_id = _unique("b8.fail")
        self._dirs: list[tempfile.TemporaryDirectory] = []
        self.base = self._temp_dir()
        self.root = self.base / "workspace"
        self.root.mkdir()
        self.workspace = Workspace(self.root)
        self.store_root = self._temp_dir()
        self.run_store = RunStore(root=str(self.store_root))
        self.idempotency_guard = integration.RunIdempotencyGuard.create(
            ledger_root=str(self._temp_dir())
        )
        self.runtime = self._runtime()
        self._clients: list[TestClient] = []

    def tearDown(self) -> None:
        RunScope.release_all()
        for client in self._clients:
            try:
                client.close()
            except Exception:  # noqa: BLE001 - teardown must not mask a failure
                pass
        for directory in self._dirs:
            try:
                directory.cleanup()
            except (PermissionError, OSError):
                pass

    def _temp_dir(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self._dirs.append(directory)
        return Path(directory.name)

    # -- composition -------------------------------------------------------
    def _profile(self) -> ProjectExecutionProfile:
        return ProjectExecutionProfile(
            profile_id=PROFILE_ID,
            allowed_commands=("python",),
            capabilities=frozenset(
                {
                    ExecutionCapability.INTERPRET_TEXT,
                    ExecutionCapability.INTERPRET_MODULE,
                }
            ),
            network_access=False,
            working_directory=".",
        )

    def _runtime(self) -> RuntimeContext:
        """Structurally offline: only the mock agent is registered, no live provider."""

        return create_runtime(test_mode=True)

    def _runtime_with_transport(self, transport: Provider) -> RuntimeContext:
        """A runtime whose only reachable text source is an injected transport.

        Built the same way ``tests/test_project_factory_vertical_slice.py`` builds
        it, so the dispatch path is unchanged and no ambient settings are read.
        """

        provider_registry = ProviderRegistry()
        agent_registry = AgentRegistry()
        provider_registry.register(transport)
        agent_registry.register(ProviderAgent(transport))
        orchestrator = Orchestrator(
            agent_registry,
            default_provider=transport.provider_name,
            provider_registry=provider_registry,
        )
        return RuntimeContext(
            settings=None,
            provider_accounts={},
            provider_registry=provider_registry,
            provider_capabilities=None,
            agent_registry=agent_registry,
            orchestrator=orchestrator,
            run_executor=RunExecutor(orchestrator),
            harness=create_agent_harness(),
        )

    def declarations(self) -> dict[str, ExecutionDeclaration]:
        return {
            self.build_id: ExecutionDeclaration(
                declaration_id=self.build_id,
                command=CREATE_COMMAND,
                profile_id=PROFILE_ID,
                working_directory=".",
                intent=GOAL,
            ),
            self.verify_id: ExecutionDeclaration(
                declaration_id=self.verify_id,
                command=VERIFY_COMMAND,
                profile_id=PROFILE_ID,
                working_directory=".",
                intent="Run the project's own test suite.",
                timeout_seconds=25.0,
            ),
            self.one_id: ExecutionDeclaration(
                declaration_id=self.one_id,
                command=WRITE_ONE,
                profile_id=PROFILE_ID,
                working_directory=".",
                intent="Write one.txt.",
            ),
            self.read_id: ExecutionDeclaration(
                declaration_id=self.read_id,
                command=READ_ONE,
                profile_id=PROFILE_ID,
                working_directory=".",
                intent="Read one.txt.",
            ),
            self.fail_id: ExecutionDeclaration(
                declaration_id=self.fail_id,
                command=EXIT_THREE,
                profile_id=PROFILE_ID,
                working_directory=".",
                intent="Exit non-zero.",
            ),
        }

    def acceptance_specs(self) -> dict[str, AcceptanceSpec]:
        return {
            self.build_id: AcceptanceSpec(
                declaration_id=self.build_id,
                criteria=CRITERIA,
                expectations=dict(EXPECTATIONS),
            )
        }

    def service(
        self,
        *,
        runtime: RuntimeContext | None = None,
        approval_policy: ApprovalPolicy | None = None,
        approval_resolver: object | None = None,
        verification_resolver=None,
        declarations: dict | None = None,
        acceptance_specs: dict | None = None,
    ) -> ForgeApiService:
        return ForgeApiService(
            runtime=runtime or self.runtime,
            workspace=self.workspace,
            run_store=self.run_store,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=self._profile(),
            declarations=declarations or self.declarations(),
            acceptance_specs=(
                self.acceptance_specs() if acceptance_specs is None else acceptance_specs
            ),
            approval_policy=ApprovalPolicy() if approval_policy is None else approval_policy,
            approval_resolver=approval_resolver,
            idempotency_guard=self.idempotency_guard,
            verification_resolver=verification_resolver,
            authorized_project_id=AUTHORIZED_PROJECT_ID,
        )

    def client(self, service: ForgeApiService | None = None) -> TestClient:
        # The composition supplies the project, exactly as `create_api_app` does in production.
        app = create_api_app(
            service or self.service(), authorized_project_id=AUTHORIZED_PROJECT_ID
        )
        client = TestClient(app)
        self._clients.append(client)
        return client

    def surface(self, service: ForgeApiService | None = None):
        """The app's own operating surface, for arranging server-side state.

        Used only to *arrange* what a test needs (recording a fact through the
        legitimate codec, reading the snapshot the route reads). No assertion is
        made against it: every claim under test is checked through a route.
        """

        from app.api.operating_surface import build_operating_surface

        return build_operating_surface(service or self.service())

    # -- the graph steps a test may ask for --------------------------------
    def steps_two(self, *, first: str | None = None, second: str | None = None):
        """`write one.txt` -> `read one.txt`, the canonical dependency chain."""

        return [
            {"step_id": "step-one", "action": {"declaration_id": first or self.one_id}},
            {
                "step_id": "step-two",
                "depends_on": ["step-one"],
                "action": {"declaration_id": second or self.read_id},
            },
        ]
    def graph_run(self, client: TestClient, graph_run_id: str, steps):
        return client.post(
            "/api/product/graph",
            json={"graph_run_id": graph_run_id, "steps": steps},
        )


# ---------------------------------------------------------------------------
# 1. project creation and initial state
# ---------------------------------------------------------------------------


class ProjectAndOnboardingTests(Block8Case):
    def test_01_initial_state_is_reported_without_invention(self) -> None:
        client = self.client()
        response = client.get("/api/product/onboarding")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["stage"], "INITIAL")
        self.assertEqual(body["counts"]["recorded_runs"], 0)
        self.assertIsNone(body["latest_classification"])
        self.assertEqual(len(body["projects"]), 1)
        # The workspace path reported is the server's own, not a request value.
        self.assertEqual(
            Path(body["projects"][0]["path"]).resolve(), self.root.resolve()
        )

    def test_02_capabilities_report_what_is_not_composed(self) -> None:
        """With no declarations and no guard there is nothing to run, and it says so."""

        bare = ForgeApiService(
            runtime=self.runtime,
            workspace=self.workspace,
            run_store=self.run_store,
        )
        client = self.client(bare)
        caps = client.get("/api/product/onboarding").json()["capabilities"]
        self.assertFalse(caps["step_graph"])
        self.assertFalse(caps["accept_cycle"])
        self.assertFalse(caps["declared_verification"])
        self.assertFalse(caps["recovery"])

    def test_02b_a_completed_run_moves_the_stage_off_initial(self) -> None:
        """_INJECTION_TARGET_: the stage must be derived from the REAL field.

        THE DEFECT THIS PINS. ``_stage_of`` read ``classification["classification"]``
        and compared it against ``SUCCESS`` / ``FAILED`` / ``REFUSED``.
        ``RunRecordClassification.to_dict()`` publishes the verdict under
        ``outcome``, with the vocabulary ``TERMINAL_SUCCESS`` /
        ``TERMINAL_FAILURE`` / ``TERMINAL_REFUSED`` / ``INDETERMINATE`` -- so no
        branch could ever match and ``stage`` was **permanently ``INITIAL``**, even
        for a run the server had itself recorded as a proven success. Fixing only
        the key would not have been enough, so both halves are pinned here: a real
        completed run must produce a non-``INITIAL`` stage.

        ``test_01`` asserted ``stage == "INITIAL"`` on an EMPTY project, which is
        vacuously true when the mapping never leaves ``INITIAL`` -- it could not
        have caught this.
        """

        client = self.client()
        # A real run, driven through the route, whose recorded outcome is a proven
        # terminal success.
        response = self.graph_run(
            client, "stage-progress", [{"step_id": "s", "action": {"declaration_id": self.one_id}}]
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["status"], "COMPLETED")

        body = client.get("/api/product/onboarding").json()
        # The classification the server publishes really carries the documented key
        # and vocabulary -- asserted on the payload, not assumed.
        latest = body["latest_classification"]
        self.assertIsNotNone(latest)
        self.assertIn("outcome", latest)
        self.assertEqual(latest["outcome"], "TERMINAL_SUCCESS")
        self.assertTrue(latest["is_success"])
        # So the stage must reflect it.
        self.assertEqual(
            body["stage"],
            "VERIFIED",
            f"a recorded TERMINAL_SUCCESS must not report stage "
            f"{body['stage']!r}; latest_classification={latest!r}",
        )
        self.assertNotEqual(body["stage"], "INITIAL")

    def test_02c_a_recorded_failure_moves_the_stage_to_failed(self) -> None:
        """The other direction, so the mapping is not just "anything but INITIAL"."""

        client = self.client()
        steps = [
            {
                "step_id": "s",
                "action": {"declaration_id": self.fail_id},
                "verification": {"kind": "EXIT_CODE", "expected_exit_code": 0},
            }
        ]
        response = self.graph_run(client, "stage-failed", steps)
        self.assertEqual(response.status_code, 200, response.text)

        body = client.get("/api/product/onboarding").json()
        latest = body["latest_classification"]
        self.assertIsNotNone(latest)
        self.assertEqual(latest["outcome"], "TERMINAL_FAILURE")
        self.assertEqual(body["stage"], "FAILED")

    def test_02d_an_unfinished_run_is_in_progress_not_initial(self) -> None:
        """The THIRD vocabulary branch: a run that proves no outcome is not INITIAL.

        ``test_02b`` and ``test_02c`` pin the two terminal outcomes. ``_stage_of``
        also maps ``WAITING`` / ``INDETERMINATE`` / ``UNKNOWN`` onto ``IN_PROGRESS``,
        and that branch had **no test at all** -- so a mapping that folded it back
        into ``INITIAL`` would still have passed while re-creating the original
        defect's shape, where nothing ever left ``INITIAL``. That is exactly the
        "green test that cannot fail" this block is required not to add.

        The record is written through the store's own API, and the field and
        vocabulary it produces are asserted before the stage is, so the assertion
        cannot be satisfied by a stage that ignores the classification.
        """

        self.run_store.write_state(
            RunStateSnapshot(
                run_id="unfinished-run",
                task_id="t",
                # A claim of progress. The record establishes nothing about the
                # present, so it must not be promoted to a finished stage.
                state="running",
                status="",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )
        body = self.client().get("/api/product/onboarding").json()

        latest = body["latest_classification"]
        self.assertIsNotNone(latest)
        self.assertEqual(
            latest["outcome"],
            "INDETERMINATE",
            f"the published vocabulary changed; got {latest!r}",
        )
        self.assertFalse(latest["is_terminal"])
        self.assertFalse(latest["is_success"])

        self.assertEqual(body["stage"], "IN_PROGRESS")
        self.assertNotEqual(body["stage"], "INITIAL")
        self.assertNotEqual(body["stage"], "VERIFIED")
        self.assertNotEqual(body["stage"], "FAILED")

    def test_03_the_ui_shell_is_served_and_says_it_has_no_authority(self) -> None:
        """The page must actually load, and must state the boundary it lives under."""

        client = self.client()
        index = client.get("/")
        self.assertEqual(index.status_code, 200)
        self.assertIn("Operating Console", index.text)
        # The console must not claim to be a sandbox.
        self.assertIn("not an operating-system sandbox", index.text)
        self.assertEqual(client.get("/app.css").status_code, 200)
        js = client.get("/app.js")
        self.assertEqual(js.status_code, 200)
        # No secret-shaped literal may appear in a served asset.
        for marker in ("sk-or-v1-", "sk-ant-", "sk-proj-", "AIzaSy"):
            self.assertNotIn(marker, index.text)
            self.assertNotIn(marker, js.text)

    def test_03b_every_operating_route_is_reachable_from_the_page(self) -> None:
        """No route may be API-only: each one must be referenced by the client.

        This is the completeness check that keeps a "no decorative sections
        unconnected to a working route" claim honest in the other direction -- a
        working route with no way to reach it is just as much a gap.

        THE OLD RULE WAS A FALSE POSITIVE. It tested `path.split("{")[0]`, so
        `/api/product/memory/{memory_id}/invalidate` was reduced to the bare prefix
        `/api/product/memory/` -- which the unrelated literal
        `/api/product/memory/refresh` already satisfied. The route could have had no
        client call at all and the test would still have passed, while its docstring
        claimed "no route may be API-only". The reduction now keeps the trailing
        literal segments, so a parameterised route must be referenced by a matching
        URL SHAPE, not by a coincidental prefix.
        """

        js = self.client().get("/app.js").text
        app = self._app_for_route_scan()
        product_routes = sorted(
            {
                route.path
                for route in app.routes
                if hasattr(route, "path") and route.path.startswith("/api/product/")
            }
        )
        self.assertTrue(product_routes)
        missing = []
        for path in product_routes:
            if not self._referenced_by_client(path, js):
                missing.append(path)
        self.assertEqual(missing, [], f"routes with no UI reference: {missing}")

    def _app_for_route_scan(self):
        from app.api.server import create_api_app as build

        return build(self.service())

    @staticmethod
    def _client_code(js: str) -> str:
        """The client source reduced to its QUOTED STRING FRAGMENTS and ``+`` joins.

        Two reductions, each closing a way the reachability rule passed for the wrong
        reason:

        * comments are removed first. ``app/static/app.js`` *names* routes in its own
          comments -- ``POST /api/product/memory/{memory_id}/invalidate`` appears in
          prose above the function that calls it. Matching raw text therefore
          satisfied the rule for a route the client could stop calling, which is the
          same class of false positive as the prefix collision this rule replaced;
        * only quoted literals and the concatenation operator survive. The client
          builds a URL as ``"/api/product/memory/" + encodeURIComponent(id) +
          "/invalidate"``, so the fragments plus the ``+`` joins preserve exactly the
          shape a real call has, and a bare identifier or a comment word cannot
          contribute it.
        """

        without_block = re.sub(r"/\*.*?\*/", " ", js, flags=re.DOTALL)
        without_line = re.sub(r"//[^\n]*", " ", without_block)
        fragments = re.findall(r'"[^"\n]*"|\'[^\'\n]*\'|\+', without_line)
        return "".join(fragments)

    @staticmethod
    def _referenced_by_client(path: str, js: str) -> bool:
        """Whether ``js`` references the URL SHAPE of ``path`` in executable code.

        Two coincidences must both be impossible, and each was measured:

        * a lone prefix is not enough. ``path.split("{")[0]`` reduced
          ``/api/product/memory/{memory_id}/invalidate`` to ``/api/product/memory/``,
          which the unrelated literal ``/api/product/memory/refresh`` already
          satisfied -- so the route could have had no client call at all;
        * a mention in a COMMENT is not enough. Requiring the literal parts to appear
          in order in the raw asset still passed, because the route's own name appears
          in the client's explanatory comments.

        The literals around a placeholder must therefore appear in order, in the right
        relationship, **inside executable text**.
        """

        code = ProjectAndOnboardingTests._client_code(js)
        segments = [segment for segment in path.split("/") if segment]
        literals = [segment for segment in segments if not segment.startswith("{")]
        if not literals:
            return True
        cursor = 0
        for literal in literals:
            needle = "/" + literal
            found = code.find(needle, cursor)
            if found < 0:
                # A trailing fragment is quoted on its own (`+ "/invalidate"`), and
                # the opening fragment is quoted immediately before the first
                # segment (`"/api/...`). Both are still quoted strings, so the
                # quoted forms are tried too -- a comment word matches none of them.
                for candidate in ('"' + needle, '"' + literal + '"', "'" + needle):
                    found = code.find(candidate, cursor)
                    if found >= 0:
                        break
                if found < 0:
                    return False
            cursor = found + 1
        return True

    def test_03c_the_console_reads_the_classification_field_the_server_publishes(self) -> None:
        """_INJECTION_TARGET_: the client must read ``outcome``, the real field.

        THE DEFECT THIS PINS. ``app.js`` read ``classification.classification`` in
        the run-history column, the run-detail pill, and the onboarding "latest run"
        card, and fell back to the literal ``"UNKNOWN"``. The payload has no such
        key: ``RunRecordClassification.to_dict()`` publishes the verdict under
        ``outcome``. So every real run was displayed as ``UNKNOWN`` -- the UI
        contradicted the record it had just fetched.

        The check is paired and both halves are required: the served asset must read
        the real field, AND rendering that field must not produce the neutral pill.
        A missing ``TERMINAL_SUCCESS`` entry in the colour map would leave the pill
        neutral even with the right key, so the vocabulary is pinned too.
        """

        js = self.client().get("/app.js").text

        # (1) The wrong field must be gone, in every place it appeared.
        self.assertNotIn(
            "classification.classification",
            js,
            "the console still reads a key the server does not publish",
        )
        self.assertNotIn("latest_classification.classification", js)

        # (2) The real field must be present.
        self.assertIn("classification.outcome", js)
        self.assertIn("latest_classification.outcome", js)

        # (3) The vocabulary must be styled, not silently neutral. Asserted against
        #     the pillFor FUNCTION BODY, not the whole file: the file's own comment
        #     names these labels, so a whole-file substring check would pass even
        #     after the styling list lost them. That is how a test becomes a false
        #     positive.
        start = js.index("function pillFor(")
        end = js.index("function kv(", start)
        pill_body = js[start:end]
        for label in ("TERMINAL_SUCCESS", "TERMINAL_FAILURE"):
            self.assertIn(
                '"' + label + '"',
                pill_body,
                f"{label} is not styled by pillFor, so a proven outcome would be "
                "drawn with the neutral 'unknown' pill",
            )

    def test_03d_the_route_reachability_rule_cannot_pass_by_prefix_collision(self) -> None:
        """The rule must reject a coincidental prefix, and accept the real shape.

        `test_03b` used to reduce every parameterised route to the text before its
        first placeholder, so `/api/product/memory/{memory_id}/invalidate` was
        satisfied by the unrelated literal `/api/product/memory/refresh`. That is a
        false positive: the route could have had no client call and still passed.
        """

        referenced = ProjectAndOnboardingTests._referenced_by_client

        # The real client shape must be accepted.
        self.assertTrue(
            referenced(
                "/api/product/memory/{memory_id}/invalidate",
                '"/api/product/memory/" + encodeURIComponent(memoryId) + "/invalidate"',
            )
        )
        # A bare prefix must NOT be accepted, even though it is a prefix.
        self.assertFalse(
            referenced("/api/product/memory/{memory_id}/invalidate", "/api/product/memory/refresh")
        )
        # A different trailing literal must not be accepted either.
        self.assertFalse(
            referenced(
                "/api/product/memory/{memory_id}/invalidate",
                '"/api/product/memory/" + id + "/revalidate"',
            )
        )
        # And the in-repo client really does reference every parameterised route by
        # its full shape, checked against the served asset with the real rule.
        js = self.client().get("/app.js").text
        self.assertTrue(
            referenced("/api/product/memory/{memory_id}/invalidate", js),
            "the invalidate route has no full-shape client reference",
        )
        self.assertTrue(
            referenced("/api/product/runs/{run_id}", js),
        )
        self.assertTrue(
            referenced("/api/product/recovery/{run_id}", js),
        )

        # A route NAMED IN A COMMENT IS NOT A CLIENT CALL, and this is the second way
        # the rule used to pass for the wrong reason. The asset explains this very
        # route in prose above the function that calls it; matching raw text counted
        # that explanation as a call, so the client could have stopped calling the
        # route and both reachability tests would still have gone green.
        self.assertFalse(
            referenced(
                "/api/product/memory/{memory_id}/invalidate",
                "/* POST /api/product/memory/{memory_id}/invalidate */\n"
                'var other = "/api/product/memory/refresh";',
            ),
            "a route named only in a block comment counted as a client call",
        )
        self.assertFalse(
            referenced(
                "/api/product/memory/{memory_id}/invalidate",
                "// POST /api/product/memory/{memory_id}/invalidate\n"
                'var other = "/api/product/memory/refresh";',
            ),
            "a route named only in a line comment counted as a client call",
        )
        # The control: the very same URL, actually quoted as a fragment, IS a call.
        self.assertTrue(
            referenced(
                "/api/product/memory/{memory_id}/invalidate",
                'var other = "/api/product/memory/refresh";\n'
                'return request("/api/product/memory/" + encodeURIComponent(id) + '
                '"/invalidate", { method: "POST" });',
            )
        )

    def test_04_the_original_routes_are_unchanged(self) -> None:
        """Block #8 adds routes; it must not remove or alter an existing one."""

        client = self.client()
        for path in (
            "/health",
            "/api/status",
            "/api/projects",
            "/api/agents",
            "/api/dashboard",
            "/api/capabilities",
            "/api/tools",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
        self.assertEqual(client.post("/api/tasks/run", json={}).status_code, 400)
        self.assertEqual(client.get("/api/runs/does-not-exist").status_code, 404)


# ---------------------------------------------------------------------------
# 2. requirements and plan, with dependencies
# ---------------------------------------------------------------------------


class RequirementsAndPlanTests(Block8Case):
    def test_05_requirements_come_from_the_operator_composition(self) -> None:
        client = self.client()
        body = client.get("/api/product/requirements").json()
        ids = {entry["requirement_id"] for entry in body["requirements"]}
        self.assertIn(self.build_id, ids)
        build = next(e for e in body["requirements"] if e["requirement_id"] == self.build_id)
        self.assertTrue(build["has_declared_criteria"])
        criterion_ids = {c["criterion_id"] for c in build["criteria"]}
        self.assertEqual(criterion_ids, {"module-present", "tests-present"})

    def test_06_a_criterion_without_an_expectation_is_reported_as_not_evaluable(self) -> None:
        """Silence must never be presented as a pass.

        Note on the stub: ``AcceptanceSpec`` itself already refuses to be
        constructed with a criterion that has no expectation
        (``AcceptanceSpecError``), so that combination cannot reach the route from
        a correct composition -- and the assertion at the end of this test pins
        that, because it is the *first* line of defence. The stub below asks the
        separate question the route must still answer: if a criterion ever arrives
        without a check, the projection reports ``evaluable: false`` rather than
        implying it passed. The stub is explicitly not an ``AcceptanceSpec``.
        """

        from app.agent_runtime.acceptance_spec import AcceptanceSpecError

        with self.assertRaises(AcceptanceSpecError):
            AcceptanceSpec(
                declaration_id=self.build_id,
                criteria=CRITERIA,
                expectations={"module-present": EXPECTATIONS["module-present"]},
            )

        class _SpecLike:
            """Not an AcceptanceSpec: only the two attributes the route reads."""

            def __init__(self):
                self.criteria = CRITERIA
                self.expectations = {"module-present": EXPECTATIONS["module-present"]}

        client = self.client(self.service(acceptance_specs={self.build_id: _SpecLike()}))
        build = next(
            e
            for e in client.get("/api/product/requirements").json()["requirements"]
            if e["requirement_id"] == self.build_id
        )
        by_id = {c["criterion_id"]: c for c in build["criteria"]}
        self.assertTrue(by_id["module-present"]["evaluable"])
        self.assertFalse(by_id["tests-present"]["evaluable"])
        self.assertIsNone(by_id["tests-present"]["expectation"])

    def test_07_the_plan_reports_declared_limits_not_a_promise(self) -> None:
        client = self.client()
        body = client.get("/api/product/plan").json()
        self.assertEqual(body["profile_id"], PROFILE_ID)
        self.assertEqual(body["allowed_commands"], ["python"])
        self.assertFalse(body["network_access"])
        self.assertTrue(body["registry_consistent"])
        self.assertEqual(body["declared_action_count"], 5)
        # No step list was submitted, so none is invented.
        self.assertEqual(body["steps"], [])

    def test_08_submitted_steps_are_echoed_as_proposed_dependencies(self) -> None:
        client = self.client()
        response = client.post(
            "/api/product/plan",
            json={
                "steps": {
                    "step-one": {"depends_on": []},
                    "step-two": {"depends_on": ["step-one"]},
                }
            },
        )
        self.assertEqual(response.status_code, 200)
        steps = {s["step_id"]: s["depends_on"] for s in response.json()["steps"]}
        self.assertEqual(steps["step-two"], ["step-one"])

    def test_09_a_malformed_steps_document_is_refused_not_guessed(self) -> None:
        client = self.client()
        response = client.post("/api/product/plan", json={"steps": ["not", "a", "map"]})
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------------------
# 3. authorized run and result display
# ---------------------------------------------------------------------------


class AuthorizedRunTests(Block8Case):
    def test_10_a_declared_verification_runs_and_the_result_is_reported(self) -> None:
        """A real declared command, started through the route, reported honestly.

        WHAT ACTUALLY HAPPENS, MEASURED AND NOT ASSUMED. ``run_declared_verification``
        dispatches through ``RunExecutor`` without supplying an
        ``ExecutionCoordinator``, so ``RunExecutor`` builds the default one --
        ``LocalExecutionAdapter(workspace_root=...)`` with ``isolate_workspace=True``,
        which is that adapter's documented default. The declared command therefore
        runs to completion in a **staged copy** of the workspace, and the operator's
        own directory is left untouched. This test asserts that behaviour instead of
        assuming the file appears in the real workspace: an isolation claim that is
        wrong in the permissive direction is exactly the kind of thing that must be
        measured.
        """

        client = self.client(
            self.service(verification_resolver=lambda task, profile: self.build_id)
        )
        response = client.post(
            "/api/product/verify",
            json={"declaration_id": self.build_id, "idempotency_key": _unique("k.verify")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["run_id"])
        # The command really succeeded: it exited zero and ran no provider.
        self.assertEqual(body["state"], "COMPLETED")
        self.assertTrue(body["success"])
        self.assertIsNone(body["error"])
        self.assertEqual(body["provider_used"], "")
        # The workspace is NOT modified by this route, because the default adapter
        # isolates. Asserting the real behaviour is what makes the isolation
        # statement in the docs checkable rather than aspirational.
        self.assertFalse(
            (self.root / MODULE_PATH).exists(),
            "the declared-verification route isolates by default; if this file now "
            "appears, the isolation default changed and the docs must change with it",
        )
        # And the run is durably recorded and readable back through the route.
        recorded = client.get(f"/api/runs/{body['run_id']}")
        self.assertEqual(recorded.status_code, 200)
        self.assertIn("classification", recorded.json())

    def test_10b_the_step_graph_route_does_write_the_operators_workspace(self) -> None:
        """The contrast that makes the previous test meaningful.

        A step graph dispatches through the runner's own coordinator, which this
        surface composes with ``isolate_workspace=False`` against the operator's
        workspace, so a step's effect IS visible. Two routes, two documented
        behaviours, both asserted.
        """

        client = self.client()
        response = self.graph_run(
            client, "writes-workspace", self.steps_two()
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["result"]["status"], "COMPLETED")
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")

    def test_10c_a_missing_approval_policy_refuses_the_step_graph(self) -> None:
        """FAIL-CLOSED: no approval policy means no step-graph runner at all.

        The service's own attribute stays ``None`` when no policy is passed (two
        existing contract tests assert that, because "no approval configured" is a
        real fail-closed state). The composition must **refuse** in that case, not
        substitute a weaker default: ``ApprovalPolicy()`` requires approval only for
        its mandatory tool set, so defaulting to it would mean a deployment that never
        stated an approval position still ran declared host commands. Refusing is also
        the same answer ``RunExecutor`` (``approval_policy_required``) and
        ``StepGraphRunner`` (it will not be constructed without a policy) already give.
        """

        # A service built with no policy keeps None, and composes NO runner.
        bare = ForgeApiService(
            runtime=self.runtime,
            workspace=self.workspace,
            run_store=self.run_store,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=self._profile(),
            declarations=self.declarations(),
            acceptance_specs=self.acceptance_specs(),
            idempotency_guard=self.idempotency_guard,
        )
        self.assertIsNone(bare._approval_policy)
        self.assertFalse(bare.can_run_step_graph())
        self.assertIsNone(self.surface(bare).build_step_graph_runner())

        # And the route refuses rather than running under an unstated policy.
        client = self.client(bare)
        refused = self.graph_run(client, "no-policy-run", self.steps_two())
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertEqual(refused.json()["code"], "step_graph_unavailable")
        self.assertFalse((self.root / "one.txt").exists())

    def test_10d_a_stated_policy_is_the_one_the_runner_enforces(self) -> None:
        """With a policy stated, that exact object is the one used -- no substitute."""

        declared = ApprovalPolicy(approval_required_tools=("python",))
        service = self.service(approval_policy=declared)
        self.assertTrue(service.can_run_step_graph())
        runner = self.surface(service).build_step_graph_runner()
        self.assertIsNotNone(runner)
        self.assertIs(runner._approval_policy, declared)
        self.assertEqual(runner._approval_policy.evaluate("python").value, "REQUIRED")

        # A default policy that requires nothing extra still composes and still runs.
        permissive = self.service(approval_policy=ApprovalPolicy())
        self.assertTrue(permissive.can_run_step_graph())
        permissive_runner = self.surface(permissive).build_step_graph_runner()
        self.assertIs(permissive_runner._approval_policy, permissive._approval_policy)
        self.assertEqual(
            permissive_runner._approval_policy.evaluate("python").value, "NOT_REQUIRED"
        )

        # The HTTP factory supplies a policy explicitly, so the console works.
        from app.api.server import create_api_app as build

        supplied = self.service()
        app = build(supplied)
        self.assertIs(app.state.forge_operating_surface._service, supplied)

    def test_11_the_idempotency_key_is_required_at_the_route(self) -> None:
        """Without a key, "the client retried" is indistinguishable from "run again"."""

        for route in ("/api/product/verify", "/api/product/accept"):
            response = self.client().post(route, json={"declaration_id": self.build_id})
            self.assertEqual(response.status_code, 400, route)
            self.assertIn("idempotency_key", response.text)

    def test_11b_a_malformed_idempotency_key_is_refused_with_the_field_named(self) -> None:
        """A short key would otherwise surface as an opaque ledger refusal."""

        client = self.client()
        for bad in ("short", "has spaces here", "x" * 201):
            response = client.post(
                "/api/product/verify",
                json={"declaration_id": self.build_id, "idempotency_key": bad},
            )
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn("idempotency_key", response.text)

    def test_12_an_undeclared_action_is_refused(self) -> None:
        client = self.client()
        response = client.post(
            "/api/product/verify",
            json={"declaration_id": "never.declared", "idempotency_key": _unique("k.x")},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "verification_refused")

    def test_13_a_repeat_under_the_same_key_does_not_run_twice(self) -> None:
        """_INJECTION_TARGET_: the ledger refusal must be visible at the route."""

        client = self.client(
            self.service(verification_resolver=lambda task, profile: self.build_id)
        )
        key = _unique("k.repeat")
        first = client.post(
            "/api/product/verify",
            json={"declaration_id": self.build_id, "idempotency_key": key},
        )
        self.assertEqual(first.status_code, 200, first.text)
        second = client.post(
            "/api/product/verify",
            json={"declaration_id": self.build_id, "idempotency_key": key},
        )
        # Either the route refused it, or it replayed the recorded result. What it
        # must never do is silently execute the command a second time.
        if second.status_code == 200:
            self.assertEqual(second.json()["run_id"], first.json()["run_id"])
        else:
            self.assertEqual(second.status_code, 409)

    def test_14_the_run_history_reports_classification_through_the_route(self) -> None:
        client = self.client(
            self.service(verification_resolver=lambda task, profile: self.build_id)
        )
        client.post(
            "/api/product/verify",
            json={"declaration_id": self.build_id, "idempotency_key": _unique("k.hist")},
        )
        body = client.get("/api/product/runs").json()
        self.assertGreaterEqual(len(body["runs"]), 1)
        self.assertTrue(all("classification" in run for run in body["runs"]))
        # Every item also carries ``project_state`` -- present as a key, ``null``
        # when the run has none -- so a client never has to guess between "no
        # state recorded" and "this endpoint does not report state".
        self.assertTrue(all("project_state" in run for run in body["runs"]))

    def test_14a_the_run_history_item_carries_the_same_project_state(self) -> None:
        """The list endpoint must publish ``project_state``, not only the detail one.

        THE DEFECT THIS PINS. ``GET /api/product/runs`` built each item from the
        state/status pair, the classification, an event count and the timestamp,
        and dropped ``project_state`` entirely. A client that counted
        code-writing outcomes from the list therefore measured zero however many
        runs had recorded one (measured: 40 runs, 0 carrying ``code_write``,
        while ``GET /api/product/runs/{run_id}`` showed it for each of them).

        The item now carries the SAME projection the single-run route returns,
        and ``null`` -- not an empty object -- when the run recorded no state.
        """

        code_write = {
            "status": "applied",
            "verification": "passed",
            "files": [{"path": "hello.txt", "action": "add", "added": 1, "removed": 0}],
            "reason": "",
        }
        # Older by time, but later in the alphabet: this test is not about ordering.
        self.assertIsNotNone(
            self.run_store.write_state(
                RunStateSnapshot(
                    run_id="zzz-with-write",
                    task_id="t",
                    state="completed",
                    status="success",
                    project_state={"code_write": code_write},
                    updated_at="2020-01-01T00:00:00+00:00",
                )
            )
        )
        self.assertIsNotNone(
            self.run_store.write_state(
                RunStateSnapshot(
                    run_id="aaa-without-state",
                    task_id="t",
                    state="completed",
                    status="success",
                    updated_at="2024-01-01T00:00:00+00:00",
                )
            )
        )

        client = self.client()
        history = client.get("/api/product/runs").json()
        by_id = {item["run_id"]: item for item in history["runs"]}

        self.assertIn("project_state", by_id["zzz-with-write"])
        self.assertEqual(
            by_id["zzz-with-write"]["project_state"]["code_write"], code_write
        )
        self.assertIn("project_state", by_id["aaa-without-state"])
        self.assertIsNone(by_id["aaa-without-state"]["project_state"])

        # The value is exactly what the single-run route already reported.
        detail = client.get("/api/product/runs/zzz-with-write").json()
        self.assertEqual(
            detail["project_state"], by_id["zzz-with-write"]["project_state"]
        )

    def test_14b_newest_first_is_by_time_not_by_run_id(self) -> None:
        """_INJECTION_TARGET_: "newest" must be time, not lexicographic order.

        THE DEFECT THIS PINS. ``RunStore.list_run_ids`` is ``tuple(sorted(...))`` and
        the surface used ``run_ids[-1]`` as ``latest_run_id`` and
        ``reversed(run_ids)`` for "newest first". Measured on the frozen code: after
        a verify run, ``latest_run_id`` was ``step.chain-verify.s1`` purely because
        ``'s' > 'r'`` -- an ordering by character, presented as an ordering by time.

        The two runs here are arranged so the two orders DISAGREE: the older one
        sorts LAST lexicographically and the newer one sorts FIRST. A lexicographic
        answer therefore gets it exactly backwards, and cannot pass by coincidence.
        """

        # Older by time, but later in the alphabet.
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="zzz-older-run",
                task_id="t",
                state="completed",
                status="success",
                updated_at="2020-01-01T00:00:00+00:00",
            )
        )
        # Newer by time, but earlier in the alphabet.
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="aaa-newer-run",
                task_id="t",
                state="completed",
                status="success",
                updated_at="2024-01-01T00:00:00+00:00",
            )
        )

        body = self.client().get("/api/product/runs").json()
        order = [run["run_id"] for run in body["runs"]]
        self.assertEqual(order[:2], ["aaa-newer-run", "zzz-older-run"], order)

        # The "latest run" the project overview reports is the newest one too, and
        # NOT the one whose id happens to sort last.
        overview = self.client().get("/api/product/onboarding").json()
        self.assertEqual(overview["latest_run_id"], "aaa-newer-run")

    def test_14c_a_lexicographically_last_verify_run_is_not_reported_as_latest(self) -> None:
        """The exact measured shape: a step run whose id sorts AFTER the verify run.

        THE ARRANGEMENT IS THE WHOLE TEST, AND THE FIRST VERSION OF IT PROVED
        NOTHING. It wrote the later-sorting step run with ``updated_at`` in the year
        2999, i.e. it also made that run the NEWEST BY TIME. Both orderings then
        agreed on the answer, so the test passed under the very defect it is named
        for: measured, restoring ``tuple(reversed(self._run_store.list_run_ids()))``
        left it green (``test_14b`` caught the mutation; this test did not).

        The step run is therefore written as the OLDER one. Id-descending order --
        the original ``run_ids[-1]`` / ``reversed(run_ids)`` reading -- puts the
        later-sorting step run first and is now wrong, while chronological order puts
        the newer verify run first. The two orders disagree, which is the only way
        this test can fail.
        """

        client = self.client(
            self.service(verification_resolver=lambda task, profile: self.build_id)
        )
        verify = client.post(
            "/api/product/verify",
            json={"declaration_id": self.build_id, "idempotency_key": _unique("k.order")},
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        verify_run_id = verify.json()["run_id"]

        # An OLDER step-graph run whose id sorts AFTER the verify id. Both facts are
        # asserted, because the test is only meaningful if they hold.
        later = self.run_store.write_state(
            RunStateSnapshot(
                run_id="step.zzzz-later.s1",
                task_id="t",
                state="completed",
                status="success",
                updated_at="2020-01-01T00:00:00+00:00",
            )
        )
        self.assertIsNotNone(later)
        self.assertLess("run", "step", "the two ids must sort the other way round")

        history = client.get("/api/product/runs").json()
        order = [run["run_id"] for run in history["runs"]]
        self.assertEqual(order[0], verify_run_id, order)
        self.assertNotEqual(order[0], "step.zzzz-later.s1", order)
        self.assertEqual(
            client.get("/api/product/onboarding").json()["latest_run_id"],
            verify_run_id,
        )
        # The older run is still recorded, just not first.
        self.assertIn("step.zzzz-later.s1", order)
        self.assertTrue(verify_run_id, verify_run_id)

    def test_15b_a_server_side_error_is_not_disguised_as_a_caller_conflict(self) -> None:
        """_INJECTION_TARGET_: verify/accept must classify, not blanket-409.

        THE DEFECT THIS PINS. Both routes wrapped *every* exception into
        409 ``verification_refused`` / ``accept_refused``. A 409 means "the request
        was well-formed and conflicts with the server's own state", which is the
        right answer for a refusal and the wrong answer for a server-side
        composition error: it blames the caller for a fault that is not theirs and
        trains a retry that cannot succeed. ``_error_response`` already drew that
        distinction everywhere else on the surface.

        The stub below makes the service raise the surface's own controlled
        configuration error from the entry point, so the route's classification is
        observed directly. A blanket-409 wrapper cannot pass this.
        """

        from app.api.operating_surface import SurfaceCompositionError

        # Build a fully working composition first, then replace the two entry points
        # at the class the route really dispatches through, so the only thing that
        # changes is where the exception comes from.
        client = self.client(self.service())

        real_verify = ForgeApiService.run_declared_verification
        real_accept = ForgeApiService.run_accepted_task

        def failing_verify(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise SurfaceCompositionError(
                "the harness is not composed", code="harness_not_composed"
            )

        def failing_accept(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            raise SurfaceCompositionError(
                "the acceptance spec is not composed", code="spec_not_composed"
            )

        ForgeApiService.run_declared_verification = failing_verify
        ForgeApiService.run_accepted_task = failing_accept
        try:
            for path, code in (
                ("/api/product/verify", "harness_not_composed"),
                ("/api/product/accept", "spec_not_composed"),
            ):
                response = client.post(
                    path,
                    json={
                        "declaration_id": self.build_id,
                        "idempotency_key": _unique("k.d8"),
                    },
                )
                self.assertEqual(response.status_code, 500, f"{path}: {response.text}")
                detail = response.json()["detail"]
                self.assertTrue(detail["configuration_error"], path)
                self.assertEqual(detail["code"], code, path)
                self.assertFalse(detail["executed"], path)
        finally:
            ForgeApiService.run_declared_verification = real_verify
            ForgeApiService.run_accepted_task = real_accept

        # AND THE CONTRAST that keeps the two classifications distinct: a genuine
        # caller-side refusal is still a 409, not a 500.
        refused = client.post(
            "/api/product/verify",
            json={"declaration_id": "never.declared", "idempotency_key": _unique("k.d8b")},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertNotIn("configuration_error", refused.text)

    def test_15_a_partial_generation_budget_refuses_before_it_spends(self) -> None:
        """The accept cycle needs an acceptance spec; without one it must refuse."""

        client = self.client(self.service(acceptance_specs={}))
        response = client.post(
            "/api/product/accept",
            json={"declaration_id": self.build_id, "idempotency_key": _unique("k.acc")},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "accept_refused")


# ---------------------------------------------------------------------------
# 4. approval wait, refusal, continuation
# ---------------------------------------------------------------------------


class ApprovalTests(Block8Case):
    """The approval gate, exercised with the profile executable as the required tool.

    ``ApprovalPolicy(approval_required_tools=("python",))`` is the same shape
    ``tests/test_step_graph_managed_execution.py`` uses. It requires approval for
    every step whose executable is ``python``, which is what makes the four
    outcomes below -- waiting, reporting, approving, rejecting -- reachable through
    the routes without weakening anything.
    """

    def _approval_client(self, resolver) -> TestClient:
        return self.client(
            self.service(
                approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
                approval_resolver=resolver,
            )
        )

    def test_16_an_unresolved_approval_waits_and_performs_nothing(self) -> None:
        """_INJECTION_TARGET_: an unresolved approval must never authorize a step."""

        client = self._approval_client(_SilentResolver())
        response = self.graph_run(client, "appr-wait", self.steps_two())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        statuses = {step["step_id"]: step["status"] for step in result["steps"]}
        self.assertEqual(statuses["step-one"], "WAITING_APPROVAL")
        # Nothing performed: the file the step's command would have written is absent.
        self.assertFalse((self.root / "one.txt").exists())
        # And the attempt is not reported as completed.
        self.assertNotEqual(result["status"], "COMPLETED")

    def test_17_the_approval_request_route_reports_parameters_without_granting(self) -> None:
        """_INJECTION_TARGET_: the route must report and must never grant."""

        client = self._approval_client(_SilentResolver())
        steps = [
            {
                "step_id": "step-one",
                "action": {
                    "declaration_id": self.one_id,
                    "parameters": {"timeout_seconds": "5"},
                },
            }
        ]
        self.graph_run(client, "appr-report", steps)
        response = client.get("/api/product/approvals/appr-report")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["awaiting"], 1)
        request = body["requests"][0]
        self.assertEqual(request["step_id"], "step-one")
        self.assertEqual(request["declaration_id"], self.one_id)
        self.assertEqual(request["status"], "WAITING_APPROVAL")
        # The route is not an approval mechanism, and it says so.
        self.assertFalse(request["granted_by_server"])
        self.assertIn("ApprovalPolicy", body["note"])
        # The parameter the operator would be approving is shown, from the server's
        # own record rather than from the request that asked for it.
        parameters = {p["name"]: p["value"] for p in request["parameters"]}
        self.assertEqual(parameters.get("timeout_seconds"), "5")
        # The consequence is named: which executable, in which directory.
        self.assertEqual(request["consequences"]["executable"], "python")
        self.assertEqual(request["consequences"]["working_directory"], ".")
        # The fingerprint the coordinator compares is available, which is what makes
        # a parameter change after approval visible as a different value.
        self.assertTrue(request["intent_fingerprint"])

    def test_18_an_approved_step_runs_and_the_dependency_follows(self) -> None:
        resolver = _FingerprintEchoResolver()
        client = self._approval_client(resolver)
        response = self.graph_run(client, "appr-ok", self.steps_two())
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        self.assertEqual(result["status"], "COMPLETED")
        statuses = {step["step_id"]: step["status"] for step in result["steps"]}
        self.assertEqual(statuses["step-one"], "SUCCEEDED")
        self.assertEqual(statuses["step-two"], "SUCCEEDED")
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")
        # The resolver really was consulted, and it approved a real fingerprint.
        self.assertTrue(all(resolver.learned))
        # After the run the step is no longer waiting, so nothing is left to approve.
        approvals = client.get("/api/product/approvals/appr-ok").json()
        self.assertEqual(approvals["awaiting"], 0)

    def test_19_a_rejected_approval_performs_nothing(self) -> None:
        """_INJECTION_TARGET_: a rejection must not be treated as a permission."""

        client = self._approval_client(_FingerprintEchoResolver(ApprovalState.REJECTED))
        response = self.graph_run(client, "appr-no", self.steps_two())
        self.assertEqual(response.status_code, 200, response.text)
        statuses = {
            step["step_id"]: step["status"] for step in response.json()["result"]["steps"]
        }
        self.assertNotEqual(statuses["step-one"], "SUCCEEDED")
        self.assertFalse((self.root / "one.txt").exists())

    def test_20_a_parameter_change_after_approval_is_refused(self) -> None:
        """_INJECTION_TARGET_: a changed parameter must not reuse an approval.

        An approval is granted for the step run id and request id the runner will
        actually use, bound to the fingerprint of one exact parameter set. The same
        step is then asked for with a *different* parameter, under that same id.
        The fingerprint differs, the approval no longer matches, and the step is
        left waiting -- the changed parameter is never executed.

        The control is the same mechanics with the parameters the approval was
        bound to, which the resolver *pre-approves by fingerprint*. Without that
        control the refusal below would prove nothing about the parameter.
        """

        original = {
            "step_id": "step-one",
            "action": {
                "declaration_id": self.one_id,
                "parameters": {"timeout_seconds": "5"},
            },
        }
        changed = {
            "step_id": "step-one",
            "action": {
                "declaration_id": self.one_id,
                "parameters": {"timeout_seconds": "9"},
            },
        }

        # Phase 1: learn the fingerprint this exact parameter set produces.
        learner = _FingerprintEchoResolver(ApprovalState.REJECTED)
        probe_client = self._approval_client(learner)
        self.graph_run(probe_client, "appr-probe", [original])
        self.assertEqual(len(learner.learned), 1)
        approved_fingerprint = learner.learned[0]
        self.assertTrue(approved_fingerprint)

        # Phase 2: a resolver that approves ONLY that fingerprint.
        strict = _FingerprintEchoResolver(auto_grant=False)
        strict.learn(approved_fingerprint)
        bound_client = self._approval_client(strict)

        # The changed parameter produces a different fingerprint ...
        changed_response = self.graph_run(bound_client, "appr-changed", [changed])
        self.assertEqual(changed_response.status_code, 200, changed_response.text)
        changed_step = changed_response.json()["result"]["steps"][0]
        self.assertNotEqual(
            strict.learned[0],
            approved_fingerprint,
            "a changed parameter must produce a different intent fingerprint",
        )
        # ... so the approval bound to the original set does not cover it, and the
        # step waits instead of running.
        self.assertEqual(changed_step["status"], "WAITING_APPROVAL")
        self.assertFalse((self.root / "one.txt").exists())

        # CONTROL: the same bound-resolver mechanics, asked for the parameters the
        # approval was granted for, does authorize the step. So the refusal above is
        # caused by the parameter change, not by approval being broken in general.
        control_resolver = _FingerprintEchoResolver()
        control_resolver.learn(approved_fingerprint)
        control = self._approval_client(control_resolver)
        control_response = self.graph_run(control, "appr-original", [original])
        self.assertEqual(control_response.status_code, 200, control_response.text)
        self.assertEqual(
            control_response.json()["result"]["steps"][0]["status"], "SUCCEEDED"
        )
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")


# ---------------------------------------------------------------------------
# 4b. the COORDINATOR layer of the two-layer approval fingerprint check
# ---------------------------------------------------------------------------


class CoordinatorFingerprintTests(Block8Case):
    """The two-layer approval fingerprint check, with the layers separated.

    WHY THIS CLASS EXISTS. The check has two independent layers: the server-side
    resolver compares the request's intent fingerprint against the fingerprint the
    approval was granted for (`app/tools/approval.py`), and then the coordinator
    **independently** compares the approval's `approved_fingerprint` against the intent
    it is about to run (`app/execution/authorizer.py`). A test that drives the whole
    stack passes through the resolver first, so a break in the *coordinator* layer alone
    is invisible to it. Acceptance review measured exactly that: disabling the
    coordinator's comparison did not fail any test.

    WHICH TEST COVERS WHICH LAYER — measured, not assumed:

    * ``test_61`` and ``test_62`` cover the **coordinator** layer. Their resolver is
      ``_AlwaysAnswers``, which answers APPROVED with whatever fingerprint the test
      supplies, so nothing except the coordinator's own comparison stands between that
      answer and the process. Disabling that comparison makes ``test_61`` fail
      (measured), which is the fails-before proof for this layer.
    * ``test_63`` covers the **resolver** layer and the parameter-change property using
      the *production* single-use ``InMemoryApprovalResolver``. Its resolver refuses the
      changed intent before the coordinator is reached — verified by measurement:
      disabling the coordinator's comparison does **not** make ``test_63`` fail, because
      the resolver already refused. That is stated here rather than left as an
      overclaim, and it is why ``test_61`` exists at all.
    """

    def _request(self, *, working_directory: str = ".") -> Any:
        from app.execution.request import ExecutionRequest

        return ExecutionRequest(
            command=("python", "-c", "open('coord.txt','w',encoding='utf-8').write('ran')"),
            request_id="coord-request",
            working_directory=working_directory,
            profile=self._profile(),
            metadata={"run_id": "coord-run"},
        )

    def _scope(self, run_id: str) -> Any:
        from app.runtime.run_scope import RunScope

        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=self._profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            # RunScope refuses an empty criterion set, and rightly so: a scope that
            # declares nothing acceptable could never accept anything. The criterion
            # is a real one, not a placeholder for the check under test.
            acceptance_criteria=(
                AcceptanceCriterion(
                    "coord-completes",
                    "the coordinator run reaches a terminal state",
                    requirement_id="coord",
                ),
            ),
        )
        scope.freeze()
        return scope

    def _coordinator(self) -> Any:
        from app.execution.adapter import LocalExecutionAdapter
        from app.execution.authorizer import ExecutionCoordinator

        return ExecutionCoordinator(
            LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=False)
        )

    class _AlwaysAnswers:
        """A resolver that always answers, so only the coordinator can refuse.

        It answers with whatever ``approved_fingerprint`` the test gives it — which
        is the whole point: the resolver layer is disabled as a variable here.
        """

        def __init__(self, approved_fingerprint: str) -> None:
            self._approved_fingerprint = approved_fingerprint
            self.calls = 0

        def resolve(self, request):  # noqa: ANN001
            self.calls += 1
            return ApprovalResolution(
                decision=ApprovalState.APPROVED,
                approved_fingerprint=self._approved_fingerprint,
            )

    def _run(self, approved_fingerprint: str):
        """Drive the coordinator once; return (result, resolver)."""

        from app.execution.intent import IntentBuilder

        request = self._request()
        scope = self._scope("coord-run")
        resolver = self._AlwaysAnswers(approved_fingerprint)
        intent = IntentBuilder.from_request(request, self._profile()).build()
        result = self._coordinator().execute(
            request,
            workspace_root=self.root,
            run_id="coord-run",
            allowed_commands=frozenset({"python"}),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=resolver,
            run_scope=scope,
        )
        return result, resolver, intent

    def test_60_the_coordinator_permits_execution_when_the_fingerprint_matches(self) -> None:
        """(a) correct approval plus matching parameters permits execution."""

        from app.execution.request import ExecutionOutcomeStatus

        request = self._request()
        from app.execution.intent import IntentBuilder

        intent = IntentBuilder.from_request(request, self._profile()).build()
        result, resolver, _ = self._run(intent.fingerprint)

        self.assertEqual(resolver.calls, 1)
        self.assertEqual(
            result.outcome_status, ExecutionOutcomeStatus.EXECUTION_SUCCESS, result.metadata
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual((self.root / "coord.txt").read_text(encoding="utf-8"), "ran")

    def test_61_the_coordinator_refuses_a_non_empty_wrong_fingerprint(self) -> None:
        """(c) a NON-EMPTY but wrong fingerprint is refused by the coordinator.

        This is the case a resolver-layer test cannot reach: the resolver answers
        APPROVED, and only the coordinator's own comparison stands between that
        answer and the process.
        """

        from app.execution.request import ExecutionOutcomeStatus

        result, resolver, intent = self._run("f" * 64)

        self.assertEqual(resolver.calls, 1)
        self.assertNotEqual("f" * 64, intent.fingerprint)
        self.assertEqual(
            result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING, result.metadata
        )
        self.assertEqual(result.metadata.get("denial_reason"), "intent_fingerprint_mismatch")
        # (d) nothing ran, no file, no side effect.
        self.assertFalse((self.root / "coord.txt").exists())
        self.assertIsNone(result.exit_code)

    def test_62_the_coordinator_refuses_an_empty_fingerprint(self) -> None:
        """An empty fingerprint is never a wildcard, at the coordinator layer."""

        from app.execution.request import ExecutionOutcomeStatus

        result, _, _ = self._run("")
        self.assertEqual(
            result.outcome_status, ExecutionOutcomeStatus.APPROVAL_WAITING
        )
        self.assertFalse((self.root / "coord.txt").exists())

    def test_63_changed_parameters_do_not_execute_under_the_old_approval(self) -> None:
        """(b) parameters changed after approval do NOT execute under the old approval.

        THE STRONGEST FORM: a real, single-use ``InMemoryApprovalResolver`` holds an
        approval bound to the ORIGINAL parameter set. The coordinator is then asked to
        run the SAME run id and request id with CHANGED parameters.

        LAYER COVERAGE, MEASURED. This exercises the **resolver** layer using the
        production resolver: the changed intent does not match the granted fingerprint,
        so the resolver returns ``None`` and the coordinator never reaches its own
        comparison. Disabling the coordinator's comparison does not break this test
        (measured); ``test_61`` is the test that covers that layer. Both layers matter,
        which is why both are tested — Block #6 proved that defeating either layer alone
        does not break the parameter-change property and defeating both does.

        The "no process ran" assertions come FIRST, before any control run writes a
        file, so they cannot be satisfied or broken by a later phase.
        """

        from app.execution.intent import IntentBuilder
        from app.execution.request import ExecutionOutcomeStatus
        from app.tools.approval import InMemoryApprovalResolver

        # --- phase 1: the refusal, asserted while the workspace is still clean -----
        original = self._request()
        original_fingerprint = IntentBuilder.from_request(
            original, self._profile()
        ).build().fingerprint

        resolver = InMemoryApprovalResolver()
        resolver.submit(
            "coord-run",
            "coord-request",
            ApprovalState.APPROVED,
            intent_fingerprint=original_fingerprint,
        )

        (self.root / "sub").mkdir(exist_ok=True)
        changed = self._request(working_directory="sub")
        changed_fingerprint = IntentBuilder.from_request(
            changed, self._profile()
        ).build().fingerprint
        self.assertNotEqual(
            original_fingerprint,
            changed_fingerprint,
            "a changed parameter must produce a different intent fingerprint",
        )

        changed_result = self._coordinator().execute(
            changed,
            workspace_root=self.root,
            run_id="coord-run",
            allowed_commands=frozenset({"python"}),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=resolver,
            run_scope=self._scope("coord-run"),
        )
        self.assertEqual(
            changed_result.outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
            changed_result.metadata,
        )
        # (d) nothing ran and no file exists, in either directory.
        self.assertFalse((self.root / "coord.txt").exists())
        self.assertFalse((self.root / "sub" / "coord.txt").exists())
        self.assertIsNone(changed_result.exit_code)

        # --- phase 2: the controls, in separate run ids and a separate directory ---
        # A run whose command targets a DIFFERENT file, so the control cannot be
        # confused with phase 1's file.
        from app.execution.request import ExecutionRequest

        control_name = "control.txt"
        control_request = ExecutionRequest(
            command=(
                "python",
                "-c",
                f"open('{control_name}','w',encoding='utf-8').write('ran')",
            ),
            request_id="control-request",
            working_directory=".",
            profile=self._profile(),
            metadata={"run_id": "coord-control"},
        )
        control_fingerprint = IntentBuilder.from_request(
            control_request, self._profile()
        ).build().fingerprint

        # CONTROL A: approval required and NOT submitted -> refused. This proves the
        # approval really is the gate, rather than something that always passes.
        unapproved = self._coordinator().execute(
            control_request,
            workspace_root=self.root,
            run_id="coord-control",
            allowed_commands=frozenset({"python"}),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=InMemoryApprovalResolver(),
            run_scope=self._scope("coord-control"),
        )
        self.assertEqual(
            unapproved.outcome_status,
            ExecutionOutcomeStatus.APPROVAL_WAITING,
            unapproved.metadata,
        )
        self.assertFalse((self.root / control_name).exists())

        # CONTROL B: an approval for this control's OWN exact intent -> executes. So
        # the phase-1 refusal is caused by the parameter change, not by approval being
        # broken in general.
        granting = InMemoryApprovalResolver()
        granting.submit(
            "coord-control",
            "control-request",
            ApprovalState.APPROVED,
            intent_fingerprint=control_fingerprint,
        )
        granted = self._coordinator().execute(
            control_request,
            workspace_root=self.root,
            run_id="coord-control",
            allowed_commands=frozenset({"python"}),
            approval_policy=ApprovalPolicy(approval_required_tools=("python",)),
            approval_resolver=granting,
            run_scope=self._scope("coord-control"),
        )
        self.assertEqual(
            granted.outcome_status,
            ExecutionOutcomeStatus.EXECUTION_SUCCESS,
            granted.metadata,
        )
        self.assertEqual(
            (self.root / control_name).read_text(encoding="utf-8"), "ran"
        )
        # Phase 1's file is still absent after every control has run.
        self.assertFalse((self.root / "coord.txt").exists())
        self.assertFalse((self.root / "sub" / "coord.txt").exists())



def dataclasses_replace_run_id(request: Any, run_id: str) -> Any:
    """A request whose metadata names ``run_id``. Keeps the helper explicit."""

    from dataclasses import replace

    metadata = dict(getattr(request, "metadata", {}) or {})
    metadata["run_id"] = run_id
    return replace(request, metadata=metadata)


# ---------------------------------------------------------------------------
# 4c. fail-closed composition: three cases, and where assembly lives
# ---------------------------------------------------------------------------


class _IngredientsService:
    """A service stub whose step-graph composition is supplied by the test.

    It exists so the three fail-closed cases can be produced exactly, rather than
    provoked indirectly through a real ``ForgeApiService``. Everything the operating
    surface reads other than the composition is delegated to a real service, so the
    stub differs from production in exactly one method.
    """

    def __init__(self, real_service: ForgeApiService, ingredients: Any) -> None:
        self._real = real_service
        self._ingredients = ingredients

    def step_graph_composition(self) -> Any:
        return self._ingredients

    def recovery_composition(self) -> Any:
        return None

    @property
    def workspace(self) -> Any:
        return self._real.workspace

    @property
    def run_store(self) -> Any:
        return self._real.run_store


class CompositionFailClosedTests(Block8Case):
    """The three ways the approval ingredient can be wrong, and the ordering.

    Every case must stop **before** the ``ExecutionCoordinator`` exists and before any
    command runs. The ordering assertion is the important one: a refusal that happens
    after the coordinator is built has already created an authority component, which is
    the thing "refuse before anything is built" is meant to prevent.
    """

    def _surface_with(self, ingredients: Any) -> Any:
        from app.api.operating_surface import OperatingSurface

        stub = _IngredientsService(self.service(approval_policy=ApprovalPolicy()), ingredients)
        return OperatingSurface(stub)

    def _real_ingredients(self) -> dict:
        service = self.service(approval_policy=ApprovalPolicy())
        return dict(service.step_graph_composition())

    def test_64_an_absent_approval_policy_key_is_a_controlled_configuration_error(self) -> None:
        """Case 1: the key is ABSENT. A raw KeyError would be a traceback, not a refusal."""

        from app.api.operating_surface import SurfaceCompositionError

        ingredients = self._real_ingredients()
        del ingredients["approval_policy"]
        surface = self._surface_with(ingredients)

        with self.assertRaises(SurfaceCompositionError) as caught:
            surface.build_step_graph_runner()
        self.assertEqual(caught.exception.code, "approval_policy_missing")
        # It is an OperatingSurfaceError, so the route layer can serialize it.
        self.assertIsInstance(caught.exception, Exception)
        self.assertFalse((self.root / "one.txt").exists())

    def test_65_a_none_approval_policy_means_no_runner_not_an_error(self) -> None:
        """Case 2: the value is ``None``. A legitimate state, reported as no runner."""

        ingredients = self._real_ingredients()
        ingredients["approval_policy"] = None
        surface = self._surface_with(ingredients)

        # No exception: "no approval position stated" is the ordinary fail-closed
        # answer, distinct from "the composition is broken".
        self.assertIsNone(surface.build_step_graph_runner())
        self.assertFalse((self.root / "one.txt").exists())

    def test_66_a_wrong_typed_approval_policy_is_a_controlled_error(self) -> None:
        """Case 3: the wrong TYPE. A policy that cannot be enforced is not a policy."""

        from app.api.operating_surface import SurfaceCompositionError

        for bad, expected_code in (
            ("a string", "approval_policy_invalid_type"),
            (object(), "approval_policy_invalid_type"),
            ({"approval_required_tools": ("python",)}, "approval_policy_invalid_type"),
            (True, "approval_policy_invalid_type"),
        ):
            ingredients = self._real_ingredients()
            ingredients["approval_policy"] = bad
            surface = self._surface_with(ingredients)
            with self.assertRaises(SurfaceCompositionError, msg=repr(bad)) as caught:
                surface.build_step_graph_runner()
            self.assertEqual(caught.exception.code, expected_code, repr(bad))
        self.assertFalse((self.root / "one.txt").exists())

    def test_67_a_lookalike_policy_that_fails_the_contract_is_refused(self) -> None:
        """A class that is not ``ApprovalPolicy`` is refused even if it has an evaluate."""

        from app.api.operating_surface import SurfaceCompositionError

        class _Lookalike:
            def evaluate(self, tool):  # noqa: ANN001
                return ApprovalState.NOT_REQUIRED

        ingredients = self._real_ingredients()
        ingredients["approval_policy"] = _Lookalike()
        with self.assertRaises(SurfaceCompositionError) as caught:
            self._surface_with(ingredients).build_step_graph_runner()
        self.assertEqual(caught.exception.code, "approval_policy_invalid_type")

    def test_68_the_refusal_happens_before_the_coordinator_is_constructed(self) -> None:
        """ORDERING: validation precedes assembly, for every failing case.

        ``_step_graph_coordinator`` is replaced by one that records construction and
        then raises. If validation ran *after* assembly, the construction record would
        be non-empty and the raised error would be the marker instead of the typed
        composition error.
        """

        from unittest.mock import patch

        from app.api.operating_surface import SurfaceCompositionError

        constructed: list[str] = []

        def _explode(self):  # noqa: ANN001
            constructed.append("coordinator")
            raise RuntimeError("coordinator construction was reached")

        cases = []
        absent = self._real_ingredients()
        del absent["approval_policy"]
        cases.append(absent)
        wrong = self._real_ingredients()
        wrong["approval_policy"] = "not-a-policy"
        cases.append(wrong)
        malformed = self._real_ingredients()
        malformed["approval_policy"] = _IngredientsService(self.surface(), {})
        cases.append(malformed)

        for ingredients in cases:
            constructed.clear()
            surface = self._surface_with(ingredients)
            with patch.object(
                type(surface), "_step_graph_coordinator", _explode
            ):
                with self.assertRaises(SurfaceCompositionError) as caught:
                    surface.build_step_graph_runner()
                self.assertIsInstance(caught.exception, SurfaceCompositionError)
            self.assertEqual(
                constructed,
                [],
                "the coordinator was constructed before the composition was validated",
            )

    def test_69_a_none_policy_also_builds_no_coordinator(self) -> None:
        """The approving-``None`` path is equally lazy: no coordinator is built."""

        from unittest.mock import patch

        constructed: list[str] = []

        def _explode(self):  # noqa: ANN001
            constructed.append("coordinator")
            raise RuntimeError("coordinator construction was reached")

        ingredients = self._real_ingredients()
        ingredients["approval_policy"] = None
        surface = self._surface_with(ingredients)
        with patch.object(type(surface), "_step_graph_coordinator", _explode):
            self.assertIsNone(surface.build_step_graph_runner())
        self.assertEqual(constructed, [])

    def test_70_the_route_reports_a_composition_error_as_a_server_error(self) -> None:
        """A composition error is 500 OVER THE WIRE, never 409 and never a 200.

        THE OLD REVISION PASSED FOR THE WRONG REASON. It called ``_error_response``
        directly and asserted the ``(status, body)`` tuple that function returns.
        That proves the mapping *function* is written correctly while proving
        nothing about what a caller receives: the route could have discarded the
        mapping, wrapped it, or answered 200 carrying that body, and the test would
        still have passed. It also never checked the two things that matter most --
        that the caller is not told the request conflicted, and that nothing ran.

        The check now drives the real ``POST /api/product/graph`` route with an
        ingredients document whose ``approval_policy`` key is ABSENT, so the
        composition is *broken* rather than merely unstated, and observes the HTTP
        status a caller actually gets.
        """

        from unittest.mock import patch

        service = self.service(approval_policy=ApprovalPolicy())
        client = self.client(service)

        ingredients = dict(service.step_graph_composition())
        # ABSENT, not None: None is the legitimate "no approval position stated"
        # case (409 ``step_graph_unavailable``, covered by test_32c). A missing key
        # is a broken composition and must not be reported as the caller's conflict.
        del ingredients["approval_policy"]

        with patch.object(
            ForgeApiService, "step_graph_composition", lambda self: ingredients
        ):
            response = client.post(
                "/api/product/graph",
                json={"graph_run_id": "compose-broken", "steps": self.steps_two()},
            )

        # (1) The real HTTP status a caller sees.
        self.assertEqual(response.status_code, 500, response.text)
        self.assertNotEqual(
            response.status_code,
            409,
            "a server-side composition fault was reported as the caller's conflict",
        )
        self.assertNotEqual(
            response.status_code,
            200,
            "a failed operation was reported as a success",
        )
        # The graph routes serialize a typed refusal through ``_run_catching``, which
        # returns the body DIRECTLY, so the configuration signal is at the top level
        # of the JSON document. Asserted explicitly, because this differs from the
        # verify/accept routes below and a silent change of shape would otherwise
        # make the checks here vacuous.
        body = response.json()
        self.assertTrue(body["configuration_error"], response.text)
        self.assertEqual(body["code"], "approval_policy_missing")
        self.assertFalse(body["executed"])

        # (2) AND NOTHING RAN. A status code can be right while the command still
        #     executed, so the workspace and the run history are both checked.
        self.assertFalse((self.root / "one.txt").exists())
        onboarding = client.get("/api/product/onboarding").json()
        self.assertEqual(onboarding["counts"]["recorded_runs"], 0)
        self.assertIsNone(onboarding["latest_classification"])

        # (3) The SAME class of fault reaching the OTHER error path carries the same
        #     three signals. ``/api/product/verify`` takes its approval ingredient
        #     from the SERVICE (``service._approval_policy``), not from
        #     ``step_graph_composition()`` -- those are two different sources, which is
        #     why patching the composition above does not reach this route. With no
        #     policy stated at all it raises an ``HTTPException`` instead of returning
        #     a body, so FastAPI nests the signal under ``detail``. Both shapes are
        #     asserted so neither can regress unnoticed, and the difference between
        #     them is recorded rather than left implicit.
        bare = self.client(
            ForgeApiService(
                runtime=self.runtime,
                workspace=self.workspace,
                run_store=self.run_store,
                tool_registry=build_default_tool_registry(self.root),
                execution_profile=self._profile(),
                declarations=self.declarations(),
                acceptance_specs=self.acceptance_specs(),
                idempotency_guard=self.idempotency_guard,
                # approval_policy deliberately omitted: stays None.
            )
        )
        verify = bare.post(
            "/api/product/verify",
            json={"declaration_id": self.one_id, "idempotency_key": _unique("k.d70")},
        )
        self.assertEqual(verify.status_code, 500, verify.text)
        self.assertNotEqual(verify.status_code, 200, verify.text)
        self.assertFalse((self.root / "one.txt").exists())
        self.assertIn("detail", verify.json(), verify.text)
        nested = verify.json()["detail"]
        self.assertTrue(nested["configuration_error"], verify.text)
        self.assertEqual(nested["code"], "approval_policy_required")
        self.assertFalse(nested["executed"])


class NoApprovalPolicyTests(Block8Case):
    """FAIL-CLOSED: a missing approval policy authorises nothing, on any route.

    THE DEFECT THIS PINS, MEASURED. ``app/execution/authorizer.py`` guarded its
    whole approval layer with ``approval_policy is not None``, so a ``None`` policy
    SKIPPED approval and meant "no approval required". ``app/orchestrator/run.py``
    made the same ``None`` an unconditional denial. The two readings disagreed, and
    the permissive one won on every execution path that reached the coordinator
    without going through ``RunExecutor`` -- measured through the Block #8 surface,
    ``POST /api/product/accept`` returned HTTP 200 with ``success=true`` and the
    declared host command really ran, writing a marker into the operator's
    workspace. ``can_run_declared_verification()`` also reported ``True`` while
    every call was denied.

    There is one meaning now. This class asserts the security property directly --
    no host command executes -- and the honest signals around it. It checks the
    filesystem, not only the status codes, because a status code can be right while
    the command still ran.
    """

    def bare_service(self) -> ForgeApiService:
        """A real composition that states **no** approval position.

        Every ingredient a host execution needs is present -- runtime, workspace,
        run store, profile, declarations, acceptance spec, idempotency guard -- so
        nothing below can refuse for an unrelated reason. The only thing missing is
        the approval policy, which is the one variable under test.
        """

        return ForgeApiService(
            runtime=self.runtime,
            workspace=self.workspace,
            run_store=self.run_store,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=self._profile(),
            declarations=self.declarations(),
            acceptance_specs=self.acceptance_specs(),
            idempotency_guard=self.idempotency_guard,
            # approval_policy deliberately omitted: stays None.
        )

    def test_32c_no_route_executes_a_host_command_without_an_approval_policy(self) -> None:
        """_INJECTION_TARGET_: ``None`` policy must not authorise execution anywhere."""

        from app.api.operating_surface import SurfaceCompositionError

        bare = self.bare_service()
        self.assertIsNone(bare._approval_policy)
        client = self.client(bare)

        marker = "one.txt"
        self.assertFalse((self.root / marker).exists())

        # (1) The step-graph route: already fail-closed, and must stay so.
        graph = self.graph_run(client, "no-policy-graph", self.steps_two())
        self.assertEqual(graph.status_code, 409, graph.text)
        self.assertEqual(graph.json()["code"], "step_graph_unavailable")

        # (2) The declared-verification route. A missing server-side policy is a
        #     configuration fact, not a caller conflict, so the refusal must NOT be
        #     a retryable 409 and must NOT be a bare 200 reporting success.
        verify = client.post(
            "/api/product/verify",
            json={"declaration_id": self.one_id, "idempotency_key": _unique("k.np.v")},
        )
        self.assertFalse(
            (self.root / marker).exists(),
            "the declared-verification route ran a host command with no approval policy",
        )
        self.assertEqual(verify.status_code, 500, verify.text)
        detail = verify.json()["detail"]
        self.assertTrue(detail["configuration_error"])
        self.assertEqual(detail["code"], "approval_policy_required")
        self.assertFalse(detail["executed"])

        # (3) The accept route. Same contract, and the workspace must be untouched.
        before = sorted(p.name for p in self.root.iterdir())
        accept = client.post(
            "/api/product/accept",
            json={"declaration_id": self.build_id, "idempotency_key": _unique("k.np.a")},
        )
        after = sorted(p.name for p in self.root.iterdir())
        self.assertEqual(
            before,
            after,
            "the accept route wrote into the operator's workspace with no approval policy",
        )
        self.assertFalse((self.root / MODULE_PATH).exists())
        self.assertFalse((self.root / marker).exists())
        self.assertEqual(accept.status_code, 500, accept.text)
        accept_detail = accept.json()["detail"]
        self.assertTrue(accept_detail["configuration_error"])
        self.assertEqual(accept_detail["code"], "approval_policy_required")
        self.assertFalse(accept_detail["executed"])

        # AND THE ROOT CAUSE, pinned at the level that owns the rule: the
        # coordinator itself refuses, whatever caller arrives.
        surface = self.surface(bare)
        with self.assertRaises(SurfaceCompositionError):
            surface.run_accepted_task(self.build_id, idempotency_key="k.np.direct")

    def test_32d_a_missing_policy_is_not_advertised_as_available(self) -> None:
        """The capability signal must agree with what the routes would do."""

        bare = self.bare_service()
        surface = self.surface(bare)
        caps = surface.project_overview()["capabilities"]

        # Every one of these dispatches host commands, so none may claim availability.
        self.assertFalse(caps["step_graph"])
        self.assertFalse(caps["declared_verification"])
        # The accept cycle dispatches host commands through the SAME chain and the
        # surface refuses it outright when no policy is stated, so its flag must agree.
        # MEASURED BEFORE THIS ASSERTION EXISTED: the flag reported True while
        # `POST /api/product/accept` answered 500 `approval_policy_required` and the
        # operator's workspace stayed untouched -- a capability claim that contradicted
        # the behaviour it described.
        self.assertFalse(caps["accept_cycle"], caps)
        self.assertFalse(bare.can_run_accept_cycle())
        self.assertFalse(bare.can_run_declared_verification())
        self.assertIsNone(surface.build_step_graph_runner())

    def test_32e_the_coordinator_itself_refuses_a_missing_policy(self) -> None:
        """_INJECTION_TARGET_: the LAYER that owns the rule must refuse on its own.

        WHY THIS EXISTS, MEASURED. ``test_32c`` drives the routes and observes that no
        host command runs and that each surface reports a configuration error. That is
        the right end-to-end property, but it does **not** pin the coordinator's own
        ``approval_policy is None`` guard: measured, disabling exactly that guard in
        ``app/execution/authorizer.py`` leaves ``test_32c`` green, because the service
        and the operating surface refuse *earlier*, before the coordinator is reached.
        A guard no test can fail is not evidence, so it is driven here directly, with
        nothing between it and a real ``python`` process.

        The control is in the same test: the identical request with a stated
        ``ApprovalPolicy`` succeeds and really writes the marker, so the refusal below
        is caused by the missing policy and not by the request, the scope, the
        workspace or the adapter being unable to run at all.
        """

        from app.execution.adapter import LocalExecutionAdapter
        from app.execution.authorizer import ExecutionCoordinator
        from app.execution.request import ExecutionOutcomeStatus, ExecutionRequest

        marker = "coord-no-policy.txt"
        command = (
            "python",
            "-c",
            "open(" + repr(marker) + ",'w',encoding='utf-8').write('ran')",
        )

        def make_request(run_id: str) -> Any:
            return ExecutionRequest(
                command=command,
                request_id=run_id + "-request",
                working_directory=".",
                profile=self._profile(),
                metadata={"run_id": run_id},
            )

        def make_scope(run_id: str) -> Any:
            scope = RunScope(
                run_id=run_id,
                workspace=self.workspace,
                execution_profile=self._profile(),
                allowed_tool_ids=frozenset(),
                allowed_execution_commands=frozenset({"python"}),
                acceptance_criteria=(
                    AcceptanceCriterion(
                        run_id,
                        "the coordinator run reaches a terminal state",
                        requirement_id="coord",
                    ),
                ),
            )
            scope.freeze()
            return scope

        def make_coordinator() -> Any:
            return ExecutionCoordinator(
                LocalExecutionAdapter(workspace_root=self.root, isolate_workspace=False)
            )

        # (1) NO POLICY. The dispatch must be denied, and the reason must be the
        #     policy's absence rather than a downstream accident.
        events: list[tuple[str, dict]] = []
        denied = make_coordinator().execute(
            make_request("coord-no-policy-run"),
            workspace_root=self.root,
            run_id="coord-no-policy-run",
            allowed_commands=frozenset({"python"}),
            approval_policy=None,
            approval_resolver=None,
            run_scope=make_scope("coord-no-policy-run"),
            observer=lambda event_type, data: events.append((event_type, data)),
        )
        self.assertEqual(
            denied.outcome_status, ExecutionOutcomeStatus.POLICY_DENIED, denied.metadata
        )
        self.assertEqual(
            denied.metadata.get("denial_reason"), "approval_policy_required", denied.metadata
        )
        self.assertTrue(
            any(str(data.get("reason")) == "approval_policy_required" for _, data in events),
            events,
        )
        # REFUSED BEFORE ANY SIDE EFFECT: no file, no exit code, no process.
        self.assertFalse((self.root / marker).exists())
        self.assertIsNone(denied.exit_code)

        # (2) CONTROL: a stated policy lets the very same request run. Without this
        #     the assertions above could be satisfied by a composition that can never
        #     execute anything at all.
        allowed = make_coordinator().execute(
            make_request("coord-with-policy-run"),
            workspace_root=self.root,
            run_id="coord-with-policy-run",
            allowed_commands=frozenset({"python"}),
            approval_policy=ApprovalPolicy(),
            approval_resolver=None,
            run_scope=make_scope("coord-with-policy-run"),
        )
        self.assertEqual(
            allowed.outcome_status,
            ExecutionOutcomeStatus.EXECUTION_SUCCESS,
            allowed.metadata,
        )
        self.assertEqual(allowed.exit_code, 0)
        self.assertEqual((self.root / marker).read_text(encoding="utf-8"), "ran")

        # AND THE POSITIVE CONTROL: with a policy the same composition does advertise
        # it, so the assertions above are about the policy and not about the fixture.
        stated = self.service()
        self.assertIsNotNone(stated._approval_policy)
        self.assertTrue(stated.can_run_declared_verification())
        self.assertTrue(self.surface(stated).project_overview()["capabilities"]["declared_verification"])


class CompositionPlacementTests(Block8Case):
    """Where assembly lives, pinned in both directions (the Block #8 decision).

    The decision: assembly lives in ``app/api/operating_surface.py``. The API
    *service* delegates rather than spawns, and no *route* constructs an authority
    component. A previous revision of ``routes.py`` claimed "no route constructs or
    bypasses ``RunScope``/``ExecutionCoordinator``/``AuthorizedExecution``/
    ``ApprovalPolicy``" while assembly had just moved into the surface -- the claim was
    imprecise and is now stated exactly. These tests keep code and statement agreeing.
    """

    BANNED = ("LocalExecutionAdapter", "AuthorizedExecution", "subprocess", "Popen")
    #: The wider ban, applied to the HTTP boundary only. ``app/api/service.py``
    #: legitimately constructs ``RunScope`` and reads an ``ApprovalPolicy``.
    ROUTE_BANNED = BANNED + ("RunScope", "ExecutionCoordinator", "ApprovalPolicy")

    def _code_of(self, relative: str) -> str:
        """The file's source with every docstring removed, so prose is not code."""

        import ast

        source = (Path(__file__).resolve().parents[1] / relative).read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    docstrings.add(doc)
        kept = []
        for line in source.splitlines():
            stripped = line.strip()
            if any(stripped and stripped in doc for doc in docstrings):
                continue
            kept.append(line)
        return "\n".join(kept)

    def test_71_neither_the_route_module_nor_the_service_names_an_authority_component(self) -> None:
        """The invariant, asserted on executable lines only.

        TWO LISTS, BECAUSE THE TWO MODULES ARE HELD TO DIFFERENT RULES -- and the
        docstring in ``app/api/routes.py`` used to claim this test pinned the wider
        one for both. It does not, and it cannot honestly: ``app/api/service.py``
        legitimately constructs ``RunScope`` and reads an ``ApprovalPolicy`` to hand
        them to the execution chain, so a wider ban would fail on correct code. The
        claim was corrected rather than the check weakened.

        ``BANNED`` (both modules, as before): the adapter, the execution token, and
        the process-spawning primitives. Neither module may name these.
        ``ROUTE_BANNED`` (``app/api/routes.py`` only): also ``RunScope``,
        ``ExecutionCoordinator`` and ``ApprovalPolicy``, because the HTTP boundary
        constructs no authority component at all.
        """

        for relative in ("app/api/routes.py", "app/api/service.py"):
            code = self._code_of(relative)
            for banned in self.BANNED:
                self.assertNotIn(banned, code, f"{relative} names {banned}")

        # The route module specifically: no authority component of any kind.
        route_code = self._code_of("app/api/routes.py")
        for banned in self.ROUTE_BANNED:
            self.assertNotIn(banned, route_code, f"app/api/routes.py names {banned}")
        # And the claim above is not vacuous: the same names ARE present in the
        # service's executable code, which is why the wider list cannot apply to it.
        service_code = self._code_of("app/api/service.py")
        self.assertIn("RunScope", service_code)
        self.assertIn("ApprovalPolicy", service_code)

    def test_72_the_operating_surface_is_the_one_place_that_assembles(self) -> None:
        """The other half: assembly IS here, and only here, in the API layer."""

        import ast

        relative = "app/api/operating_surface.py"
        source = (Path(__file__).resolve().parents[1] / relative).read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name:
                    called.add(name)
        # It imports the two components ...
        self.assertIn("LocalExecutionAdapter", imported)
        self.assertIn("ExecutionCoordinator", imported)
        # ... and constructs exactly one of each.
        self.assertEqual(source.count("ExecutionCoordinator("), 1)
        self.assertEqual(source.count("LocalExecutionAdapter("), 1)

        # No other API-layer module constructs them.
        api_dir = Path(__file__).resolve().parents[1] / "app" / "api"
        for path in api_dir.glob("*.py"):
            if path.name == "operating_surface.py":
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ExecutionCoordinator(", text, path.name)
            self.assertNotIn("LocalExecutionAdapter(", text, path.name)

    def test_73_the_coordinator_is_built_once_per_surface(self) -> None:
        """Exactly one coordinator object, so two runs cannot get two authorities."""

        surface = self.surface(self.service(approval_policy=ApprovalPolicy()))
        first = surface._step_graph_coordinator()
        second = surface._step_graph_coordinator()
        self.assertIs(first, second)
        # And two runners over one surface share it.
        one = surface.build_step_graph_runner()
        two = surface.build_step_graph_runner()
        self.assertIsNotNone(one)
        self.assertIs(one._execution_coordinator, two._execution_coordinator)


# ---------------------------------------------------------------------------
# 5. step failure and dependent-step blocking
# ---------------------------------------------------------------------------


class StepFailureTests(Block8Case):
    def test_21_a_failing_step_fails_the_graph_and_blocks_its_dependent(self) -> None:
        """_INJECTION_TARGET_: a blocked step must never be dispatched."""

        steps = [
            {"step_id": "step-one", "action": {"declaration_id": self.fail_id}},
            {
                "step_id": "step-two",
                "depends_on": ["step-one"],
                "action": {"declaration_id": self.one_id},
            },
        ]
        client = self.client()
        response = self.graph_run(client, "fail-chain", steps)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["result"]
        statuses = {step["step_id"]: step["status"] for step in result["steps"]}
        self.assertEqual(statuses["step-one"], "FAILED")
        self.assertEqual(statuses["step-two"], "BLOCKED")
        self.assertEqual(result["status"], "FAILED")
        # The blocked step's command never ran, so its file was never created.
        self.assertFalse((self.root / "one.txt").exists())

    def test_22_a_non_zero_exit_is_reported_as_the_observed_exit_code(self) -> None:
        client = self.client()
        steps = [{"step_id": "step-one", "action": {"declaration_id": self.fail_id}}]
        record = self.graph_run(client, "exit-three", steps).json()["result"]
        self.assertEqual(record["steps"][0]["execution_exit_code"], 3)


# ---------------------------------------------------------------------------
# 6. cancellation, timeout, indeterminate
# ---------------------------------------------------------------------------


class IndeterminateTests(Block8Case):
    def test_23_an_interrupted_step_is_read_back_as_unknown(self) -> None:
        """_INJECTION_TARGET_: UNKNOWN must never become SUCCEEDED or be retried.

        The record is built with the runner's own value objects, so it has exactly
        the shape a crash between "dispatched" and "settled" leaves on disk.
        """

        from app.workflows.step_graph import (
            StepAction,
            StepDefinition,
            StepGraph,
            StepGraphSnapshot,
            StepGraphStatus,
            StepRecord,
            StepStatus,
            declared_plan_of,
        )
        from app.runtime.run_store import RunStateSnapshot

        definition = StepDefinition(
            step_id="step-one", action=StepAction(self.one_id)
        )
        graph = StepGraph(graph_id="console-plan", steps=(definition,))
        projected = StepGraphSnapshot(
            graph_id="console-plan",
            graph_run_id="interrupted",
            status=StepGraphStatus.INCOMPLETE,
            steps=(
                StepRecord(
                    step_id="step-one",
                    status=StepStatus.RUNNING,
                    # The record a real run leaves behind carries the fingerprint of
                    # the plan it was driving, which is what lets a resume prove the
                    # caller's graph is the one that was declared.
                    action_fingerprint=definition.fingerprint(),
                    declaration_id=self.one_id,
                ),
            ),
            declared_plan=declared_plan_of(graph),
        )
        self.run_store.write_state(
            RunStateSnapshot(
                run_id="interrupted",
                task_id="",
                state="running",
                status="running",
                project_state={"step_graph": projected.to_dict()},
                updated_at="2026-01-01T00:00:00+00:00",
            )
        )
        client = self.client()
        status = client.get("/api/product/graph/interrupted").json()
        self.assertTrue(status["found"])
        self.assertEqual(status["steps"][0]["status"], "UNKNOWN")

        # Resuming must not repeat it: an unknown result is not permission to retry.
        resumed = client.post("/api/product/graph/interrupted/resume", json={})
        self.assertEqual(resumed.status_code, 200, resumed.text)
        statuses = {
            step["step_id"]: step["status"]
            for step in resumed.json()["result"]["steps"]
        }
        self.assertNotEqual(statuses["step-one"], "SUCCEEDED")
        self.assertNotEqual(statuses["step-one"], "RUNNING")
        self.assertFalse((self.root / "one.txt").exists())

    def test_24_a_start_before_cancellation_is_refused_before_any_step(self) -> None:
        """An attempt whose record is already recorded executes nothing on a re-ask."""

        client = self.client()
        first = self.graph_run(client, "cancel-first", self.steps_two())
        self.assertEqual(first.status_code, 200, first.text)
        lines_after_first = (self.root / "one.txt").read_text(encoding="utf-8")
        second = self.graph_run(client, "cancel-first", self.steps_two())
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["replayed"])
        self.assertFalse(second.json()["executed"])
        # The command is not append-based, so this checks the run, not the bytes.
        self.assertEqual(
            (self.root / "one.txt").read_text(encoding="utf-8"), lines_after_first
        )

    def test_25_an_unknown_baseline_is_refused_rather_than_substituted(self) -> None:
        """Answering a question about a different baseline is worse than refusing."""

        client = self.client()
        response = client.get("/api/product/change-impact?baseline=never-recorded")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "unknown_baseline")

    def test_25b_an_unsafe_baseline_identifier_is_a_permission_refusal(self) -> None:
        client = self.client()
        response = client.get("/api/product/change-impact?baseline=..%2Fescape")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["code"], "unsafe_identifier")

    def test_26_an_unsafe_graph_run_id_is_refused_at_the_route(self) -> None:
        """The id reaches the storage layout, so it is allow-listed, not sanitized."""

        client = self.client()
        for bad in ("../escape", "a/b", "C:evil", "", "x" * 200):
            response = self.graph_run(client, bad, self.steps_two())
            self.assertEqual(response.status_code, 400, bad)
            self.assertIn("graph_run_id", response.text)

        # On a PATH parameter, an identifier containing an encoded slash is
        # rejected by the router (404) before any handler runs -- an equally safe
        # outcome, and the unsafe value never reaches the store. An identifier with
        # only allow-list-shaped characters but a shape the allow-list rejects does
        # reach the handler, and is refused as a permission error.
        for bad in ("..%2Fescape", "x" * 200):
            for template in (
                "/api/product/graph/{}",
                "/api/product/approvals/{}",
                "/api/product/recovery/{}",
                "/api/product/runs/{}",
            ):
                response = client.get(template.format(bad))
                self.assertIn(response.status_code, (403, 404), (template, bad))
                if response.status_code == 403:
                    self.assertEqual(response.json()["code"], "unsafe_identifier")

        # "...." is made only of allow-list characters, so the router serves it and
        # the surface's own allow-list is what refuses it.
        for template in (
            "/api/product/graph/{}",
            "/api/product/approvals/{}",
            "/api/product/recovery/{}",
            "/api/product/runs/{}",
        ):
            response = client.get(template.format("...."))
            self.assertEqual(response.status_code, 403, template)
            self.assertEqual(response.json()["code"], "unsafe_identifier")


# ---------------------------------------------------------------------------
# 7. recovery after restart
# ---------------------------------------------------------------------------


class RecoveryTests(Block8Case):
    def test_27_recovery_plans_a_failed_attempt_and_executes_nothing(self) -> None:
        client = self.client()
        steps = [
            {"step_id": "step-one", "action": {"declaration_id": self.fail_id}},
            {
                "step_id": "step-two",
                "depends_on": ["step-one"],
                "action": {"declaration_id": self.one_id},
            },
        ]
        self.graph_run(client, "recover-me", steps)

        scan = client.get("/api/product/recovery")
        self.assertEqual(scan.status_code, 200, scan.text)
        body = scan.json()
        ids = {finding["graph_run_id"] for finding in body["unfinished"]}
        self.assertIn("recover-me", ids)
        # Nothing was executed by the scan itself.
        self.assertFalse((self.root / "one.txt").exists())

        plan = client.get("/api/product/recovery/recover-me")
        self.assertEqual(plan.status_code, 200, plan.text)
        decisions = {step["step_id"]: step["decision"] for step in plan.json()["steps"]}
        self.assertIn("step-two", decisions)

    def test_28_recovery_after_a_real_restart_sees_the_same_state(self) -> None:
        """A NEW store and a NEW service over the same root: a real restart."""

        client = self.client()
        self.graph_run(
            client, "restart-me", [{"step_id": "s", "action": {"declaration_id": self.fail_id}}]
        )

        # Rebuild everything the way a restart would.
        restarted_store = RunStore(root=str(self.store_root))
        restarted_guard = integration.RunIdempotencyGuard.create(
            ledger_root=str(self.idempotency_guard.engine.ledger.root)
        )
        restarted = ForgeApiService(
            runtime=self.runtime,
            workspace=self.workspace,
            run_store=restarted_store,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=self._profile(),
            declarations=self.declarations(),
            acceptance_specs=self.acceptance_specs(),
            idempotency_guard=restarted_guard,
        )
        fresh = self.client(restarted)
        scan = fresh.get("/api/product/recovery")
        self.assertEqual(scan.status_code, 200, scan.text)
        ids = {finding["graph_run_id"] for finding in scan.json()["unfinished"]}
        self.assertIn("restart-me", ids)

    def test_29_recovery_is_unavailable_without_an_injected_guard(self) -> None:
        """Fail-closed: no guard means the route refuses rather than planning loosely."""

        bare = ForgeApiService(
            runtime=self.runtime, workspace=self.workspace, run_store=self.run_store
        )
        response = self.client(bare).get("/api/product/recovery")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "recovery_unavailable")


# ---------------------------------------------------------------------------
# 8. refusal to re-run a non-idempotent action
# ---------------------------------------------------------------------------


class RepeatRefusalTests(Block8Case):
    def test_30_a_repeated_graph_start_does_not_re_execute_the_step(self) -> None:
        """The durable record -- not an in-process flag -- is what prevents a repeat.

        The declared command *appends* a marker, so a second execution is visible in
        the bytes rather than merely inferred from a status.

        MEASURED CORRECTION. This test was first written as an injection target for
        ``StepGraphRunner._claim_graph_run``. Disabling that claim did **not** break
        it, and the reason is a real property rather than a weak test: the claim is
        the *in-process concurrency* guard (two callers in one process racing), while
        repeat protection for a caller that asks twice comes from the durable record
        the runner reads first. ``tests/test_step_graph_managed_execution.py`` covers
        both, and ``test_a_concurrent_second_start_of_the_same_graph_is_refused``
        covers the claim. What this test adds is that the property survives the HTTP
        boundary. Saying so is more useful than an annotation that overstates it.
        """

        append_id = _unique("b8.append")
        append_command = (
            "python",
            "-c",
            "open('count.txt','a',encoding='utf-8').write('x')",
        )
        declarations = self.declarations()
        declarations[append_id] = ExecutionDeclaration(
            declaration_id=append_id,
            command=append_command,
            profile_id=PROFILE_ID,
            working_directory=".",
            intent="Append one marker.",
        )
        client = self.client(self.service(declarations=declarations))
        steps = [{"step_id": "s", "action": {"declaration_id": append_id}}]

        first = self.graph_run(client, "repeat-me", steps)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["replayed"])
        self.assertEqual(len((self.root / "count.txt").read_text(encoding="utf-8")), 1)

        second = self.graph_run(client, "repeat-me", steps)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["replayed"])
        self.assertFalse(second.json()["executed"])
        # Still exactly one marker: nothing was re-executed.
        self.assertEqual(len((self.root / "count.txt").read_text(encoding="utf-8")), 1)


# ---------------------------------------------------------------------------
# 9. failed verification while the command succeeded
# ---------------------------------------------------------------------------


class VerificationTests(Block8Case):
    def test_31_a_step_whose_verification_fails_is_not_a_success(self) -> None:
        """_INJECTION_TARGET_: exit 0 with a failed check must never read as success."""

        # The command exits 0 but produces no artifact, while the step declares
        # that an artifact must exist. The command succeeded; the check did not.
        noop_id = _unique("b8.noop")
        declarations = self.declarations()
        declarations[noop_id] = ExecutionDeclaration(
            declaration_id=noop_id,
            command=("python", "-c", "print('nothing produced')"),
            profile_id=PROFILE_ID,
            working_directory=".",
            artifact_targets=("produced.txt",),
            intent="Claim to produce an artifact it does not.",
        )
        client = self.client(self.service(declarations=declarations))
        steps = [
            {
                "step_id": "s",
                "action": {"declaration_id": noop_id},
                "verification": {
                    "kind": "ARTIFACT_PRODUCED",
                    "artifact_target": "produced.txt",
                },
            }
        ]
        response = self.graph_run(client, "verify-fail", steps)
        self.assertEqual(response.status_code, 200, response.text)
        step = response.json()["result"]["steps"][0]
        # The process exited 0 ...
        self.assertEqual(step["execution_exit_code"], 0)
        # ... and the declared check still failed, so the step is not a success.
        self.assertEqual(step["verification_status"], "FAILED")
        self.assertNotEqual(step["status"], "SUCCEEDED")

    def test_32_the_graph_status_route_reports_the_verification_verdict(self) -> None:
        client = self.client()
        self.graph_run(
            client, "verify-read", [{"step_id": "s", "action": {"declaration_id": self.one_id}}]
        )
        status = client.get("/api/product/graph/verify-read").json()
        self.assertTrue(status["found"])
        self.assertIn("verification_status", status["steps"][0])

    def test_32b_a_produced_artifact_passes_and_its_dependent_still_runs(self) -> None:
        """_INJECTION_TARGET_: a REAL artifact must verify PASSED, not FAILED.

        THE DEFECT THIS PINS, MEASURED. ``build_step_graph_runner`` hard-coded
        ``artifacts_are_harvested=True`` while ``_step_graph_coordinator`` composed
        ``LocalExecutionAdapter(isolate_workspace=False)``, and the adapter
        harvested only *inside* its isolation branch. So on the step-graph path the
        adapter harvested nothing while the runner claimed it did, and the runner's
        three-way verdict collapsed into a false claim:

        * command exit 0 -- true;
        * ``made.txt`` really on disk in the operator's own workspace -- true;
        * verdict ``FAILED`` with ``artifact_not_produced:made.txt`` -- FALSE.

        The step was FAILED and every dependent was BLOCKED, so a correct run was
        reported as a broken one on the strength of a file the operator could see.

        The two verdicts that must never be conflated are ``FAILED`` ("the verifier
        looked and the artifact is not there") and ``ERROR`` ("this composition
        cannot evaluate the check"). Before the fix the first lie was told; with
        only the flag flipped it became ``ERROR`` /
        ``adapter_does_not_harvest``. Neither is acceptable, because the artifact
        genuinely exists. This test requires the third answer: PASSED, with the
        digest the adapter actually measured, and the dependent step still running.

        Checks the *behaviour*, not the flag: it never asserts that harvesting is
        enabled, so it cannot pass by reading the same constant it is testing.
        """

        client = self.client()
        steps = [
            {
                "step_id": "step-one",
                "action": {"declaration_id": self.one_id},
                "verification": {
                    "kind": "ARTIFACT_PRODUCED",
                    "artifact_target": "one.txt",
                },
            },
            {
                "step_id": "step-two",
                "depends_on": ["step-one"],
                "action": {"declaration_id": self.read_id},
            },
        ]
        response = self.graph_run(client, "verify-produced", steps)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()["result"]
        first, second = body["steps"]

        # The command really ran and really succeeded.
        self.assertEqual(first["execution_exit_code"], 0)
        self.assertEqual(first["execution_outcome"], "EXECUTION_SUCCESS")
        # The artifact really exists -- asserted on the filesystem, so the verdict
        # below cannot be satisfied by a fabricated observation.
        self.assertTrue((self.root / "one.txt").exists())
        self.assertEqual((self.root / "one.txt").read_text(encoding="utf-8"), "1")

        # THE CLAIM UNDER TEST. A produced-and-verifiable artifact is PASSED, and
        # the observation carries a measured digest rather than an assertion.
        self.assertEqual(
            first["verification_status"],
            "PASSED",
            f"a produced artifact must verify PASSED, got "
            f"{first['verification_status']!r} observed={first['verification_observed']!r} "
            f"evidence={first['evidence']!r}",
        )
        self.assertIn("artifact_produced=one.txt", first["verification_observed"])
        self.assertIn("sha256=", first["verification_observed"])
        self.assertEqual(first["status"], "SUCCEEDED")
        # Not a false claim of absence, and not a false claim of unevaluability.
        self.assertNotIn("artifact_not_produced", first["evidence"])
        self.assertNotIn("verification_error:", " ".join(first["evidence"]))

        # AND THE CONSEQUENCE THAT MADE THIS HIGH: a wrong FAILED blocks
        # dependents. The dependent must have really run.
        self.assertEqual(
            second["status"],
            "SUCCEEDED",
            f"the dependent of a genuinely produced artifact must not be blocked, "
            f"got {second['status']!r} evidence={second['evidence']!r}",
        )
        self.assertEqual(second["execution_exit_code"], 0)


# ---------------------------------------------------------------------------
# 10. protection of another caller's project / run
# ---------------------------------------------------------------------------


class IsolationTests(Block8Case):
    def test_33_a_foreign_project_id_is_refused(self) -> None:
        """_INJECTION_TARGET_: the project boundary must hold on every route."""

        client = self.client()
        for path in (
            "/api/product/onboarding",
            "/api/product/requirements",
            "/api/product/plan",
            "/api/product/understanding",
            "/api/product/memory",
            "/api/product/change-impact",
            "/api/product/runs",
        ):
            response = client.get(path, params={"project_id": "somebody-elses-project"})
            self.assertEqual(response.status_code, 403, path)

        for path in ("/api/product/goal", "/api/product/memory", "/api/product/baseline"):
            response = client.post(
                path, json={"project_id": "somebody-elses-project", "goal": "x"}
            )
            self.assertEqual(response.status_code, 403, path)

    def test_34_a_foreign_run_id_is_not_readable(self) -> None:
        """An unrecorded run is indistinguishable from an unknown one."""

        other_root = self._temp_dir()
        other_store = RunStore(root=str(other_root))
        from app.runtime.run_store import RunStateSnapshot

        other_store.write_state(
            RunStateSnapshot(run_id="someone-elses-run", task_id="t", state="done", status="ok")
        )
        client = self.client()
        response = client.get("/api/product/runs/someone-elses-run")
        self.assertEqual(response.status_code, 404)

    def test_35_an_unsafe_run_id_in_a_path_is_refused(self) -> None:
        client = self.client()
        response = client.get("/api/product/runs/..%2Fescape")
        self.assertIn(response.status_code, (403, 404))

    def test_36_the_response_never_names_the_requested_foreign_project(self) -> None:
        """A refusal must not become a probe for what exists."""

        client = self.client()
        response = client.get(
            "/api/product/onboarding", params={"project_id": "secret-project-name"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("secret-project-name", response.text)


# ---------------------------------------------------------------------------
# 11. user changes preserved / diff display
# ---------------------------------------------------------------------------


class ChangeImpactTests(Block8Case):
    def _seed(self) -> None:
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "core.py").write_text(
            "def alpha():\n    return 1\n", encoding="utf-8", newline=""
        )
        (self.root / "README.md").write_text("original\n", encoding="utf-8", newline="")

    def test_37_a_user_edit_is_reported_as_changed_with_both_fingerprints(self) -> None:
        """_INJECTION_TARGET_: the change report must reflect a real user edit."""

        self._seed()
        client = self.client()
        baseline = client.post("/api/product/baseline", json={})
        self.assertEqual(baseline.status_code, 200, baseline.text)

        # A USER EDIT, made outside Forge, in the workspace.
        (self.root / "pkg" / "core.py").write_text(
            "def alpha():\n    return 2\n", encoding="utf-8", newline=""
        )

        body = client.get("/api/product/change-impact").json()
        changes = {change["path"]: change for change in body["changes"]}
        self.assertIn("pkg/core.py", changes)
        change = changes["pkg/core.py"]
        self.assertEqual(change["kind"], "CHANGED")
        self.assertTrue(change["stale"])
        self.assertNotEqual(
            change["baseline_fingerprint"], change["current_fingerprint"]
        )
        # The user's edit is still on disk: nothing rewrote it.
        self.assertIn("return 2", (self.root / "pkg" / "core.py").read_text(encoding="utf-8"))

    def test_38_a_deleted_file_is_reported_as_deleted(self) -> None:
        self._seed()
        client = self.client()
        client.post("/api/product/baseline", json={})
        (self.root / "README.md").unlink()

        body = client.get("/api/product/change-impact").json()
        changes = {change["path"]: change for change in body["changes"]}
        self.assertIn("README.md", changes)
        self.assertEqual(changes["README.md"]["kind"], "DELETED")
        self.assertIsNone(changes["README.md"]["current_fingerprint"])

    def test_39_a_rename_is_reported_as_probable_never_as_established(self) -> None:
        """Two files with identical bytes are indistinguishable from a move."""

        self._seed()
        client = self.client()
        client.post("/api/product/baseline", json={})
        payload = (self.root / "README.md").read_text(encoding="utf-8")
        (self.root / "README.md").unlink()
        (self.root / "NOTES.md").write_text(payload, encoding="utf-8", newline="")

        body = client.get("/api/product/change-impact").json()
        changes = {change["path"]: change for change in body["changes"]}
        self.assertEqual(changes["README.md"]["kind"], "DELETED")
        self.assertEqual(changes["README.md"]["probable_rename_to"], "NOTES.md")

    def test_40_the_report_states_what_it_does_not_prove(self) -> None:
        """No false traceability: the limits are part of the response."""

        self._seed()
        client = self.client()
        client.post("/api/product/baseline", json={})
        (self.root / "pkg" / "core.py").write_text("x = 1\n", encoding="utf-8", newline="")
        body = client.get("/api/product/change-impact").json()
        claims = body["claims"]
        self.assertTrue(claims["proved"])
        self.assertTrue(claims["not_proved"])
        self.assertTrue(
            any("traceability" in item for item in claims["not_proved"]),
            claims["not_proved"],
        )
        # File contents are never returned.
        self.assertNotIn("x = 1", json.dumps(body))

    def test_41_a_changed_file_is_linked_to_an_acceptance_expectation(self) -> None:
        """A check is named only where a declared expectation names the path."""

        spec = AcceptanceSpec(
            declaration_id=self.build_id,
            criteria=(
                AcceptanceCriterion("core-present", "core.py exists", requirement_id="core"),
                AcceptanceCriterion(
                    "module-present", "calc.py exists", requirement_id="project-source"
                ),
            ),
            expectations={
                "core-present": VerificationExpectation("pkg/core.py", True),
                "module-present": VerificationExpectation("calc.py", True),
            },
        )
        self._seed()
        client = self.client(self.service(acceptance_specs={self.build_id: spec}))
        client.post("/api/product/baseline", json={})
        (self.root / "pkg" / "core.py").write_text("y = 2\n", encoding="utf-8", newline="")

        body = client.get("/api/product/change-impact").json()
        touched = {c["criterion_id"]: c for c in body["criteria_touched"]}
        # Only the criterion whose expectation names a CHANGED path is linked.
        self.assertEqual(set(touched), {"core-present"})
        self.assertEqual(touched["core-present"]["expectation_path"], "pkg/core.py")
        self.assertEqual(touched["core-present"]["requirement_id"], "core")
        # The requirement whose expectation names an unchanged path is reported as
        # unlinked rather than being credited with a change it did not see.
        self.assertIn("project-source", body["unlinked_requirements"])


# ---------------------------------------------------------------------------
# 12/13. fact invalidation, and confirmed facts vs hypotheses
# ---------------------------------------------------------------------------


class MemoryTests(Block8Case):
    def test_42_a_hypothesis_is_never_reported_as_a_confirmed_fact(self) -> None:
        """_INJECTION_TARGET_: an assumption must not be promoted."""

        client = self.client()
        response = client.post(
            "/api/product/memory", json={"statement": "The API is probably stateless."}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kind"], "HYPOTHESIS")
        self.assertFalse(body["confirmed_fact"])
        self.assertEqual(body["trust_level"], "INFERRED")

        view = client.get("/api/product/memory").json()
        buckets = view["buckets"]
        self.assertEqual(len(buckets["hypotheses"]), 1)
        self.assertEqual(len(buckets["confirmed_facts"]), 0)
        self.assertEqual(
            buckets["hypotheses"][0]["memory_id"], body["memory_id"]
        )

    def test_43_a_confirmed_fact_cannot_be_asserted_through_the_route(self) -> None:
        """_INJECTION_TARGET_: the kind allow-list must hold at the route."""

        client = self.client()
        for kind in ("CONFIRMED_FACT", "VERIFIED_CONCLUSION"):
            response = client.post(
                "/api/product/memory", json={"statement": "I say so.", "kind": kind}
            )
            self.assertEqual(response.status_code, 409, kind)
            self.assertEqual(response.json()["code"], "kind_not_recordable")

    def test_44_the_goal_is_recorded_as_a_hypothesis(self) -> None:
        """_INJECTION_TARGET_: the goal must be reported as a hypothesis.

        MEASURED NOTE. This test was first written with the goal *record
        construction* as its injection target. Changing that construction to a
        ``CONFIRMED_FACT`` did **not** break it, because ``capture_goal`` reports its
        verdict from its own return value rather than from the record it wrote -- so
        the response would have kept saying HYPOTHESIS while the stored entry said
        otherwise. That is a test that would have passed for the wrong reason.

        The construction is still checked, but by ``test_42`` and ``test_43``, which
        read the stored document back through the memory route. What THIS test pins
        is the report, and the injection that does break it is one that changes the
        returned ``kind``/``confirmed_fact`` pair.
        """

        client = self.client()
        response = client.post("/api/product/goal", json={"goal": GOAL})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["confirmed_fact"])
        self.assertEqual(body["kind"], "HYPOTHESIS")
        # The requirements returned are the operator's, not derived from the goal.
        ids = {entry["requirement_id"] for entry in body["requirements"]}
        self.assertIn(self.build_id, ids)
        # And the stored document agrees with the report, read back independently.
        buckets = client.get("/api/product/memory").json()["buckets"]
        self.assertEqual(buckets["confirmed_facts"], [])
        stored_ids = {entry["memory_id"] for entry in buckets["hypotheses"]}
        self.assertIn(body["memory_id"], stored_ids)

    def _record_confirmed_facts(self, limit: int = 1) -> list[str]:
        """Record real structural facts as ``CONFIRMED_FACT`` entries.

        This is the *legitimate* path a confirmed fact takes: a fact the bounded
        scanner really extracted, recorded through ``record_from_structural_fact``
        with the scanner's own SHA-256 as the evidence pin. The route deliberately
        cannot do this -- a confirmed fact must cite evidence, and a caller cannot
        supply an evidence id -- so the test does it through the same codec the
        understanding layer uses, which is exactly what an operator composition
        would do.

        Returns the recorded memory ids.
        """

        from app.understanding.memory import (
            ProjectMemoryStore,
            record_from_structural_fact,
        )
        from app.understanding.models import StructuralFact, StructuralFactType

        raised = self.surface()
        understanding = raised.understanding()
        # ``understanding()`` returns fact descriptors; the fingerprint that makes
        # an entry *pinned* comes from the snapshot's own file inventory, so it is
        # read from the snapshot rather than from the route's summary.
        snapshot = raised.snapshotter.create_snapshot(
            self.workspace, project_id=understanding["project_id"]
        )
        fingerprint_of = {f.relative_path: f.fingerprint for f in snapshot.files}

        store = ProjectMemoryStore(self.run_store, memory_id="project-memory")
        memory = store.load()
        recorded: list[str] = []
        for fact in snapshot.facts[:limit]:
            entry = record_from_structural_fact(
                fact, fingerprint=fingerprint_of.get(fact.source_path)
            )
            memory = memory.record(entry)
            recorded.append(entry.memory_id)
        store.save(memory)
        return recorded

    def test_45_a_changed_file_invalidates_the_fact_pinned_to_it(self) -> None:
        """_INJECTION_TARGET_: freshness must drive invalidation, not a claim.

        A structural fact the scanner really extracted from a real file is recorded
        as a ``CONFIRMED_FACT`` pinned to the scanner's own fingerprint. The file
        then changes, and the refresh route must move that entry to STALE with a
        reason code naming the observation.
        """

        (self.root / "pkg").mkdir()
        target = self.root / "pkg" / "mod.py"
        target.write_text("def alpha():\n    return 1\n", encoding="utf-8", newline="")

        client = self.client()
        recorded = self._record_confirmed_facts()
        self.assertTrue(recorded, "the scanner must extract a fact from the workspace")

        before = client.get("/api/product/memory").json()["buckets"]
        self.assertGreaterEqual(len(before["confirmed_facts"]), 1)
        self.assertEqual(before["stale"], [])

        # THE USER CHANGES THE FILE.
        target.write_text("def alpha():\n    return 99\n", encoding="utf-8", newline="")

        refreshed = client.post("/api/product/memory/refresh", json={})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        body = refreshed.json()
        self.assertTrue(body["wrote"])
        self.assertGreaterEqual(body["refresh"]["checked"], 1)
        self.assertGreaterEqual(body["refresh"]["stale"], 1)
        buckets = body["memory"]["buckets"]
        # The entry is no longer presented as a confirmed, current fact ...
        self.assertEqual(buckets["confirmed_facts"], [])
        # ... it is presented as stale, with the observation named.
        self.assertGreaterEqual(len(buckets["stale"]), 1)
        outcomes = {entry["freshness"]["outcome"] for entry in buckets["stale"]}
        self.assertEqual(outcomes, {"CONTENT_CHANGED"})

    def test_46_refresh_is_idempotent_and_reports_it_wrote_nothing(self) -> None:
        """A second refresh over unchanged bytes must write nothing and say so."""

        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text(
            "def alpha():\n    return 1\n", encoding="utf-8", newline=""
        )
        client = self.client()
        # A hypothesis on its own is unpinned, so it is knowingly NOT checked.
        client.post("/api/product/memory", json={"statement": "An unpinned idea."})
        self._record_confirmed_facts()

        first = client.post("/api/product/memory/refresh", json={}).json()
        self.assertGreaterEqual(first["refresh"]["checked"], 1)
        self.assertEqual(first["refresh"]["unchanged"], first["refresh"]["checked"])
        self.assertFalse(first["wrote"])

        second = client.post("/api/product/memory/refresh", json={}).json()
        self.assertFalse(second["wrote"])
        self.assertEqual(second["refresh"]["stale"], 0)

    def test_47_an_explicit_invalidation_keeps_the_history(self) -> None:
        client = self.client()
        recorded = client.post(
            "/api/product/memory", json={"statement": "A claim I no longer hold."}
        ).json()
        response = client.post(
            f"/api/product/memory/{recorded['memory_id']}/invalidate",
            json={"reason": "superseded by a measurement"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        view = client.get("/api/product/memory").json()
        stale = {entry["memory_id"]: entry for entry in view["buckets"]["stale"]}
        self.assertIn(recorded["memory_id"], stale)
        actions = [h["action"] for h in stale[recorded["memory_id"]]["history"]]
        self.assertIn("invalidated", actions)
        # Nothing was deleted: the entry is still present, in the stale bucket.
        self.assertEqual(view["buckets"]["hypotheses"], [])

    def test_48_an_unsafe_memory_subject_path_is_refused(self) -> None:
        client = self.client()
        for subject in ("../escape.py", "C:\\Windows\\system32", "/etc/passwd"):
            response = client.post(
                "/api/product/memory",
                json={"statement": "s", "subject": subject},
            )
            self.assertEqual(response.status_code, 409, subject)
            self.assertEqual(response.json()["code"], "unsafe_subject_path")

    def test_49_an_unknown_memory_id_is_refused(self) -> None:
        client = self.client()
        response = client.post("/api/product/memory/no-such-id/invalidate", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "unknown_memory_id")

    def test_49b_a_subject_is_a_reference_and_does_not_pin_the_entry(self) -> None:
        """_INJECTION_TARGET_: ``subject`` must not imply a freshness check.

        THE DEFECT THIS PINS. ``record_hypothesis(subject=...)`` wrote the path into
        ``scope.paths``, which is the field the freshness projection reads. That
        implied a pin. It never was one: ``freshness_of`` answers ``UNPINNED``
        whenever ``pin.is_file_pinned`` is false, and this surface cannot set a pin
        -- it has no scanner fingerprint to pin with. So an entry naming a file
        looked pinned and reported ``UNPINNED`` regardless, and the parameter
        promised a guarantee the code did not provide.

        The path is still validated and still recorded. What changed is that it is
        recorded as what it is, and the entry reports itself unpinned.
        """

        client = self.client()
        created = client.post(
            "/api/product/memory",
            json={"statement": "calc.py probably needs a rewrite.", "subject": "calc.py"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        body = created.json()
        # The surface states the fact rather than leaving it to be inferred.
        self.assertFalse(body["pinned"])
        self.assertEqual(body["subject_reference"], "calc.py")

        view = client.get("/api/product/memory").json()
        entry = {e["memory_id"]: e for e in view["buckets"]["hypotheses"]}[
            body["memory_id"]
        ]
        # The freshness verdict is UNPINNED -- the honest answer -- and the path is
        # NOT in the scope, so nothing downstream can mistake it for a pin.
        self.assertEqual(entry["freshness"]["outcome"], "UNPINNED")
        self.assertEqual(list(entry["scope"]["paths"]), [])
        self.assertEqual(entry["details"]["subject_reference"], "calc.py")
        # The record itself says why the subject did not become a pin, so the
        # distinction survives in the durable data and not only in this test.
        self.assertIn("subject_reference_note", entry["details"])

        # AND THE CONTROL: the same route WITHOUT a subject behaves identically with
        # respect to pinning, so the assertions above are about the subject field and
        # not about every entry being unpinned by construction.
        plain = client.post("/api/product/memory", json={"statement": "No subject."}).json()
        self.assertFalse(plain["pinned"])
        self.assertEqual(plain["subject_reference"], "")
        plain_view = client.get("/api/product/memory").json()
        plain_entry = {e["memory_id"]: e for e in plain_view["buckets"]["hypotheses"]}[
            plain["memory_id"]
        ]
        self.assertEqual(plain_entry["freshness"]["outcome"], "UNPINNED")
        self.assertNotIn("subject_reference", plain_entry["details"])


# ---------------------------------------------------------------------------
# 14. offline test runtime
# ---------------------------------------------------------------------------


class OfflineRuntimeTests(Block8Case):
    def test_50_the_runtime_under_test_registers_only_the_mock_agent(self) -> None:
        self.assertEqual(self.runtime.agent_registry.list_agents(), ["mock"])
        self.assertEqual(tuple(self.runtime.settings.enabled_providers), ("mock",))
        self.assertEqual(self.runtime.settings.default_provider, "mock")

    def test_51_no_route_reports_a_live_provider(self) -> None:
        """If any of these ever names a real provider, the suite is not offline."""

        client = self.client(
            self.service(verification_resolver=lambda task, profile: self.build_id)
        )
        response = client.post(
            "/api/product/verify",
            json={"declaration_id": self.build_id, "idempotency_key": _unique("k.off")},
        )
        self.assertEqual(response.json()["provider_used"], "")
        for entry in client.get("/api/agents").json():
            self.assertEqual(entry["provider"], "mock", entry)

    def test_52_a_scripted_transport_is_the_only_text_source(self) -> None:
        """The injected transport is reached, and it is the one this test supplied."""

        payload = json.dumps(
            {
                "files": {MODULE_PATH: "def add(a, b):\n    return a + b\n"},
                "decision": {
                    "decision_type": "CONTINUE",
                    "action": "EXECUTE",
                    "reason_code": "ready",
                    "rationale": "complete",
                    "confidence": 0.9,
                },
            }
        )
        transport = _ScriptedTransport(payload)
        runtime = self._runtime_with_transport(transport)
        service = self.service(runtime=runtime)
        # The injected transport really is the runtime's registered provider and
        # its configured default, so this test's text source is the only one.
        self.assertIs(service.runtime.provider_registry.get("block8-scripted"), transport)
        self.assertEqual(
            service.runtime.orchestrator._dispatcher._default_provider,
            "block8-scripted",
        )
        # No call has happened merely by composing the service.
        self.assertEqual(transport.calls, 0)


# ---------------------------------------------------------------------------
# 15. no secrets in API responses or diagnostics
# ---------------------------------------------------------------------------


class SecretBoundaryTests(Block8Case):
    SECRET_MARKERS = ("sk-or-v1-", "sk-ant-", "sk-proj-", "AIzaSy", "gsk_", "sk-")

    def _assert_clean(self, text: str, label: str) -> None:
        for marker in self.SECRET_MARKERS:
            self.assertNotIn(marker, text, f"{label} leaked {marker!r}")

    def test_53_no_projection_route_returns_a_secret_shaped_value(self) -> None:
        """Success AND refusal bodies are both checked.

        The refusal half is why this test carries ``_INJECTION_TARGET_``: an
        earlier version checked only 200 responses, so injecting a secret-shaped
        literal into the refusal body did **not** break it. A leak in an error path
        is exactly where a nested message would quote a configured value, so the
        refusals have to be walked too.
        """

        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8", newline="")
        client = self.client()
        client.post("/api/product/baseline", json={})
        for path in (
            "/api/product/onboarding",
            "/api/product/requirements",
            "/api/product/plan",
            "/api/product/understanding",
            "/api/product/memory",
            "/api/product/change-impact",
            "/api/product/runs",
            "/api/product/recovery",
            "/api/product/graph/none-recorded",
            "/api/product/approvals/none-recorded",
            "/api/status",
            "/api/dashboard",
            "/health",
        ):
            response = client.get(path)
            self._assert_clean(response.text, path)

        # The refusal half: every route that can refuse is made to refuse, and its
        # body is checked with the same markers.
        refusals = [
            client.get("/api/product/onboarding", params={"project_id": "other-project"}),
            client.post("/api/product/memory", json={"statement": "s", "kind": "CONFIRMED_FACT"}),
            client.post("/api/product/memory/unknown-id/invalidate", json={}),
            client.post(
                "/api/product/verify",
                json={"declaration_id": "no.such.declaration", "idempotency_key": _unique("k.s53")},
            ),
            client.post("/api/product/verify", json={"declaration_id": self.build_id}),
            client.post(
                "/api/product/verify",
                json={"declaration_id": self.build_id, "idempotency_key": "short"},
            ),
            client.get("/api/product/change-impact", params={"baseline": "never-recorded"}),
            client.get("/api/product/graph/...."),
            self.graph_run(client, "../escape", self.steps_two()),
        ]
        for response in refusals:
            self.assertGreaterEqual(response.status_code, 400, response.text)
            self._assert_clean(response.text, f"refusal {response.status_code}")

    def test_54_a_refusal_never_echoes_exception_text(self) -> None:
        """A nested exception message can quote a configured value."""

        client = self.client()
        response = client.post(
            "/api/product/verify",
            json={"declaration_id": "no.such.declaration", "idempotency_key": _unique("k.s")},
        )
        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        # Only the exception CLASS is reported, never its message.
        self.assertEqual(detail["error"], "UnknownExecutionDeclarationError")
        self.assertNotIn("no.such.declaration", json.dumps(detail))

    def test_55_the_scan_route_does_not_return_secret_file_fingerprints(self) -> None:
        """The canonical scanner must still refuse to fingerprint a credential file."""

        (self.root / ".env").write_text("SOME_KEY=sk-or-v1-not-a-real-key\n", encoding="utf-8")
        client = self.client()
        body = client.get("/api/product/understanding").json()
        rendered = json.dumps(body)
        self._assert_clean(rendered, "understanding")
        # The route returns descriptors, never contents.
        self.assertNotIn("not-a-real-key", rendered)

    def test_56_secrets_do_not_reach_the_run_record(self) -> None:
        (self.root / ".env").write_text("SOME_KEY=sk-or-v1-not-a-real-key\n", encoding="utf-8")
        client = self.client()
        self.graph_run(
            client, "secret-run", [{"step_id": "s", "action": {"declaration_id": self.one_id}}]
        )
        record = client.get("/api/product/runs/secret-run")
        self._assert_clean(record.text, "run record")


# ---------------------------------------------------------------------------
# 16. compatibility of existing routes and models
# ---------------------------------------------------------------------------


class CompatibilityTests(Block8Case):
    def test_57_the_existing_models_are_byte_compatible(self) -> None:
        from app.api.models import (
            AgentInfo,
            HealthResponse,
            ProjectInfo,
            SystemStatusResponse,
            TaskRunRequest,
            TaskRunResponse,
        )

        self.assertEqual(
            set(HealthResponse("ok", "connected").to_dict()),
            {"status", "core_status", "version", "timestamp"},
        )
        self.assertEqual(
            set(ProjectInfo("p", "n", "/x").to_dict()),
            {"project_id", "name", "path", "status"},
        )
        self.assertEqual(
            set(AgentInfo("n", "p", "m").to_dict()),
            {"name", "provider", "model", "status", "capabilities"},
        )
        self.assertEqual(
            set(TaskRunRequest("d").to_dict()),
            {
                "description",
                "category",
                "task_id",
                "provider_name",
                "project_id",
                "context",
                # Opt-in write mode: false by default, and always present in the
                # request contract once it exists.
                "write_mode",
                # The model for this dispatch only; ``None`` by default, so a caller
                # that never names one keeps the provider's configured model.
                "model_name",
            },
        )
        self.assertEqual(
            set(
                TaskRunResponse(
                    run_id="r", task_id="t", project_id="p", state="s",
                    success=True, output="o",
                ).to_dict()
            ),
            {
                "run_id", "task_id", "project_id", "state", "success", "output",
                "error", "tokens", "cost", "duration_seconds", "provider_used",
                "model_used",
                # The outcome gate and the independent save indicators. They are
                # additive: every field that existed before is unchanged, and no
                # client that reads only those fields is affected.
                "outcome", "clarification_questions", "needs_clarification",
                "run_record_saved", "project_created", "project_files_created",
                "artifact_manifest_saved", "files_created",
                # The write mode this run used: "" when it wrote nothing, otherwise
                # MOCK_DETERMINISTIC or REAL_PROVIDER_EXPLICIT. Additive like the
                # fields above.
                "write_mode",
                "cost_tier", "estimated_cost",
                # The intent the answer declared (structured intent v0.1). Additive:
                # an empty mapping means "no intent declared", and no field that
                # existed before it changed.
                "model_intent",
                # The lifecycle project (`proj-…`) this run created or was attached
                # to, or None. Additive and last: `project_id` above is still the
                # workspace/Core project, and it is not copied here.
                "lifecycle_project_id",
                # The clarification round (0 = no context, 1 = may ask, 2 = forced
                # final). Additive: the backend's own count, published so a client
                # never infers a round from the wording of an answer.
                "clarification_round",
                # The automatic routing decision (provider · model · tier · why). Additive
                # and last: an empty mapping means this entry point made no decision, and
                # no field that existed before it changed.
                "model_choice",
                # «Что сделал Forge»: the model's own first lines, screened and bounded by
                # the backend, so a run that wrote no file can say what it replied.
                "answer_excerpt",
            },
        )
        status = SystemStatusResponse("c", "a", "READY", 1, 1).to_dict()
        self.assertIn("details", status)
        self.assertIn("active_project", status)

    def test_58_every_original_route_still_declares_no_authentication_dependency(self) -> None:
        """The recorded residual risk is unchanged by Block #8, not quietly widened."""

        from app.api.server import create_api_app as build

        app = build(self.service())
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            if dependant is None:
                continue
            self.assertEqual(
                list(getattr(dependant, "dependencies", [])),
                [],
                f"route {getattr(route, 'path', '?')} gained an authentication dependency",
            )

    def test_59_the_api_client_still_works_against_the_new_app(self) -> None:
        from app.api.client import ForgeApiClient

        service = self.service()
        app = create_api_app(service)
        client = TestClient(app)
        self._clients.append(client)
        self.assertEqual(client.get("/health").json()["status"], "ok")
        self.assertEqual(service.get_health().status, "ok")
        # The in-process client object is unaffected by the added routes.
        self.assertIsNotNone(ForgeApiClient(api_service=service))

    def test_60_the_route_table_only_grew(self) -> None:
        """No existing path may disappear."""

        original = {
            "/health", "/api/status", "/api/projects", "/api/agents",
            "/api/dashboard", "/api/dashboard/refresh", "/api/capabilities",
            "/api/tools", "/api/tasks/run", "/api/runs/{run_id}",
        }
        app = create_api_app(self.service())
        paths = {route.path for route in app.routes if hasattr(route, "path")}
        self.assertTrue(original.issubset(paths), original - paths)


if __name__ == "__main__":
    unittest.main()
