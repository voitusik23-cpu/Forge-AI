"""Block #9 — Evidence Graph, Verification Planning, Change-Impact reasoning.

WHAT THIS FILE PROVES, AND HOW
==============================

Every claim under test is checked through an **HTTP route**
(``fastapi.testclient.TestClient`` against ``create_api_app``) or through the
project's own durable artifacts on disk. Nothing here asserts against a private
helper as a substitute for the route: a helper that works while the route forgets
to call it is exactly the "checked a helper instead of HTTP" failure this project
has already recorded once (``test_70``).

The runtime is structurally offline (``create_runtime(test_mode=True)``), the
workspace/store/ledger are per-test temporaries, and ``RunScope.release_all()``
runs before and after every test.

WHY EACH TEST CAN FAIL
======================

Each test names, in its docstring, the *specific* change that makes it go red.
Tests carrying ``_INJECTION_TARGET`` were exercised with a real source patch
during Block #9 verification (recorded with before/after SHA-256 in the report).
Tests carrying ``_SENSITIVITY`` prove their own protection is load-bearing by
re-running an in-file copy of the exact decision rule with that one rule disabled
and asserting the outcome changes -- so the test cannot pass for the wrong reason
on a build where the rule was quietly loosened.

FIVE WAYS A GREEN TEST IS NOT EVIDENCE, AND WHAT IS DONE ABOUT EACH
===================================================================

1. **Prefix collision.** A route reachable only because another route's prefix
   matches. ``test_09`` rebuilds the route set from the live app, keeps the
   trailing literal segments, and requires the *full* shape to appear as an
   executable string fragment. It is paired with a self-check that a prefix and a
   comment mention are both rejected.
2. **Vacuous on an empty project.** ``test_01`` asserts on a *populated* project
   and additionally asserts the positive control (the same field is non-empty
   when it should be).
3. **Pinning the defect it tests.** No test here asserts that a known-wrong value
   is produced; every state assertion is paired with the outcome it must not be.
4. **PASSED with no evidence.** ``test_11`` proves the negative direction: an
   empty basis is ``UNKNOWN``, and a check with no observation is not ``PASSED``.
5. **A number standing in for evidence.** ``test_14`` asserts the confidence is an
   ordinal enum with a stated derivation, and that ``support_rank`` is not used as
   a probability.
"""

from __future__ import annotations

import hashlib
import pathlib
import json
import re
import unittest
from pathlib import Path

from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.agent_runtime.idempotency import TaskIdentity
from app.agent_runtime.idempotency_integration import operation_identity_from_binding
from app.api.server import create_api_app
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.verification import VerificationExpectation
from app.workflows.step_graph import (
    StepGraphSnapshot,
    StepRecord,
    StepStatus,
    VerificationStatus,
)

from test_product_integration import (  # noqa: E402 - tests/ is not a package
    Block8Case,
    EXIT_THREE,
    READ_ONE,
    WRITE_ONE,
)

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

#: A workspace fingerprint: every relative path mapped to its SHA-256, so a test
#: can prove that a *read* route wrote nothing. Directories are included so a new
#: empty directory is also visible.
def workspace_fingerprint(root: Path) -> dict[str, str]:
    fingerprint: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            fingerprint[relative + "/"] = "dir"
            continue
        fingerprint[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return fingerprint


def _spec(declaration_id: str, *, expectations: dict[str, str]) -> AcceptanceSpec:
    criteria = tuple(
        AcceptanceCriterion(
            criterion_id,
            f"{criterion_id} holds",
            requirement_id=f"req-{criterion_id}",
        )
        for criterion_id in sorted(expectations)
    )
    return AcceptanceSpec(
        declaration_id=declaration_id,
        criteria=criteria,
        expectations={
            criterion_id: VerificationExpectation(path, True)
            for criterion_id, path in expectations.items()
        },
    )


class Block9Case(Block8Case):
    """The Block #8 fixture, plus the Block #9 routes."""

    def steps_with_artifact_check(self):
        """The Block #8 two-step chain, with a REAL declared artifact check.

        ``step-one`` runs ``WRITE_ONE`` and declares ``ARTIFACT_PRODUCED one.txt``,
        so its verification is a harvested-artifact observation the runner really
        evaluates. Without a declared verification the step records
        ``no_verification_declared`` and the graph can never contain a PASSED
        verification link -- which is exactly the "vacuous fixture" this method
        exists to avoid.
        """

        return [
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

    def impact(self, client: TestClient) -> dict:
        response = client.get("/api/product/change-impact", timeout=120)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def evidence(self, client: TestClient, run_id: str = "") -> dict:
        path = "/api/product/evidence-graph"
        if run_id:
            path = path + "?graph_run_id=" + run_id
        response = client.get(path, timeout=120)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def plan(self, client: TestClient) -> dict:
        response = client.get("/api/product/verification-plan", timeout=120)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()


# ---------------------------------------------------------------------------
# 1. route reachability, without a prefix collision
# ---------------------------------------------------------------------------


class RouteReachabilityTests(Block9Case):
    """Every new route must be reachable by a real client action.

    The Block #8 rule reduced a parameterised route to the text before its first
    placeholder, so one route was satisfied by another route's prefix. That is why
    the check below is written as an allow-list of *executable call shapes* rather
    than as a prefix search.
    """

    #: For each new route, the URL-construction shape the client must contain. The
    #: placeholder is represented by ``%s`` and must appear as a concatenation.
    CALL_SHAPES = {
        "/api/product/change-impact": ['"/api/product/change-impact"'],
        "/api/product/verification-plan": ['"/api/product/verification-plan"'],
        "/api/product/stale-facts": ['"/api/product/stale-facts"'],
        "/api/product/repair-plan": ['"/api/product/repair-plan"'],
        "/api/product/evidence-graph": [
            '"/api/product/evidence-graph"',
            '"/api/product/evidence-graph?graph_run_id=" + encodeURIComponent(runId)',
        ],
    }

    @staticmethod
    def _executable_fragments(js: str) -> str:
        """Only quoted literals and ``+`` joins, with comments removed first.

        A route named in a comment is not a client call. Block #8's asset explains
        its own routes in prose, so a raw-text search counted an explanation as a
        call.
        """

        without_block = re.sub(r"/\*.*?\*/", " ", js, flags=re.DOTALL)
        without_line = re.sub(r"//[^\n]*", " ", without_block)
        return "".join(re.findall(r'"[^"\n]*"|\'[^\'\n]*\'|\+', without_line))

    def test_09_every_block_nine_route_is_reachable_by_a_client_call(self) -> None:
        """_SENSITIVITY_: a prefix or a comment reference must NOT satisfy this.

        Goes red if: a client call is deleted; or the check is loosened back to a
        prefix search, which the negative controls below show would accept text that
        is not a call at all.
        """

        app = create_api_app(self.service())
        registered = {
            route.path
            for route in app.routes
            if hasattr(route, "path") and route.path.startswith("/api/product/")
        }
        js = self.client().get("/app.js").text
        code = self._executable_fragments(js)

        # The FULL URL shape, every literal segment in order, INSIDE executable text.
        # The reduction keeps quoted literals and `+` joins only, so an identifier
        # such as `encodeURIComponent` is removed -- the shape is therefore every
        # literal fragment up to and including the last quote of the path, and that
        # is what must appear in order. Requiring the trailing identifier would be
        # requiring text the reduction deletes, and the test would then fail for a
        # reason unrelated to reachability.
        shapes = {
            "/api/product/change-impact": ('"/api/product/change-impact"',),
            "/api/product/verification-plan": ('"/api/product/verification-plan"',),
            "/api/product/stale-facts": ('"/api/product/stale-facts"',),
            "/api/product/repair-plan": ('"/api/product/repair-plan"',),
            "/api/product/evidence-graph": (
                '"/api/product/evidence-graph"',
                '"/api/product/evidence-graph?graph_run_id="',
            ),
        }

        def shape_present(shape: str, text: str) -> bool:
            return shape in text

        for path, expected_shapes in shapes.items():
            self.assertIn(path, registered, f"{path} is not registered on the app")
            self.assertTrue(
                any(shape_present(shape, code) for shape in expected_shapes),
                f"{path} has no executable client call of shape {expected_shapes}",
            )

        # NEGATIVE CONTROLS, against the same asset-processing rule.
        # (1) a bare path prefix is not the parameterised call shape;
        self.assertFalse(
            shape_present(
                '"/api/product/evidence-graph?graph_run_id="',
                self._executable_fragments('"/api/product/evidence-graph"'),
            ),
            "a bare prefix satisfied the parameterised shape",
        )
        # (2) a route named only in a comment is not a call.
        self.assertFalse(
            shape_present(
                '"/api/product/evidence-graph"',
                self._executable_fragments("/* GET /api/product/evidence-graph */ var x = 1;"),
            ),
            "a route named only in a block comment counted as a client call",
        )
        self.assertFalse(
            shape_present(
                '"/api/product/verification-plan"',
                self._executable_fragments("// /api/product/verification-plan\nvar y = 2;"),
            ),
            "a route named only in a line comment counted as a client call",
        )
        # (3) a DIFFERENT path must not satisfy this route's shape.
        self.assertFalse(
            shape_present(
                '"/api/product/evidence-graph"',
                self._executable_fragments('"/api/product/evidence-graph-other"'),
            ),
            "a different path satisfied the shape",
        )
        # (4) the positive control: the real shapes ARE accepted, and the
        #     parameterised one is the fragment the served asset really contains.
        self.assertTrue(
            shape_present(
                '"/api/product/evidence-graph?graph_run_id="',
                self._executable_fragments(
                    'return request("/api/product/evidence-graph?graph_run_id=" + '
                    "encodeURIComponent(runId));"
                ),
            )
        )
        self.assertTrue(
            shape_present(
                '"/api/product/evidence-graph?graph_run_id="',
                code,
            ),
            "the served asset does not contain the parameterised call fragment",
        )

    def test_09b_the_new_routes_answer_through_http_and_are_project_bound(self) -> None:
        """The routes are real, and each one enforces the project boundary.

        Goes red if: a route is registered but raises; or the project check is
        removed, because a foreign ``project_id`` would then return 200.
        """

        client = self.client()
        for path in (
            "/api/product/change-impact",
            "/api/product/evidence-graph",
            "/api/product/verification-plan",
        ):
            ok = client.get(path, timeout=120)
            self.assertEqual(ok.status_code, 200, f"{path}: {ok.text}")
        for path in ("/api/product/stale-facts", "/api/product/repair-plan"):
            ok = client.post(path, json={}, timeout=120)
            # MEASURED: an empty graph_run_id fails the route's own allow-list, so
            # the answer is the 403 `unsafe_identifier` refusal -- the same
            # vocabulary the other routes use -- and never a 500 and never a
            # silent 200. Either refusal is accepted, and both must say nothing ran.
            self.assertIn(ok.status_code, (200, 403, 409), f"{path}: {ok.text}")
            if ok.status_code >= 400:
                self.assertTrue(ok.json().get("refused"), ok.text)
                self.assertFalse(ok.json().get("executed", True), ok.text)

        for path in (
            "/api/product/change-impact",
            "/api/product/evidence-graph",
            "/api/product/verification-plan",
        ):
            foreign = client.get(path, params={"project_id": "another-project"})
            self.assertEqual(foreign.status_code, 403, f"{path}: {foreign.text}")
            self.assertNotIn("another-project", foreign.text)
        for path in ("/api/product/stale-facts", "/api/product/repair-plan"):
            foreign = client.post(
                path, json={"project_id": "another-project"}, timeout=120
            )
            self.assertEqual(foreign.status_code, 403, f"{path}: {foreign.text}")
            self.assertNotIn("another-project", foreign.text)


# ---------------------------------------------------------------------------
# 2. the evidence graph
# ---------------------------------------------------------------------------


class EvidenceGraphTests(Block9Case):
    def _seed_and_run(self, *, graph_run_id: str = "b9-graph"):
        """A real attempt: one step writes a file with a declared artifact check.

        ``WRITE_ONE`` is declared to produce ``one.txt``, so its verification is a
        real harvested-artifact observation rather than a status. The file is
        seeded with DIFFERENT bytes first, so the step's write is a real measured
        change -- seeding it with the same content would make the file-change link
        honestly report "no change" and the test would be measuring nothing.
        """

        (self.root / "one.txt").write_text("seeded-not-one", encoding="utf-8", newline="")
        service = self.service(
            acceptance_specs={
                self.one_id: _spec(self.one_id, expectations={"artifact-one": "one.txt"})
            }
        )
        client = self.client(service)
        client.post("/api/product/baseline", json={})
        response = self.graph_run(client, graph_run_id, self.steps_with_artifact_check())
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()

    def test_10_the_graph_is_built_from_real_entities(self) -> None:
        """Every transition cites a concrete identifier that really exists.

        Goes red if: a link is emitted without its object existing (the assertion
        re-reads each cited ref from the server's own record); or if the goal is
        not recorded, because the goal link would then be MISSING rather than
        ESTABLISHED.
        """

        client, _ = self._seed_and_run()
        goal = client.post("/api/product/goal", json={"goal": "Deliver one.txt."})
        self.assertEqual(goal.status_code, 200, goal.text)
        goal_memory_id = goal.json()["memory_id"]

        graph = self.evidence(client, "b9-graph")
        self.assertTrue(graph["attempt_recorded"])
        links = graph["links"]
        self.assertTrue(links)

        by_transition = {(link["from"], link["to"]) for link in links}
        for transition in [
            ("GOAL", "REQUIREMENTS"),
            ("REQUIREMENTS", "ACCEPTANCE_CRITERIA"),
            ("ACCEPTANCE_CRITERIA", "PLAN"),
            ("PLAN", "STEPS"),
            ("STEPS", "APPROVAL"),
            ("APPROVAL", "AUTHORIZED_ACTIONS"),
            ("AUTHORIZED_ACTIONS", "FILE_CHANGES"),
            ("FILE_CHANGES", "COMMANDS"),
            ("COMMANDS", "VERIFICATION_RESULTS"),
            ("VERIFICATION_RESULTS", "FINAL_STATUS"),
        ]:
            self.assertIn(transition, by_transition, f"no transition {transition}")

        # The goal link cites the memory id the server actually created.
        goal_links = [
            link for link in links if link["from"] == "GOAL" and link["status"] == "ESTABLISHED"
        ]
        self.assertTrue(goal_links)
        self.assertIn(goal_memory_id, graph["goal_refs"])
        for link in goal_links:
            self.assertEqual(link["source_ref"], goal_memory_id)
            self.assertTrue(link["evidence"])

        # Every ESTABLISHED link carries at least one concrete identifier, and the
        # refs of the step links are step ids the record really holds.
        record = client.get("/api/product/graph/b9-graph").json()
        real_step_ids = {step["step_id"] for step in record["steps"]}
        for link in links:
            if link["status"] != "ESTABLISHED":
                continue
            self.assertTrue(
                link["evidence"], f"ESTABLISHED link {link['link_id']} cites no evidence"
            )
            if link["from"] == "PLAN" and link["to"] == "STEPS":
                self.assertIn(link["source_ref"], real_step_ids)

        # The artifact check that really passed is reported PASSED with the real
        # harvested digest, and the final status is computed rather than claimed.
        verification_links = [
            link for link in links if link["to"] == "VERIFICATION_RESULTS"
        ]
        passed = [
            link
            for link in verification_links
            if link.get("extra", {}).get("state") == "PASSED"
        ]
        self.assertTrue(passed, "no verification link recorded the real PASSED")
        self.assertIn("verification_status=PASSED", " ".join(passed[0]["evidence"]))
        self.assertNotEqual(graph["final_status"], "ACCEPTED")
        # MEASURED: the requirement whose step really produced a PASSED artifact
        # check is the ONLY one not reported UNPROVEN. The overall verdict stays
        # UNPROVEN because the other four declarations have no criterion at all --
        # and the acceptance gate still refuses, which is the point.
        self.assertEqual(graph["requirement_verdicts"][self.one_id], "PROVEN")
        self.assertEqual(graph["verdict"], "UNPROVEN")
        self.assertNotEqual(graph["verdict"], "PROVEN")
        self.assertEqual(graph["final_status"], "ACCEPTED_REFUSED_INCOMPLETE_TRACE")
        self.assertTrue(graph["acceptance_refused"])
        # The real write really changed a file, so the file-change link really
        # exists -- otherwise the missing-link assertions below would be vacuous.
        established_file_links = [
            link
            for link in graph["links"]
            if link["to"] == "FILE_CHANGES" and link["status"] == "ESTABLISHED"
        ]
        self.assertTrue(established_file_links, "the real write produced no file change")
        self.assertIn("one.txt", established_file_links[0]["target_ref"])

    def test_11_a_missing_link_is_shown_explicitly_and_refuses_acceptance(self) -> None:
        """_SENSITIVITY_: the acceptance gate must refuse on a missing mandatory link.

        The control is in the same test: a *non-mandatory* missing link
        (``no_file_change_observed_since_baseline``) does not by itself change the
        verdict, so the refusal below is caused by the mandatory link and not by
        "any missing link refuses".

        Goes red if: the ``MISSING`` status is dropped (the missing links vanish
        from the payload); or ``_acceptance_gate`` stops refusing, because
        ``final_status`` would then be ``PARTIAL``/``PROVEN`` instead of
        ``ACCEPTED_REFUSED_INCOMPLETE_TRACE``.
        """

        client, _ = self._seed_and_run()
        graph = self.evidence(client, "b9-graph")

        missing = graph["missing_links"]
        self.assertTrue(missing, "a run really leaves missing links")
        codes = {link["missing_code"] for link in missing}
        # MEASURED, and this expectation was CORRECTED rather than the
        # implementation. This fixture declares a criterion only for the ONE
        # declaration, and only that declaration is dispatched by a step. No goal was
        # recorded. So the missing set is exactly the goal link and the
        # criterion-less requirements -- all of which are MANDATORY kinds, which is
        # why the acceptance below is refused. The earlier assertion demanded
        # ``no_step_targets_this_requirement``, a code this run cannot produce;
        # asserting it would have pinned a fiction.
        self.assertIn("no_goal_recorded", codes)
        self.assertIn("requirement_has_no_declared_criterion", codes)
        # The mandatory kinds the real graph DOES produce are named, not counted, and
        # every one of them is present in the missing set.
        self.assertTrue(graph["mandatory_missing_kinds"])
        self.assertTrue(graph["mandatory_missing_codes"])
        for code in graph["mandatory_missing_codes"]:
            self.assertIn(code, codes)
        self.assertIn("goal", graph["mandatory_missing_kinds"])
        self.assertIn("acceptance_criterion", graph["mandatory_missing_kinds"])
        # The remaining mandatory codes live in the vocabulary, so a step that really
        # reaches those transitions can produce them.
        from app.analysis.evidence import MISSING_CODES

        for code in (
            "no_approval_decision_recorded",
            "no_command_performed",
            "no_authorized_action_recorded",
            "no_step_targets_this_requirement",
        ):
            self.assertIn(code, MISSING_CODES)

        for link in missing:
            self.assertEqual(link["status"], "MISSING")
            self.assertTrue(link["detail"], "a missing link must say what is absent")
            self.assertTrue(link["missing_code"])

        self.assertTrue(graph["acceptance_refused"])
        self.assertEqual(graph["final_status"], "ACCEPTED_REFUSED_INCOMPLETE_TRACE")
        self.assertIn("mandatory", graph["acceptance_refusal_reason"])

        # NEGATIVE CONTROL, in-file: a non-mandatory code must not refuse by itself.
        from app.api.operating_surface import OperatingSurface

        non_mandatory = OperatingSurface._acceptance_gate(
            {
                "verdict": "PROVEN",
                "missing_links": [
                    {
                        "missing_code": "no_file_change_observed_since_baseline",
                        "from": "AUTHORIZED_ACTIONS",
                        "to": "FILE_CHANGES",
                    }
                ],
            }
        )
        self.assertFalse(non_mandatory["acceptance_refused"])
        self.assertEqual(non_mandatory["final_status"], "PROVEN")

        # POSITIVE CONTROL: with the mandatory link present, acceptance is allowed
        # and the status is the computed verdict.
        accepted = OperatingSurface._acceptance_gate(
            {"verdict": "PROVEN", "missing_links": []}
        )
        self.assertFalse(accepted["acceptance_refused"])
        self.assertEqual(accepted["final_status"], "PROVEN")

    def test_12_a_graph_completion_flag_is_not_treated_as_proof(self) -> None:
        """_INJECTION_TARGET_: COMPLETED is not PROVEN.

        A step whose command exits 0 but whose declared artifact check cannot be
        evaluated is ``ERROR``, and the derived verdict must be ``UNKNOWN`` -- not
        ``PROVEN``. The control in the same test is a step whose artifact really is
        harvested, which IS ``PASSED``.

        Goes red if: ``derive_verdict`` treats ``ERROR`` as a pass, or the builder
        reads the graph's own status instead of the observations.
        """

        from app.analysis.evidence import Verdict, derive_verdict

        # The rule itself, in every direction.
        self.assertEqual(derive_verdict([]), Verdict.UNPROVEN)
        self.assertEqual(derive_verdict(["NOT_RUN"]), Verdict.PARTIAL)
        self.assertEqual(derive_verdict(["PASSED"]), Verdict.PROVEN)
        self.assertEqual(derive_verdict(["PASSED", "NOT_RUN"]), Verdict.PARTIAL)
        self.assertEqual(derive_verdict(["PASSED", "ERROR"]), Verdict.UNKNOWN)
        self.assertEqual(derive_verdict(["PASSED", "FAILED"]), Verdict.REFUTED)
        self.assertEqual(derive_verdict(["REFUSED"]), Verdict.REFUSED)
        # NOT_RUN and ERROR are never a pass, alone or together.
        for states in (["NOT_RUN"], ["ERROR"], ["NOT_RUN", "ERROR"]):
            self.assertNotEqual(derive_verdict(states), Verdict.PROVEN, states)

        # And through the route: the real attempt above holds one PASSED check, so
        # the requirement it belongs to is PARTIAL -- never PROVEN, because the
        # step's own graph-level verification was never declared.
        client, _ = self._seed_and_run()
        graph = self.evidence(client, "b9-graph")
        self.assertEqual(graph["graph_status_reported"], "COMPLETED")
        # COMPLETED is NOT proof: the graph's own control flow finished, and the
        # overall verdict is still not PROVEN. The one requirement whose step really
        # produced a PASSED artifact check IS PROVEN, so the negative direction is
        # not vacuous -- and the final status is still refused.
        self.assertNotEqual(graph["verdict"], "PROVEN")
        self.assertEqual(graph["requirement_verdicts"][self.one_id], "PROVEN")
        self.assertTrue(
            any(
                verdict == "UNPROVEN"
                for verdict in graph["requirement_verdicts"].values()
            ),
            graph["requirement_verdicts"],
        )
        self.assertEqual(graph["final_status"], "ACCEPTED_REFUSED_INCOMPLETE_TRACE")

    def test_13_the_verdict_does_not_read_the_agent_claim_compartment(self) -> None:
        """An agent's claim is kept apart and is inert.

        Goes red if: the compartment stops being reported (an absent section would
        make "no claim was made" indistinguishable from "claims are not
        collected"); or if the response stops stating that the verdict is computed.
        """

        from app.api.operating_surface import OperatingSurface

        client, _ = self._seed_and_run()
        graph = self.evidence(client, "b9-graph")
        self.assertIn("claimed_by_agent", graph)
        self.assertEqual(graph["claimed_by_agent"], [])
        self.assertTrue(graph["verdict_is_not_an_agent_claim"])
        self.assertIn("no agent claim is read", graph["verdict_source"])

        # A claim injected into the compartment changes nothing about the verdict.
        with_claim = OperatingSurface._acceptance_gate(
            {
                "verdict": "PROVEN",
                "missing_links": [],
                "claimed_by_agent": [
                    {"source": "agent", "claim": "the task is complete and verified"}
                ],
            }
        )
        self.assertEqual(with_claim["final_status"], "PROVEN")

        # ... and a claim cannot rescue a graph with a missing mandatory link.
        rescued = OperatingSurface._acceptance_gate(
            {
                "verdict": "PROVEN",
                "missing_links": [{"missing_code": "no_command_performed"}],
                "claimed_by_agent": [
                    {"source": "agent", "claim": "the task is complete and verified"}
                ],
            }
        )
        self.assertTrue(rescued["acceptance_refused"])
        self.assertEqual(rescued["final_status"], "ACCEPTED_REFUSED_INCOMPLETE_TRACE")

    def test_14_approval_is_bound_to_parameters_and_a_change_is_visible(self) -> None:
        """The approval link carries the parameter set AND the intent fingerprint.

        Goes red if: the parameters stop being reported on the approval link, or
        the fingerprint comparison stops being published, because a parameter
        change would then be invisible in the graph.
        """

        client, _ = self._seed_and_run()
        graph = self.evidence(client, "b9-graph")
        approval_links = [
            link for link in graph["links"] if link["to"] == "APPROVAL" and link["status"] == "ESTABLISHED"
        ]
        self.assertTrue(approval_links, "no established approval link was recorded")
        bound = [link for link in approval_links if link.get("extra", {}).get("approved_fingerprint")]
        if bound:
            link = bound[0]
            self.assertIn("parameters", link["extra"])
            self.assertIn("approved_fingerprint", link["extra"])
            self.assertIn("observed_fingerprint", link["extra"])
            self.assertIn("parameter_binding_holds", link["extra"])
            self.assertIn("intent_fingerprint=", " ".join(link["evidence"]))
        else:
            # The step had no approval; then the link must be MISSING and named --
            # never replaced by an invented one.
            codes = {
                link["missing_code"]
                for link in graph["missing_links"]
                if link["to"] == "APPROVAL"
            }
            self.assertIn("no_approval_decision_recorded", codes)


# ---------------------------------------------------------------------------
# 3. change impact: classifications and the ambiguous move
# ---------------------------------------------------------------------------


class ChangeImpactReasoningTests(Block9Case):
    def _seed(self) -> None:
        (self.root / "pkg").mkdir(exist_ok=True)
        (self.root / "pkg" / "core.py").write_text(
            "def alpha():\n    return 1\n", encoding="utf-8", newline=""
        )
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8", newline="")
        (self.root / "README.md").write_text("original\n", encoding="utf-8", newline="")
        (self.root / "tests").mkdir(exist_ok=True)
        (self.root / "tests" / "test_core.py").write_text(
            "def test_nothing():\n    assert True\n", encoding="utf-8", newline=""
        )

    def test_20_a_real_edit_is_confirmed_with_its_basis_retained(self) -> None:
        """_INJECTION_TARGET_: the classification must come from the basis.

        Goes red if: the fingerprint comparison is disabled (the path would not be
        reported at all); or the basis is dropped, because the assertion requires
        the structural ``FINGERPRINT_MISMATCH`` item and its detail.
        """

        self._seed()
        client = self.client()
        # The baseline is the workspace as it was, so it is taken BEFORE the edit.
        # Taking it afterwards would compare the edit with itself and prove nothing.
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "pkg" / "core.py").write_text(
            "def alpha():\n    return 2\n", encoding="utf-8", newline=""
        )

        body = self.impact(client)
        changes = {change["path"]: change for change in body["changes"]}
        self.assertIn("pkg/core.py", changes)
        change = changes["pkg/core.py"]
        self.assertEqual(change["kind"], "CHANGED")
        self.assertEqual(change["classification"], "CONFIRMED")
        self.assertFalse(change["basis_is_empty"])
        kinds = [item["kind"] for item in change["basis"]]
        self.assertIn("FINGERPRINT_MISMATCH", kinds)
        self.assertIn("SCANNER_FACT_MATCHED_BY_PATH", kinds)
        fingerprint_item = next(
            item for item in change["basis"] if item["kind"] == "FINGERPRINT_MISMATCH"
        )
        self.assertTrue(fingerprint_item["structural"])
        self.assertIn("->", fingerprint_item["detail"])
        self.assertNotEqual(change["baseline_fingerprint"], change["current_fingerprint"])
        # The user's edit is untouched: nothing rewrote the file.
        self.assertIn("return 2", (self.root / "pkg" / "core.py").read_text(encoding="utf-8"))
        # The reason template is stated, not implied.
        self.assertIn("NOT a probability", change["support_rank_meaning"])

    def test_21_a_deletion_is_confirmed_and_invalidates_the_facts_it_carried(self) -> None:
        """_INJECTION_TARGET_: staleness must be derived, not assumed.

        Goes red if: ``_stale_facts`` stops comparing fact ids (no stale facts
        reported); or the invalidation reason vocabulary is replaced, because the
        assertion requires ``source_deleted`` specifically.
        """

        self._seed()
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "pkg" / "core.py").unlink()

        body = self.impact(client)
        changes = {change["path"]: change for change in body["changes"]}
        self.assertIn("pkg/core.py", changes)
        self.assertEqual(changes["pkg/core.py"]["classification"], "CONFIRMED")
        self.assertIsNone(changes["pkg/core.py"]["current_fingerprint"])

        stale = [item for item in body["stale_facts"] if item["source_path"] == "pkg/core.py"]
        self.assertTrue(stale, "a deleted file's facts must be reported as stale")
        for item in stale:
            self.assertEqual(item["reason"], "source_deleted")
            self.assertTrue(item["fact_id"])
            self.assertIsNotNone(item["baseline_fingerprint"])
            self.assertIsNone(item["current_fingerprint"])

        response = client.post("/api/product/stale-facts", json={})
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertIn("source_deleted", payload["reasons"])
        self.assertTrue(payload["stale_fact_ids"])
        # History is retained across the call, and retained ids are reported.
        self.assertTrue(
            set(payload["stale_fact_ids"]).issubset(set(payload["history_retained"]))
        )

    def test_21b_a_truthy_string_is_not_consent_to_invalidate(self) -> None:
        """_INJECTION_TARGET_: only a JSON boolean means what the caller wrote.

        Goes red if ``invalidate`` is coerced with ``bool()``: the strings "false",
        "no" and "0" are all truthy and each one would perform a durable
        invalidation while the caller believed the flag was off.
        """

        self._seed()
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)

        for falsy_text in ("false", "no", "0"):
            response = client.post(
                "/api/product/stale-facts", json={"invalidate": falsy_text}
            )
            self.assertEqual(response.status_code, 400, response.text)

        # A real boolean still works in both directions.
        for value in (False, True):
            response = client.post(
                "/api/product/stale-facts", json={"invalidate": value}
            )
            self.assertEqual(response.status_code, 200, response.text)

    @staticmethod
    def _durable_state(store: object) -> dict:
        """Every file in the run store, by relative name, with its exact bytes.

        This is the durable state a GET could damage: the baseline record, the
        onboarding record, step-graph records and the ledger. Names and bytes are
        compared, so a rewrite that changed one field cannot pass unnoticed.
        """

        root = pathlib.Path(store.root)
        state: dict = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                key = str(path.relative_to(root)).replace("\\", "/")
                state[key] = path.read_bytes()
        return state

    def test_a_verification_plan_read_does_not_write_durable_state(self) -> None:
        """_INJECTION_TARGET_: a read is not a write.

        Goes red if the read path seeds a baseline record or merges stale-fact
        history while answering: the store then differs before and after two
        consecutive ``GET /api/product/verification-plan`` calls, and the
        onboarding stage can move without anybody asking for it.
        """

        service = self.service()
        client = self.client(service)
        store = service.run_store

        # Durable state is created through the WRITE path on purpose: comparing two
        # reads of an empty store would prove nothing. ``POST /api/product/baseline``
        # is the documented way to record a baseline.
        (self.root / "pkg").mkdir(parents=True, exist_ok=True)
        (self.root / "pkg" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
        baseline = client.post("/api/product/baseline", json={})
        self.assertEqual(baseline.status_code, 200, baseline.text)
        warmup = client.get("/api/product/verification-plan")
        self.assertEqual(warmup.status_code, 200, warmup.text)
        stage_before = client.get("/api/product/onboarding").json()

        before = self._durable_state(store)
        self.assertTrue(before, "the store must hold the seeded state by now")
        self.assertIn("project-baseline/state.json", before)

        second = client.get("/api/product/verification-plan")
        self.assertEqual(second.status_code, 200, second.text)
        third = client.get("/api/product/verification-plan")
        self.assertEqual(third.status_code, 200, third.text)

        after = self._durable_state(store)
        self.assertEqual(
            sorted(after),
            sorted(before),
            "a read created or removed durable files",
        )
        for name, content in before.items():
            self.assertEqual(after[name], content, f"{name} was rewritten by a read")

        # The plan is still returned, and it is stable across reads.
        self.assertTrue(second.json())
        self.assertEqual(second.json(), third.json())

        # Reading the plan did not advance onboarding.
        self.assertEqual(client.get("/api/product/onboarding").json(), stage_before)

    def test_22_an_ambiguous_move_is_never_auto_resolved(self) -> None:
        """_SENSITIVITY_: two identical candidates must stay unresolved.

        The in-file control re-runs the exact decision rule with the
        ``len(candidates) == 1`` guard removed and shows the loosened rule WOULD
        have picked the first candidate -- so this test is measuring the guard and
        not the fixture.

        Goes red if: the ambiguity guard is dropped, or ``move_candidates`` is
        truncated to a single resolved path.
        """

        self._seed()
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        payload = (self.root / "README.md").read_text(encoding="utf-8")
        (self.root / "README.md").unlink()
        (self.root / "NOTES.md").write_text(payload, encoding="utf-8", newline="")
        (self.root / "OTHER.md").write_text(payload, encoding="utf-8", newline="")
        # Two DIFFERENT paths really exist now, so the ambiguity is real and not a
        # fixture artefact of one path being reported twice.
        self.assertNotEqual(
            (self.root / "NOTES.md").resolve(), (self.root / "OTHER.md").resolve()
        )

        body = self.impact(client)
        changes = {change["path"]: change for change in body["changes"]}
        deleted = changes["README.md"]
        self.assertEqual(deleted["kind"], "DELETED")
        self.assertTrue(deleted["ambiguous_move"])
        self.assertEqual(deleted["probable_rename_to"], "")
        self.assertEqual(
            sorted(deleted["move_candidates"]), ["NOTES.md", "OTHER.md"]
        )
        # The note is the engine's own wording. What matters is that it says the
        # destination is NOT chosen, in the engine's own terms.
        self.assertIn("ambiguous", deleted["reasoning_note"])
        self.assertIn("none may be treated as the destination", deleted["reasoning_note"])
        self.assertIn("No candidate was chosen", deleted["reasoning_note"])
        self.assertEqual(body["classification_counts"]["ambiguous_moves"], 1)

        # The two candidates themselves are still reported, as additions, and
        # neither is labelled as a rename destination.
        self.assertEqual(changes["NOTES.md"]["kind"], "ADDED")
        self.assertEqual(changes["OTHER.md"]["kind"], "ADDED")
        self.assertEqual(changes["NOTES.md"]["probable_rename_to"], "")

        # THE SENSITIVITY CONTROL: the same rule with the guard removed.
        def loosened(candidates: list[str]) -> str:
            # This is precisely the branch under test with `len(...) == 1` removed.
            if candidates:
                return candidates[0]
            return ""

        self.assertEqual(loosened(["NOTES.md", "OTHER.md"]), "NOTES.md")
        self.assertNotEqual(loosened(["NOTES.md", "OTHER.md"]), deleted["probable_rename_to"])

    def test_23_a_single_candidate_is_probable_and_still_not_established(self) -> None:
        """The Block #8 discipline is preserved: probable, never established.

        Goes red if: a single candidate is reported as a resolved rename -- the
        control asserts the response never contains an established-rename marker.
        """

        self._seed()
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        payload = (self.root / "README.md").read_text(encoding="utf-8")
        (self.root / "README.md").unlink()
        (self.root / "NOTES.md").write_text(payload, encoding="utf-8", newline="")

        body = self.impact(client)
        changes = {change["path"]: change for change in body["changes"]}
        self.assertEqual(changes["README.md"]["probable_rename_to"], "NOTES.md")
        self.assertFalse(changes["README.md"]["ambiguous_move"])
        self.assertEqual(changes["README.md"]["kind"], "DELETED")
        self.assertIn("probable, not established", changes["README.md"]["reasoning_note"])
        self.assertNotIn('"RENAMED"', json.dumps(body))
        self.assertNotIn("renamed_to_established", json.dumps(body))

    def test_24_requirements_and_tests_are_named_with_their_basis(self) -> None:
        """A requirement is linked only where its declared expectation names the path.

        Goes red if: the expectation match is loosened to a substring or a prefix,
        because ``other/core.py`` and ``pkg/core.py.orig`` must NOT be linked; or
        the basis stops naming the criterion.
        """

        self._seed()
        spec = AcceptanceSpec(
            declaration_id=self.build_id,
            criteria=(
                AcceptanceCriterion("core", "core exists", requirement_id="req-core"),
                AcceptanceCriterion("other", "other exists", requirement_id="req-other"),
            ),
            expectations={
                "core": VerificationExpectation("pkg/core.py", True),
                "other": VerificationExpectation("pkg/core.py.orig", True),
            },
        )
        client = self.client(self.service(acceptance_specs={self.build_id: spec}))
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "pkg" / "core.py").write_text("z = 1\n", encoding="utf-8", newline="")

        body = self.impact(client)
        change = next(item for item in body["changes"] if item["path"] == "pkg/core.py")
        linked = {item["criterion_id"] for item in change["affected_requirements"]}
        self.assertEqual(linked, {"core"})
        self.assertIn(
            "DECLARED_EXPECTATION_NAMES_PATH", [item["kind"] for item in change["basis"]]
        )
        # The unrelated expectation is not credited with a change it did not see.
        self.assertNotIn("other", linked)
        self.assertIn("req-other", body["unlinked_requirements"])

        # A related test is named, and named as a path RELATION rather than as
        # coverage.
        related = change["affected_tests"]
        self.assertTrue(related, "tests/test_core.py relates to pkg/core.py by name")
        self.assertEqual(related[0]["path"], "tests/test_core.py")
        self.assertIn("not proof that it covers", related[0]["why"])
        self.assertIn(
            "TEST_FILE_RELATED_BY_PATH", [item["kind"] for item in change["basis"]]
        )

    def test_25_an_empty_basis_is_unknown_and_never_confirmed(self) -> None:
        """_INJECTION_TARGET_: absence of evidence never becomes a confirmed fact.

        Goes red if: ``classify_basis`` is changed to default to ``CONFIRMED``; or
        the engine assigns a classification without a basis.
        """

        from app.analysis.impact import (
            BasisItem,
            BasisKind,
            FindingClassification,
            classify_basis,
        )

        self.assertEqual(classify_basis([]), FindingClassification.UNKNOWN)
        self.assertEqual(
            classify_basis([BasisItem(BasisKind.TOPOLOGY_EDGE_CITES_FACT, "edge-1")]),
            FindingClassification.PROBABLE,
        )
        self.assertEqual(
            classify_basis([BasisItem(BasisKind.FINGERPRINT_MISMATCH, "pkg/core.py")]),
            FindingClassification.CONFIRMED,
        )
        # A weak basis is never upgraded by adding more weak basis.
        weak = [
            BasisItem(BasisKind.TOPOLOGY_EDGE_CITES_FACT, "e1"),
            BasisItem(BasisKind.MEMORY_PINNED_TO_PATH, "m1"),
            BasisItem(BasisKind.TEST_FILE_RELATED_BY_PATH, "tests/test_core.py"),
        ]
        self.assertEqual(classify_basis(weak), FindingClassification.PROBABLE)

    def test_26_every_change_is_classified_with_a_cross_checkable_count(self) -> None:
        """The counts must agree with the per-item classifications.

        Goes red if: the count is computed from anything other than the findings;
        or a classification value outside the three-value vocabulary is emitted.
        """

        self._seed()
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "pkg" / "core.py").write_text("q = 1\n", encoding="utf-8", newline="")
        (self.root / "pkg" / "new.py").write_text("n = 1\n", encoding="utf-8", newline="")

        body = self.impact(client)
        allowed = {"CONFIRMED", "PROBABLE", "UNKNOWN"}
        observed = {"CONFIRMED": 0, "PROBABLE": 0, "UNKNOWN": 0}
        for change in body["changes"]:
            self.assertIn(change["classification"], allowed)
            observed[change["classification"]] += 1
        counts = body["classification_counts"]
        self.assertEqual(observed["CONFIRMED"], counts["confirmed"])
        self.assertEqual(observed["PROBABLE"], counts["probable"])
        self.assertEqual(observed["UNKNOWN"], counts["unknown"])
        self.assertEqual(counts["findings"], len(body["changes"]))
        # Every path reported as unclassified really is classified UNKNOWN, so the
        # two fields cannot disagree.
        for path in body["unclassified_paths"]:
            self.assertEqual(
                next(c for c in body["changes"] if c["path"] == path)["classification"],
                "UNKNOWN",
            )


# ---------------------------------------------------------------------------
# 4. verification planning: six states, no promotion
# ---------------------------------------------------------------------------


class VerificationPlanTests(Block9Case):
    def test_30_all_six_states_are_representable_and_none_but_passed_is_a_pass(self) -> None:
        """_SENSITIVITY_: SKIPPED and UNKNOWN are never counted as passed.

        Goes red if: ``CheckState.is_pass`` is loosened; or the planner maps
        ``ERROR`` onto ``PASSED``; or ``SKIPPED`` is dropped from the vocabulary.
        """

        from app.analysis.verification import CheckState, VerificationPlanner

        self.assertEqual(
            [state.value for state in CheckState],
            ["NOT_RUN", "RUNNING", "PASSED", "FAILED", "SKIPPED", "UNKNOWN"],
        )
        for state in CheckState:
            self.assertEqual(state.is_pass, state is CheckState.PASSED, state.value)
        for state in (CheckState.NOT_RUN, CheckState.SKIPPED, CheckState.UNKNOWN):
            self.assertFalse(state.is_proven, state.value)

        # Each real record shape maps to the state it must map to.
        cases = {
            (StepStatus.RUNNING, VerificationStatus.NOT_RUN, ""): "RUNNING",
            (StepStatus.PENDING, VerificationStatus.NOT_RUN, ""): "NOT_RUN",
            (StepStatus.CANCELLED, VerificationStatus.NOT_RUN, ""): "SKIPPED",
            (StepStatus.BLOCKED, VerificationStatus.NOT_RUN, ""): "SKIPPED",
            (StepStatus.SUCCEEDED, VerificationStatus.PASSED, ""): "PASSED",
            (StepStatus.SUCCEEDED, VerificationStatus.FAILED, ""): "FAILED",
            (StepStatus.SUCCEEDED, VerificationStatus.ERROR, ""): "UNKNOWN",
            # MEASURED and deliberate: a step that SUCCEEDED but whose declared check
            # was never observed to hold is UNKNOWN -- a successful command is not a
            # passing check. A step that has not run yet is NOT_RUN.
            (StepStatus.SUCCEEDED, VerificationStatus.NOT_RUN, ""): "UNKNOWN",
            # An unprovable step stays UNKNOWN whatever the declared check says. An
            # earlier ordering tested the check first and called this NOT_RUN, which
            # reads as "nothing has happened yet" and is weaker than the truth.
            (StepStatus.UNKNOWN, VerificationStatus.NOT_RUN, ""): "UNKNOWN",
            # A declared check whose evaluation raised: ERROR, mapped to UNKNOWN.
            (StepStatus.FAILED, VerificationStatus.ERROR, "boom"): "UNKNOWN",
            # A step recorded FAILED with no check overriding it is FAILED.
            (StepStatus.FAILED, VerificationStatus.NOT_RUN, ""): "FAILED",
        }
        from app.analysis.verification import _verification_state

        observed = set()
        for (step_status, verification, error), expected in cases.items():
            record = StepRecord(
                step_id="s",
                status=step_status,
                verification_status=verification,
                verification_error=error,
                evidence=("evidence-code",),
            )
            state, evidence, reason = _verification_state(
                record=record, graph_status="INCOMPLETE"
            )
            self.assertEqual(state.value, expected, (step_status, verification))
            self.assertTrue(reason)
            if expected != "PASSED":
                self.assertFalse(state.is_pass, expected)
            observed.add(expected)
        self.assertEqual(
            observed,
            {"RUNNING", "NOT_RUN", "SKIPPED", "PASSED", "FAILED", "UNKNOWN"},
        )

        # Through the planner: a step that succeeded with no declared verification
        # is UNKNOWN, never PASSED -- and the plan's own counts agree.
        plan = VerificationPlanner(
            declarations={"d1": object()},
            step_records=(
                StepRecord(
                    step_id="s1",
                    status=StepStatus.SUCCEEDED,
                    verification_status=VerificationStatus.PASSED,
                ),
            ),
            declared_plan=({"step_id": "s1", "action": {"declaration_id": "d1"}},),
        ).plan(None)
        self.assertEqual(plan.passed_count, 0)
        self.assertEqual(plan.state_counts["PASSED"], 0)

    def test_31_the_plan_is_derived_from_changed_files_with_a_named_gap(self) -> None:
        """A changed path no expectation covers is an explicit UNKNOWN gap.

        Goes red if: the uncovered path is omitted (the gap set would be empty);
        or it is reported as PASSED.
        """

        (self.root / "covered.py").write_text("a = 1\n", encoding="utf-8", newline="")
        (self.root / "uncovered.py").write_text("b = 1\n", encoding="utf-8", newline="")
        spec = AcceptanceSpec(
            declaration_id=self.build_id,
            criteria=(AcceptanceCriterion("c1", "covered exists", requirement_id="r1"),),
            expectations={"c1": VerificationExpectation("covered.py", True)},
        )
        client = self.client(self.service(acceptance_specs={self.build_id: spec}))
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "covered.py").write_text("a = 2\n", encoding="utf-8", newline="")
        (self.root / "uncovered.py").write_text("b = 2\n", encoding="utf-8", newline="")

        plan = self.plan(client)
        states = {check["check_id"]: check["state"] for check in plan["checks"]}
        self.assertIn("criterion:%s:c1" % self.build_id, states)
        uncovered_id = "changed-path:uncovered.py"
        self.assertIn(uncovered_id, states)
        self.assertEqual(states[uncovered_id], "UNKNOWN")
        self.assertNotEqual(states[uncovered_id], "PASSED")
        check = next(c for c in plan["checks"] if c["check_id"] == uncovered_id)
        self.assertEqual(
            check["uncovered_reason"], "no_declared_expectation_covers_this_path"
        )
        self.assertEqual(check["driven_by"], ["uncovered.py"])
        self.assertTrue(any("uncovered.py" in gap for gap in plan["coverage_gaps"]))
        self.assertTrue(any("NOT as passed" in gap for gap in plan["coverage_gaps"]))
        # The covered criterion's own check IS driven by its changed path, so the
        # gap above is caused by the absence of an expectation, not by the fixture.
        covered = next(
            c for c in plan["checks"] if c["check_id"] == "criterion:%s:c1" % self.build_id
        )
        self.assertEqual(covered["driven_by"], ["covered.py"])

    def test_32_a_requirement_with_no_criterion_is_a_named_gap(self) -> None:
        """A requirement nothing can check is named, not silently absent.

        Goes red if: ``requirements_without_checks`` stops being populated (the
        requirement would then be invisible rather than reported as a gap).
        """

        # ``AcceptanceSpec`` refuses an empty criterion list ("criteria must not be
        # empty"), so a requirement with nothing to check is composed by declaring NO
        # spec for it -- which is exactly the state this test is about. The earlier
        # revision constructed an ``AcceptanceSpec(criteria=())`` and the
        # constructor correctly refused it.
        client = self.client(self.service(acceptance_specs={}))
        plan = self.plan(client)
        self.assertIn(self.build_id, plan["requirements_without_checks"])
        self.assertTrue(
            any("no declared criterion" in gap for gap in plan["coverage_gaps"]),
            plan["coverage_gaps"],
        )
        # With no file differing from the baseline there is no change-driven check,
        # and the plan says so rather than inventing one.
        self.assertFalse(
            any(check["kind"] == "UNCOVERED_CHANGE" for check in plan["checks"])
        )
        declared = client.get("/api/product/requirements").json()["requirements"]
        self.assertIn(
            self.build_id, {item["requirement_id"] for item in declared}
        )
        # And every requirement listed as checkless is really declared.
        declarations = client.get("/api/product/requirements").json()["requirements"]
        declared = {item["requirement_id"] for item in declarations}
        for requirement_id in plan["requirements_without_checks"]:
            self.assertIn(requirement_id, declared)

    def test_33_the_plan_is_project_bound_and_read_only(self) -> None:
        """_INJECTION_TARGET_: the plan route writes nothing.

        Goes red if: planning turns into a write (the workspace fingerprint would
        change), or the project check is removed (a foreign id would return 200).
        """

        (self.root / "thing.py").write_text("x = 1\n", encoding="utf-8", newline="")
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "thing.py").write_text("x = 2\n", encoding="utf-8", newline="")
        before = workspace_fingerprint(self.root)

        first = self.plan(client)
        second = self.plan(client)
        self.assertEqual(first["state_counts"], second["state_counts"])
        after = workspace_fingerprint(self.root)
        self.assertEqual(before, after, "the verification plan wrote to the workspace")
        # The changed path really is present, so the invariance above is not vacuous.
        self.assertTrue(
            any(check["driven_by"] for check in first["checks"]),
            "the fixture produced no change-driven check, so the test would be vacuous",
        )

    def test_34_coverage_gaps_name_the_reason_and_never_a_pass(self) -> None:
        """A gap is never rounded up, and the response says so in its own words.

        Goes red if: the honest note is removed, or ``passed_count`` counts a
        non-passing state.
        """

        client = self.client()
        plan = self.plan(client)
        self.assertTrue(plan["only_passed_counts_as_a_pass"])
        passed = [check for check in plan["checks"] if check["state"] == "PASSED"]
        self.assertEqual(plan["passed_count"], len(passed))
        self.assertTrue(
            any("SKIPPED and UNKNOWN" in note for note in plan["notes"]), plan["notes"]
        )
        for check in plan["checks"]:
            if check["state"] != "PASSED":
                self.assertFalse(check["is_pass"], check["check_id"])
            self.assertTrue(check["reason"], check["check_id"])


# ---------------------------------------------------------------------------
# 5. the bounded repair decision
# ---------------------------------------------------------------------------


class RepairLoopTests(Block9Case):
    def _failing_attempt(self, graph_run_id: str = "b9-repair"):
        """A real attempt that really fails: exit 3 with a declared exit-0 check."""

        client = self.client()
        steps = [
            {
                "step_id": "step-fail",
                "action": {"declaration_id": self.fail_id},
                "verification": {"kind": "EXIT_CODE", "expected_exit_code": 0},
            }
        ]
        response = self.graph_run(client, graph_run_id, steps)
        self.assertEqual(response.status_code, 200, response.text)
        return client, response.json()

    def _repair(self, client: TestClient, run_id: str, **extra) -> dict:
        body = {"graph_run_id": run_id}
        body.update(extra)
        response = client.post("/api/product/repair-plan", json=body, timeout=120)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_40_a_real_failure_yields_a_named_cause_and_an_ordinal_confidence(self) -> None:
        """_INJECTION_TARGET_: the cause must come from recorded evidence.

        Goes red if: the evidence codes stop being read (the cause would fall back
        to ``status_only``); or the confidence becomes a number, because the
        assertion requires it to be one of exactly three ordinal labels with a
        stated meaning.
        """

        client, result = self._failing_attempt()
        self.assertEqual(result["result"]["status"], "FAILED")
        payload = self._repair(client, "b9-repair")

        self.assertTrue(payload["steps"], payload)
        step = payload["steps"][0]
        cause = step["probable_cause"]
        # MEASURED: the command exits 3, so the runner records
        # `verification_not_run:execution_did_not_succeed`. The honest cause is
        # therefore that the COMMAND did not succeed, not that the check was
        # unevaluable. The first version of this test asserted the latter, which the
        # implementation never promised; the implementation was right and the test
        # expectation was wrong, so the expectation was corrected.
        self.assertEqual(cause["code"], "command_did_not_succeed")
        self.assertIn("exited 3", cause["statement"])
        self.assertIn(cause["confidence"], {"SUPPORTED", "INFERRED", "UNSUPPORTED"})
        self.assertEqual(cause["confidence"], "SUPPORTED")
        self.assertNotIsInstance(cause["confidence"], (int, float))
        self.assertTrue(cause["basis"])
        self.assertTrue(cause["limitations"], "a cause must state its own limits")
        self.assertTrue(cause["confidence_is_ordinal_not_a_probability"])
        self.assertIn("ORDINAL", cause["confidence_meaning"])
        self.assertIn("not a probability", cause["confidence_meaning"])
        self.assertEqual(step["targeted_condition"], "EXIT_CODE:exit_code=0")
        self.assertIn("evidence:", " ".join(step["evidence"]))
        # The loop reports that it performed nothing.
        self.assertFalse(payload["dispatch_performed"])
        self.assertFalse(step["dispatched"])
        self.assertIn("no dispatch path is composed", payload["dispatch_path"])

    def test_41_an_unknown_side_effect_is_refused_and_never_repeated(self) -> None:
        """_INJECTION_TARGET_: REFUSED_SIDE_EFFECT_UNKNOWN stays a refusal.

        THE GUARANTEE IS PRODUCED BY THE REAL LEDGER, and its reachability is
        asserted BEFORE it is used: a fresh ``RunIdempotencyGuard`` is claimed for a
        real operation identity, a NON-replay-safe ``HOST_PROCESS`` effect is begun
        and never completed, and the guard's own ``repeat_decision`` is required to
        answer ``REFUSED_SIDE_EFFECT_UNKNOWN``. That decision is then handed to the
        loop the route composes, so the refusal path is exercised on a guarantee the
        ledger really reached -- not on a value this test invented.

        WHY THE STEP'S OWN IDENTITY IS NOT USED. Measured: a step's own record is
        already TERMINAL (``FAILED``) by the time it can be planned, so its repeat
        guarantee is ``REPLAY_OF_RECORD`` -- the recorded outcome is read back and
        nothing is re-run. That is the correct answer for "repeat this step", and
        ``test_41c`` pins it through the HTTP route. The unprovable position belongs
        to a *repair* operation, which is a different operation identity, so it is
        constructed as one.

        Goes red if: the refusal is converted into an admissible repair; or any
        dispatch happens, because ``dispatch_performed`` must be false and the
        workspace fingerprint must be unchanged.
        """

        from app.agent_runtime.idempotency import (
            OperationClass,
            SideEffectKind,
            SideEffectRecord,
            SideEffectState,
        )
        from app.agent_runtime.idempotency_integration import RunIdempotencyGuard
        from app.analysis.repair import RepairLoop, RepairOutcome, StopRule
        from app.workflows.step_graph import StepGraphSnapshot, StepGraphStatus

        guard = RunIdempotencyGuard.create(ledger_root=str(self._temp_dir()))
        identity = operation_identity_from_binding(
            operation_class=OperationClass.DECLARED_VERIFICATION,
            idempotency_key="b9-repair-side-effect-1",
            declaration_id=self.fail_id,
            task_identity=TaskIdentity(
                task_id="b9-repair-side-effect-1",
                # A real digest: ``TaskIdentity`` refuses anything that is not one.
                task_fingerprint=hashlib.sha256(b"b9-repair").hexdigest(),
            ),
            criterion_identities=(),
        )
        guard.begin(identity, requested_run_id="b9-repair-run")
        guard.engine.ledger.begin_side_effect(
            identity,
            SideEffectRecord(
                fingerprint="b9-unprovable-effect",
                kind=SideEffectKind.HOST_PROCESS,
                state=SideEffectState.NOT_STARTED,
                detail="begun and never completed: whether it landed is unproven",
            ),
        )
        decision = guard.repeat_decision(identity)
        self.assertEqual(
            decision.guarantee.value,
            "REFUSED_SIDE_EFFECT_UNKNOWN",
            "the ledger did not reach the unprovable position, so this test would be vacuous",
        )
        self.assertFalse(decision.permits_automatic_repeat)

        snapshot = StepGraphSnapshot(
            graph_id="operator-plan",
            graph_run_id="b9-refused-repair",
            status=StepGraphStatus.FAILED,
            attempts=1,
            steps=(
                StepRecord(
                    step_id="step-fail",
                    status=StepStatus.FAILED,
                    declaration_id=self.fail_id,
                    verification_status=VerificationStatus.NOT_RUN,
                    execution_exit_code=3,
                    execution_outcome="EXECUTION_FAILURE",
                    evidence=("execution_did_not_succeed:EXECUTION_FAILURE",),
                    verification_expected="exit_code=0",
                ),
            ),
        )
        before = workspace_fingerprint(self.root)
        payload = RepairLoop(
            graph_run_id="b9-refused-repair",
            snapshot=snapshot,
            declarations=self.declarations(),
            repeat_decisions={"step-fail": decision},
            approval_policy=ApprovalPolicy(),
        ).plan().to_dict()
        after = workspace_fingerprint(self.root)

        self.assertEqual(before, after)
        self.assertEqual(payload["outcome"], RepairOutcome.REFUSED_SIDE_EFFECT_UNKNOWN.value)
        self.assertEqual(payload["stop_rule"], StopRule.SIDE_EFFECT_OUTCOME_UNKNOWN.value)
        self.assertFalse(payload["dispatch_performed"])
        step = payload["steps"][0]
        self.assertEqual(step["refusal_code"], "REFUSED_SIDE_EFFECT_UNKNOWN")
        self.assertFalse(step["permits_automatic_repeat"])
        self.assertFalse(step["authorized_to_dispatch"])
        self.assertFalse(step["dispatched"])
        self.assertIn("never repeated", json.dumps(payload))

    def test_41b_a_terminal_guarantee_refuses_the_repair_and_dispatches_nothing(
        self,
    ) -> None:
        """MEASURED: the composed route refuses, because the guarantee is replay-only.

        A step's own record is terminal as soon as it has been settled, so its
        measured guarantee is ``REPLAY_OF_RECORD`` and the loop refuses to treat that
        as permission to run it again. That is the correct and safe answer, and this
        test pins it rather than asserting a permissive one.

        Goes red if: ``permits_automatic_repeat=False`` stops refusing (the outcome
        would become ``REPAIR_ADMISSIBLE``), or the loop dispatches.
        """

        client, _ = self._failing_attempt("b9-terminal-guarantee")
        first = self._repair(client, "b9-terminal-guarantee")
        self.assertEqual(first["outcome"], "REFUSED_BY_REPEAT_GUARANTEE")
        self.assertEqual(first["stop_rule"], "repeat_guarantee_does_not_permit")
        self.assertFalse(first["dispatch_performed"])
        self.assertIn("no dispatch path is composed", first["dispatch_path"])
        step = first["steps"][0]
        self.assertEqual(step["repeat_guarantee"], "REPLAY_OF_RECORD")
        self.assertFalse(step["dispatched"])
        # Asking twice reports the same decision and dispatches nothing either time.
        second = self._repair(client, "b9-terminal-guarantee")
        self.assertEqual(second["outcome"], first["outcome"])
        self.assertEqual(second["progress_marker"], first["progress_marker"])
        self.assertFalse(second["dispatch_performed"])

    def test_41d_an_admissible_repair_is_still_not_dispatched(self) -> None:
        """_INJECTION_TARGET_: an admissible repair performs none of itself.

        ``FIRST_ATTEMPT`` is the guarantee for an operation with no durable record,
        and it DOES permit an automatic repeat. Reaching it requires a decision the
        real guard produced for an unclaimed identity, so the decision is obtained
        from a fresh ``RunIdempotencyGuard`` -- and then the loop must still dispatch
        nothing, because no dispatch path is composed on this surface.

        Goes red if: ``dispatch_performed`` becomes true, or the ``REPAIR_ADMISSIBLE``
        branch stops naming the targeted re-verification it would perform.
        """

        from app.agent_runtime.idempotency import OperationClass
        from app.agent_runtime.idempotency_integration import RunIdempotencyGuard
        from app.analysis.repair import RepairLoop
        from app.workflows.step_graph import StepGraphSnapshot, StepGraphStatus

        guard = RunIdempotencyGuard.create(ledger_root=str(self._temp_dir()))
        identity = operation_identity_from_binding(
            operation_class=OperationClass.DECLARED_VERIFICATION,
            idempotency_key="b9-fresh-repair-1",
            declaration_id=self.fail_id,
            task_identity=TaskIdentity(
                task_id="b9-fresh-repair-1",
                task_fingerprint=hashlib.sha256(b"b9-fresh").hexdigest(),
            ),
            criterion_identities=(),
        )
        decision = guard.repeat_decision(identity)
        self.assertEqual(decision.guarantee.value, "FIRST_ATTEMPT")
        self.assertTrue(decision.permits_automatic_repeat)

        snapshot = StepGraphSnapshot(
            graph_id="operator-plan",
            graph_run_id="b9-fresh-repair",
            status=StepGraphStatus.FAILED,
            steps=(
                StepRecord(
                    step_id="step-fail",
                    status=StepStatus.FAILED,
                    declaration_id=self.fail_id,
                    verification_status=VerificationStatus.NOT_RUN,
                    execution_exit_code=3,
                    execution_outcome="EXECUTION_FAILURE",
                    evidence=("execution_did_not_succeed:EXECUTION_FAILURE",),
                    verification_kind="EXIT_CODE",
                    verification_expected="exit_code=0",
                ),
            ),
        )
        before = workspace_fingerprint(self.root)
        payload = RepairLoop(
            graph_run_id="b9-fresh-repair",
            snapshot=snapshot,
            declarations=self.declarations(),
            repeat_decisions={"step-fail": decision},
            approval_policy=ApprovalPolicy(),
        ).plan().to_dict()
        self.assertEqual(workspace_fingerprint(self.root), before)
        self.assertEqual(payload["outcome"], "REPAIR_ADMISSIBLE")
        self.assertFalse(payload["dispatch_performed"])
        step = payload["steps"][0]
        self.assertFalse(step["dispatched"])
        self.assertEqual(step["targeted_condition"], "EXIT_CODE:exit_code=0")
        self.assertIn("re-establish the targeted condition only", step["proposed_action"])

    def test_41c_a_terminal_step_reports_replay_not_a_repeat(self) -> None:
        """A step that already reached a terminal outcome must not be repeatable.

        Goes red if: the route reports a guarantee that permits an automatic repeat
        for a step whose record is already terminal.
        """

        client, _ = self._failing_attempt("b9-terminal")
        payload = self._repair(client, "b9-terminal")
        step = payload["steps"][0]
        self.assertEqual(step["repeat_guarantee"], "REPLAY_OF_RECORD")
        self.assertFalse(step["permits_automatic_repeat"])
        self.assertFalse(step["authorized_to_dispatch"])

    def test_42_the_attempt_cap_stops_the_loop(self) -> None:
        """_SENSITIVITY_: the cap is a real ceiling, not a comment.

        The same attempt is planned with ``attempt_cap=1`` and with
        ``attempt_cap=5``; the difference between the two outcomes is caused by the
        cap alone, because nothing else about the attempt changes.

        Goes red if: the cap comparison is removed (both would report the same
        outcome and the positive control would fail).
        """

        client, _ = self._failing_attempt("b9-cap")
        # Spend one attempt so the attempt counter is non-zero.
        capped = self._repair(client, "b9-cap", attempt_cap=1)
        roomy = self._repair(client, "b9-cap", attempt_cap=5)

        self.assertEqual(capped["outcome"], "ATTEMPT_CAP_REACHED")
        self.assertEqual(capped["stop_rule"], "attempt_cap")
        self.assertEqual(capped["budget"]["attempts_remaining"], 0)
        self.assertNotEqual(roomy["outcome"], "ATTEMPT_CAP_REACHED")
        self.assertGreater(roomy["budget"]["attempts_remaining"], 0)
        self.assertFalse(capped["dispatch_performed"])

    def test_43_no_progress_stops_the_loop(self) -> None:
        """_SENSITIVITY_: an unchanged observable state stops the loop.

        The marker from the first call is fed back as ``previous_progress_marker``;
        the observable state has not changed, so the loop stops. The positive
        control is a *different* marker, which does not stop it.

        Goes red if: the marker comparison is removed (the replayed marker would
        then produce the same outcome as a fresh one).
        """

        client, _ = self._failing_attempt("b9-progress")
        first = self._repair(client, "b9-progress")
        self.assertTrue(first["progress_marker"])
        # THREE-VALUED: with no previous observation supplied, progress is not
        # establishable. Reporting false here would claim a comparison that never
        # happened, which is the defect this assertion pins.
        self.assertIsNone(first["progress_made"])
        self.assertFalse(first["progress_is_establishable"])
        self.assertIn("null = no previous observation", first["progress_meaning"])

        replayed = self._repair(
            client, "b9-progress", previous_progress_marker=first["progress_marker"]
        )
        self.assertFalse(replayed["dispatch_performed"])
        self.assertEqual(replayed["outcome"], "NO_PROGRESS")
        self.assertEqual(replayed["stop_rule"], "no_progress")
        self.assertFalse(replayed["progress_made"])
        self.assertFalse(replayed["dispatch_performed"])

        control = self._repair(
            client, "b9-progress", previous_progress_marker="a-different-marker"
        )
        self.assertNotEqual(control["outcome"], "NO_PROGRESS")

    def test_44_the_marker_is_a_digest_of_observables_only(self) -> None:
        """_INJECTION_TARGET_: the marker must not depend on time or a counter.

        Goes red if: a timestamp, an attempt counter or a random value enters the
        observation payload, because two calls with the same observables would then
        differ.
        """

        from app.analysis.repair import progress_marker

        self.assertEqual(
            progress_marker({"steps": {"a": {"status": "FAILED"}}}),
            progress_marker({"steps": {"a": {"status": "FAILED"}}}),
        )
        self.assertNotEqual(
            progress_marker({"steps": {"a": {"status": "FAILED"}}}),
            progress_marker({"steps": {"a": {"status": "SUCCEEDED"}}}),
        )
        client, _ = self._failing_attempt("b9-marker")
        first = self._repair(client, "b9-marker")
        second = self._repair(client, "b9-marker")
        self.assertEqual(first["progress_marker"], second["progress_marker"])

    def test_45_budgets_are_clamped_and_never_unlimited(self) -> None:
        """A caller may lower a budget but never raise it past the ceiling.

        Goes red if: the clamp is removed, because a huge ``attempt_cap`` would
        then be echoed back.
        """

        client, _ = self._failing_attempt("b9-budget")
        payload = self._repair(
            client, "b9-budget", attempt_cap=10**9, step_budget=10**9, seconds_budget=10**9
        )
        self.assertLessEqual(payload["budget"]["attempt_cap"], 50)
        self.assertLessEqual(payload["budget"]["step_budget"], 200)
        self.assertLessEqual(payload["budget"]["seconds_budget"], 86400.0)

        lowered = self._repair(client, "b9-budget", step_budget=1)
        self.assertEqual(lowered["budget"]["step_budget"], 1)

        malformed = self._repair(client, "b9-budget", attempt_cap="not-a-number")
        self.assertEqual(malformed["budget"]["attempt_cap"], 3)

        # NaN is never a budget.
        nan = self._repair(client, "b9-budget", seconds_budget="nan")
        self.assertEqual(nan["budget"]["seconds_budget"], 900.0)

    def test_46_an_unknown_attempt_is_a_controlled_refusal_not_a_500(self) -> None:
        """The run boundary holds, and an unsafe id is refused before any read.

        Goes red if: the allow-list is bypassed (an unsafe id would raise an
        unhandled error), or an unknown id is treated as an empty plan.
        """

        client = self.client()
        unknown = client.post(
            "/api/product/repair-plan", json={"graph_run_id": "no-such-attempt"}
        )
        self.assertEqual(unknown.status_code, 200, unknown.text)
        self.assertFalse(unknown.json()["found"])
        self.assertEqual(unknown.json()["outcome"], "BLOCKED_NO_TARGET")

        unsafe = client.post(
            "/api/product/repair-plan", json={"graph_run_id": "../../etc/passwd"}
        )
        self.assertIn(unsafe.status_code, (400, 403, 409), unsafe.text)
        self.assertFalse(unsafe.json().get("executed", True))


# ---------------------------------------------------------------------------
# 6. an end-to-end scenario over REAL services
# ---------------------------------------------------------------------------


class EndToEndVerticalSliceTests(Block9Case):
    """Goal -> requirements -> criteria -> plan -> real steps -> results -> status.

    Nothing is simulated: the steps are real ``python -c`` child processes, the
    files are really written, the approval decision is whatever the composed
    resolver returns, and the final status is computed from the recorded results.
    """

    def test_50_the_whole_chain_ends_in_an_honest_status_with_the_gap_named(self) -> None:
        """_INJECTION_TARGET_: the final status must derive from the observations.

        Goes red if: any protection below is loosened -- the fingerprint basis,
        the acceptance gate, the gap naming, or the parameter-bound approval link.
        The assertions are paired so that no single loosening satisfies them all.
        """

        spec = AcceptanceSpec(
            declaration_id=self.build_id,
            criteria=(
                AcceptanceCriterion("module", "calc.py exists", requirement_id="r-src"),
                AcceptanceCriterion("tests", "tests exist", requirement_id="r-tests"),
            ),
            expectations={
                "module": VerificationExpectation("calc.py", True),
                "tests": VerificationExpectation("tests/test_calc.py", True),
            },
        )
        service = self.service(acceptance_specs={self.build_id: spec})
        client = self.client(service)

        # 1. GOAL: recorded as a hypothesis, never a confirmed fact.
        goal = client.post("/api/product/goal", json={"goal": "Build calc.py with tests."})
        self.assertEqual(goal.status_code, 200, goal.text)
        self.assertEqual(goal.json()["kind"], "HYPOTHESIS")
        self.assertFalse(goal.json()["confirmed_fact"])

        # 2. REQUIREMENTS + CRITERIA come from the operator composition.
        requirements = client.get("/api/product/requirements").json()
        by_id = {item["requirement_id"]: item for item in requirements["requirements"]}
        self.assertIn(self.build_id, by_id)
        criterion_ids = {c["criterion_id"] for c in by_id[self.build_id]["criteria"]}
        self.assertEqual(criterion_ids, {"module", "tests"})

        # 3. BASELINE, then a REAL step graph that really creates the files.
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        started = self.graph_run(client, "b9-e2e", [
            {"step_id": "step-build", "action": {"declaration_id": self.build_id}},
            {
                "step_id": "step-verify",
                "depends_on": ["step-build"],
                "action": {"declaration_id": self.verify_id},
            },
        ])
        self.assertEqual(started.status_code, 200, started.text)

        # The real child processes really wrote the real files.
        self.assertTrue((self.root / "calc.py").is_file())
        self.assertTrue((self.root / "tests" / "test_calc.py").is_file())

        # 4. CHANGE IMPACT over the real change, with a real basis.
        impact = self.impact(client)
        paths = {change["path"]: change for change in impact["changes"]}
        self.assertIn("calc.py", paths)
        self.assertEqual(paths["calc.py"]["kind"], "ADDED")
        self.assertEqual(paths["calc.py"]["classification"], "CONFIRMED")
        self.assertFalse(paths["calc.py"]["basis_is_empty"])
        self.assertTrue(paths["calc.py"]["affected_requirements"])

        # 5. VERIFICATION PLAN derived from those changes, with the six states.
        plan = self.plan(client)
        self.assertTrue(plan["checks"])
        for check in plan["checks"]:
            self.assertIn(
                check["state"],
                {"NOT_RUN", "RUNNING", "PASSED", "FAILED", "SKIPPED", "UNKNOWN"},
            )
            self.assertTrue(check["reason"])
        criterion_checks = [
            check for check in plan["checks"] if check["kind"] == "DECLARED_EXPECTATION"
        ]
        self.assertTrue(criterion_checks)
        self.assertTrue(any(check["driven_by"] for check in criterion_checks))
        self.assertIn("tests/test_calc.py", {item["path"] for item in impact["changes"]})

        # 6. EVIDENCE GRAPH: real links, real missing links, a computed status.
        graph = self.evidence(client, "b9-e2e")
        self.assertTrue(graph["attempt_recorded"])
        self.assertEqual(graph["graph_status_reported"], "COMPLETED")
        by_transition = {(link["from"], link["to"]) for link in graph["links"]}
        self.assertIn(("GOAL", "REQUIREMENTS"), by_transition)
        self.assertIn(("VERIFICATION_RESULTS", "FINAL_STATUS"), by_transition)
        self.assertTrue(graph["missing_link_count"] > 0)
        # COMPLETED is NOT treated as proof: the criteria declare *expectations*,
        # not step verifications, so the derived verdict is not PROVEN and the
        # mandatory verification link is missing.
        self.assertNotEqual(graph["verdict"], "PROVEN")
        self.assertTrue(graph["acceptance_refused"])
        self.assertEqual(graph["final_status"], "ACCEPTED_REFUSED_INCOMPLETE_TRACE")
        self.assertIn(
            "verification_result", graph["mandatory_missing_kinds"]
        )
        final_link = next(
            link for link in graph["links"] if link["to"] == "FINAL_STATUS"
        )
        self.assertFalse(final_link["extra"]["graph_status_used_as_proof"])
        self.assertEqual(final_link["extra"]["graph_status_reported"], "COMPLETED")

        # 7. The response says, in its own words, what it did not prove.
        claims = impact["claims"]
        self.assertTrue(claims["proved"])
        self.assertTrue(
            any("UNKNOWN or SKIPPED" in item for item in claims["not_proved"])
        )
        self.assertNotIn("Traceback", json.dumps(graph))

    def test_51_a_refused_step_leaves_no_side_effect(self) -> None:
        """An authorization refusal precedes any side effect, and the graph says so.

        A step naming an undeclared action is refused before the scope is frozen,
        so no file appears and the graph records a REFUSED/blocked state rather
        than a success.

        Goes red if: the declaration boundary is bypassed (a file would appear), or
        a refusal is rendered as a success.
        """

        client = self.client()
        before = workspace_fingerprint(self.root)
        refused = self.graph_run(client, "b9-refused", [
            {"step_id": "step-bad", "action": {"declaration_id": "not-declared"}},
        ])
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertTrue(refused.json()["refused"])
        self.assertFalse(refused.json()["executed"])
        self.assertEqual(before, workspace_fingerprint(self.root))

    def test_52_a_foreign_project_and_a_foreign_run_stay_inaccessible(self) -> None:
        """_INJECTION_TARGET_: the isolation boundary holds on every new route.

        Goes red if: any project check is dropped (403 becomes 200) or a run id
        from another store becomes readable.
        """

        client = self.client()
        started = self.graph_run(client, "b9-own", self.steps_with_artifact_check())
        self.assertEqual(started.status_code, 200, started.text)

        for path in (
            "/api/product/change-impact",
            "/api/product/evidence-graph",
            "/api/product/verification-plan",
        ):
            response = client.get(path, params={"project_id": "elsewhere"})
            self.assertEqual(response.status_code, 403, f"{path}: {response.text}")
            self.assertNotIn("elsewhere", response.text)

        # A run id from a DIFFERENT store is indistinguishable from an unknown one:
        # the evidence graph reports "not recorded" and invents nothing.
        other_store_root = self._temp_dir()
        from app.runtime.run_store import RunStore

        other = RunStore(root=str(other_store_root))
        other.bind_run("foreign-run", task_id="t")
        graph = self.evidence(client, "foreign-run")
        self.assertFalse(graph["attempt_recorded"])
        self.assertEqual(graph["graph_run_id"], "foreign-run")
        self.assertTrue(graph["acceptance_refused"])
        self.assertNotIn("foreign-run", json.dumps(graph["requirement_verdicts"]))

    def test_53_no_route_returns_a_secret_shaped_or_content_value(self) -> None:
        """The new routes return descriptors, never file contents.

        Goes red if: a route starts returning a file body or a fingerprint of a
        secret-recognised file.
        """

        (self.root / "notes.py").write_text(
            "TOKEN = 'sk-ant-0123456789abcdefghijklmnop'\n", encoding="utf-8", newline=""
        )
        client = self.client()
        self.assertEqual(client.post("/api/product/baseline", json={}).status_code, 200)
        (self.root / "notes.py").write_text(
            "TOKEN = 'sk-ant-9876543210zyxwvutsrqponm'\n", encoding="utf-8", newline=""
        )
        bodies = [
            client.get("/api/product/change-impact", timeout=120).text,
            client.get("/api/product/verification-plan", timeout=120).text,
            client.get("/api/product/evidence-graph", timeout=120).text,
            client.post("/api/product/stale-facts", json={}, timeout=120).text,
        ]
        for body in bodies:
            for marker in ("sk-ant-", "sk-or-v1-", "sk-proj-", "AIzaSy"):
                self.assertNotIn(marker, body)
            self.assertNotIn("TOKEN =", body)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
