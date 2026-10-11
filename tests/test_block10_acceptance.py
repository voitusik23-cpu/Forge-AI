"""Block #10 acceptance: structured HTTP errors and unambiguous execution semantics.

Covers the two defects measured live before the fix:
  * graph route without the mandatory idempotency guard -> 409 with an EMPTY body
  * invalid step schema (no nested ``action``)         -> 500 with an EMPTY body
and the ambiguity that made a refusal read like a completed command:
``executed: true`` alongside ``status: REFUSED`` / step ``WAITING_APPROVAL``.

Everything runs offline: create_runtime(test_mode=True) registers only the mock
provider. No provider call, no repository write, no shared RunStore root.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest

REPO = pathlib.Path(r"C:\Users\User\Desktop\Forge-AI")
sys.path.insert(0, str(REPO))

# Redirect the durable run store BEFORE any service is built. ``RunStore()`` with no
# root resolves to a per-user location (%LOCALAPPDATA%\ForgeAI\runs) that holds real,
# durable run history -- 51566 entries on this machine at the time of writing. Sharing
# it made this file order- and history-dependent: a ``graph_run_id`` used by an earlier
# run was found there, so a brand-new workspace reported ``replayed: true``,
# ``executed: false`` and no artifact. ``FORGE_RUN_STORE_ROOT`` is the documented
# redirect for exactly this (see ``app.runtime.run_store.default_run_store_root``).
_ISOLATED_RUN_STORE = tempfile.mkdtemp(prefix="forge_b10_runstore_")
os.environ["FORGE_RUN_STORE_ROOT"] = _ISOLATED_RUN_STORE

from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.agent_runtime.idempotency_integration import RunIdempotencyGuard
from app.api.server import create_api_app
from app.api.service import ForgeApiService
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.runtime.bootstrap import create_runtime
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, InMemoryApprovalResolver
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace

DID = "block10.declared.write"
CID = "artifact-present"
ARTIFACT = "b10_probe.txt"


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        # Release any claim leaked by a previous test in this process. Without this the
        # file is order-dependent: a scope left behind by a refusal/waiting test makes
        # the NEXT execution return without running the command, so a later test sees
        # executed=false and a missing artifact. The canonical example
        # (test_acceptance_production.py:106,113) does exactly this.
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory(prefix="forge_b10_test_")
        self.root = pathlib.Path(self._tmp.name)
        (self.root / "seed.txt").write_text("seed", encoding="utf-8")
        self.profile = ProjectExecutionProfile(
            profile_id="api-default",
            allowed_commands=("python",),
            capabilities=frozenset({ExecutionCapability.INTERPRET_TEXT}),
            network_access=False,
            working_directory=".",
        )
        self.declarations = {
            DID: ExecutionDeclaration(
                declaration_id=DID,
                command=("python", "-c", f"open('{ARTIFACT}','w').write('x')"),
                profile_id="api-default",
                working_directory=".",
            )
        }
        self.specs = {
            DID: AcceptanceSpec(
                declaration_id=DID,
                criteria=(
                    AcceptanceCriterion(
                        criterion_id=CID, description="artifact produced", requirement_id="req-b10"
                    ),
                ),
                expectations={CID: VerificationExpectation(ARTIFACT, True)},
            )
        }

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _service(self, *, guard: bool, policy=None, resolver=None) -> ForgeApiService:
        kwargs = dict(
            runtime=create_runtime(test_mode=True),
            workspace=Workspace(self.root),
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=self.profile,
            declarations=self.declarations,
            acceptance_specs=self.specs,
            approval_policy=policy if policy is not None else ApprovalPolicy(),
        )
        if resolver is not None:
            kwargs["approval_resolver"] = resolver
        if guard:
            kwargs["idempotency_guard"] = RunIdempotencyGuard.create(
                ledger_root=self.root / "idempotency"
            )
        return ForgeApiService(**kwargs)

    def _client(self, **kw):
        from fastapi.testclient import TestClient

        return TestClient(create_api_app(service=self._service(**kw)))

    @staticmethod
    def _step(declaration_id: str = DID) -> dict:
        return {
            "step_id": "s1",
            "action": {"declaration_id": declaration_id, "parameters": []},
            "depends_on": [],
        }

    def _post(self, client, run_id: str, steps):
        return client.post(
            "/api/product/graph",
            json={"graph_run_id": run_id, "graph_id": "b10-plan", "steps": steps},
        )


class HttpDiagnosticsTests(_Base):
    """The two empty-body defects measured live before the fix."""

    def test_01_missing_required_dependency_is_structured_not_empty(self):
        """No idempotency guard => 409 with a STRUCTURED body, never an empty one."""
        client = self._client(guard=False)
        resp = self._post(client, "b10-no-guard", [self._step()])

        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertTrue(resp.text.strip(), "body must not be empty")
        body = resp.json()
        self.assertEqual(body.get("error_code"), "missing_required_dependency")
        self.assertIs(body.get("executed"), False)
        self.assertTrue(body.get("message"))
        self.assertTrue(body.get("refused"))

    def test_02_invalid_step_schema_is_400_not_500_and_not_empty(self):
        """A malformed step is a REQUEST error (400), not an internal failure (500)."""
        client = self._client(guard=True)
        # Deliberately missing the nested "action" mapping.
        bad = [{"step_id": "s1", "declaration_id": DID, "depends_on": []}]
        resp = self._post(client, "b10-bad-schema", bad)

        self.assertEqual(resp.status_code, 400, resp.text)
        self.assertTrue(resp.text.strip(), "body must not be empty")
        payload = resp.json()
        inner = payload.get("detail", payload)
        self.assertEqual(inner.get("error_code"), "invalid_request_schema")
        self.assertIs(inner.get("executed"), False)

    def test_03_diagnostics_do_not_leak_paths_or_tracebacks(self):
        """Safe messages only: no traceback, no absolute temp path, no secrets."""
        client = self._client(guard=True)
        for run_id, steps in (
            ("b10-leak-a", [{"step_id": "s1", "declaration_id": DID, "depends_on": []}]),
            ("b10-leak-b", [{"step_id": "s1", "action": {}, "depends_on": []}]),
        ):
            with self.subTest(run_id=run_id):
                resp = self._post(client, run_id, steps)
                raw = resp.text
                self.assertNotIn("Traceback", raw)
                self.assertNotIn(str(self.root), raw)
                self.assertNotIn("File \"", raw)

    def test_04_refusal_creates_no_side_effect(self):
        """A refused graph must not produce the declared artifact."""
        client = self._client(guard=False)
        self._post(client, "b10-no-effect", [self._step()])
        self.assertFalse(
            (self.root / ARTIFACT).exists(),
            "a refused graph must not create the declared artifact",
        )


class ExecutedSemanticsTests(_Base):
    """`executed` must never read as "the command ran" when it did not."""

    def test_10_success_reports_command_started_and_runs_for_real(self):
        client = self._client(guard=True)
        resp = self._post(client, "b10-ok", [self._step()])

        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIs(body.get("executed"), True)
        self.assertIs(body.get("command_started"), True)
        self.assertEqual(body.get("commands_started"), 1)
        self.assertTrue(
            (self.root / ARTIFACT).exists(),
            "command_started:true without the artifact on disk is a false success",
        )

    def test_11_waiting_for_approval_does_not_claim_the_command_started(self):
        """The original defect: executed/command_started true while WAITING_APPROVAL."""
        policy = ApprovalPolicy(approval_required_tools=("python",))
        client = self._client(guard=True, policy=policy, resolver=InMemoryApprovalResolver())
        resp = self._post(client, "b10-waiting", [self._step()])

        body = resp.json()
        result = body.get("result", body)
        steps = result.get("steps") or []
        statuses = [s.get("status") for s in steps]
        self.assertIn("WAITING_APPROVAL", statuses, body)

        # The command must not be claimed as started anywhere in the payload.
        self.assertIs(body.get("command_started"), False, body)
        self.assertEqual(body.get("commands_started", 0), 0, body)
        self.assertFalse(
            (self.root / ARTIFACT).exists(),
            "WAITING_APPROVAL must not create the declared artifact",
        )

    def test_12_replay_does_not_start_the_command_again(self):
        client = self._client(guard=True)
        first = self._post(client, "b10-replay", [self._step()])
        self.assertEqual(first.status_code, 200, first.text)

        artifact = self.root / ARTIFACT
        before = artifact.stat().st_mtime_ns

        second = self._post(client, "b10-replay", [self._step()])
        self.assertEqual(second.status_code, 200, second.text)
        body = second.json()
        self.assertIs(body.get("replayed"), True, body)
        self.assertIs(body.get("executed"), False, body)
        self.assertEqual(body.get("commands_started", 0), 0, body)
        self.assertEqual(
            artifact.stat().st_mtime_ns, before, "replay must not rewrite the artifact"
        )


if __name__ == "__main__":
    unittest.main()
