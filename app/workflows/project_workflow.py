"""First end-to-end vertical workflow: goal -> specification -> plan -> approval
-> execution -> verification -> result.

HONEST SCOPE -- READ THIS FIRST
==============================

**The project this workflow produces is written by an operator-declared
``python`` command, NOT by an LLM.** No model, provider, or API key is involved
in producing a single line of the generated project. The mock/declared command
is the *source* of the artefacts on purpose, so that this slice exercises the
*workflow and its verification machinery* end to end and offline. It proves:

* that an operator-owned goal becomes a visible specification and acceptance
  criteria before anything runs;
* that the existing authorization chain (``RunScope`` -> ``ExecutionCoordinator``
  -> approval -> ``ExecutionPolicy`` -> ``AuthorizedExecution`` ->
  ``LocalExecutionAdapter``) really gates the action;
* that verification really executes the generated project's own test suite and
  reads its real exit code, rather than printing a success string.

It does **not** prove anything about the quality of model-generated code. That
remains unproven by this slice and is stated as such in the report and in
``docs/SOURCE_OF_TRUTH.md``.

WHAT THIS MODULE IS
===================

A thin, operator-composed coordinator over components that already exist. It
adds no orchestrator, no memory system, no authorization mechanism, and no
second execution path. Every authoritative step is delegated:

============================  ============================================
step                          existing component that performs it
============================  ============================================
specification + criteria      ``AcceptanceSpec`` / ``TaskSpecification``
plan                          ``app.planning.planner.Planner``
approval                      ``ApprovalPolicy`` + ``ApprovalResolver``
execution                     ``ForgeApiService.run_accepted_task``
verification command          ``ForgeApiService.run_declared_verification``
acceptance verdict            ``AcceptanceGate`` (via the harness)
result                        this module's response dataclass
============================  ============================================

The only entry point is :func:`run_project_workflow`, and like the sibling API
entry points it takes an **operator-owned declaration id**. It accepts no
command, no path, no allowlist, and no criterion from a caller.

CALL SHAPE
==========

A minimal Python project (a module, a README, and a test module) is created by
an operator-declared ``python`` command, and then the project's *own* test
suite is really executed as a **second declared command**. The exit code of
that second command is the verification result.

Composition (all of it operator-owned, none of it caller-supplied)::

    BUILD = (
        "python", "-c", "<writes mod.py, README.md, tests/test_mod.py>",
    )
    VERIFY = ("python", "-m", "unittest", "discover", "-s", "tests")

    registry = ProjectWorkflowRegistry(
        verification_declarations={BUILD_ID: VERIFY_ID},
    )
    service = ForgeApiService(
        ...,
        declarations={BUILD_ID: ExecutionDeclaration(BUILD_ID, BUILD, ...),
                      VERIFY_ID: ExecutionDeclaration(VERIFY_ID, VERIFY, ...)},
        acceptance_specs={BUILD_ID: AcceptanceSpec(BUILD_ID, criteria, expectations)},
    )
    result = run_project_workflow(service, registry, BUILD_ID)

LIMITATIONS, STATED PLAINLY
===========================

* The acceptance verdict (``ProjectWorkflowResult.acceptance_status``) is the
  existing ``AcceptanceGate`` verdict over the operator's frozen workspace
  facts. It is **not** the verdict of the second command. The two are reported
  in separate fields on purpose, and neither is presented as the other.
* The second command's exit code is read from the real ``ExecutionResult`` the
  coordinator produced (captured through the service's own coordinator factory,
  which is a public composition seam). It is a measurement, not a claim.
* ``created_files`` / ``deleted_files`` are measured by diffing a workspace
  snapshot taken before the build against one taken after it. ``__pycache__``
  directories are excluded, because CPython writes byte-code caches as a side
  effect of importing the generated module; every other path is reported.
* The workflow does not delete anything, so ``deleted_files`` is empty unless a
  declared command deleted a file that existed before the run.
* A build that fails is never reported as success, and a verification whose
  exit code differs from the declared expectation is reported as failed even
  when the acceptance gate passed.
* In order to read the verification command's real exit code, the workflow
  temporarily rebinds the service's runtime to an equivalent one whose
  ``RunExecutor`` is wrapped, and restores the original in a ``finally`` block.
  That window is not re-entrant: one workflow invocation at a time per
  ``ForgeApiService`` instance. Nothing in that window changes what may be
  authorized -- the wrapped executor forwards every argument and every result
  unchanged.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

from app.agent_runtime.acceptance_spec import AcceptanceSpec
from app.api.service import ForgeApiService
from app.decision.ai_provider import AIDecisionProvider
from app.decision.models import Decision, DecisionRequest
from app.execution.declaration import ExecutionDeclaration
from app.execution.request import ExecutionResult
from app.orchestrator.models import EventType, Task, TaskResult
from app.planning.plan import ExecutionPlan
from app.planning.planner import Planner
from app.tasks.specification import Requirement
from app.tools.acceptance import AcceptanceCriterion
from app.workflows.project_generation import (
    MOCK_TRANSPORT_IS_NOT_REAL_GENERATION,
    GenerationEvidence,
    MaterialisationOutcome,
    ParsedModelProject,
    decision_for_model_text,
    materialise_project,
    parse_model_project,
)

__all__ = [
    "DECLARED_COMMAND_IS_NOT_A_GENERATOR",
    "MODEL_TRANSPORT_IS_NOT_AN_OS_SANDBOX",
    "MOCK_TRANSPORT_IS_NOT_REAL_GENERATION",
    "VERDICT_REAL_PROVIDER_NOT_VERIFIED",
    "VERDICT_GENERATION_FAILED",
    "VERDICT_MATERIALISATION_FAILED",
    "VERDICT_TESTS_FAILED",
    "VERDICT_ACCEPTANCE_FAILED",
    "VERDICT_APPROVAL_REQUIRED",
    "VERDICT_BUILD_FAILED",
    "VERDICT_GENERATION_REFUSED",
    "VERDICT_PROJECT_VERIFIED",
    "VERDICT_MATERIALISATION_INCOMPLETE",
    "VERDICT_DECLARED_COMMAND_VERIFIED",
    "GENERATION_STATUSES",
    "EXECUTION_STATUSES",
    "GENERATION_DISABLED",
    "GENERATION_REFUSED_LIMIT",
    "GENERATION_FAILED_TRANSPORT",
    "GENERATION_FAILED_RESPONSE",
    "GENERATION_FAILED_MATERIALISATION",
    "GENERATION_SUCCEEDED",
    "EXECUTION_NOT_DISPATCHED",
    "EXECUTION_DISPATCHED",
    "EXECUTION_WAITING_FOR_APPROVAL",
    "EXECUTION_FAILED",
    "EXECUTION_SUCCEEDED",
    "EXECUTION_UNKNOWN",
    "DEFAULT_MAX_GENERATION_ATTEMPTS",
    "model_project_source",
    "ProjectWorkflowError",
    "UnknownWorkflowError",
    "CriterionView",
    "ProjectSpecificationView",
    "WorkflowStepView",
    "VerificationOutcome",
    "ProjectWorkflowResult",
    "ProjectWorkflowRegistry",
    "run_project_workflow",
]

#: A single sentence repeated in the code, the report, and the documentation so
#: no reader can conclude that an LLM generated the project in this slice.
DECLARED_COMMAND_IS_NOT_A_GENERATOR = (
    "The generated project in this workflow is produced by an operator-declared "
    "command, not by an LLM: this slice proves the workflow and its verification "
    "machinery, not the quality of model-generated code."
)

#: The honest scope of the model-driven path added beside the declared-command
#: path. Stated in the result's own ``messages`` so it travels with the data.
MODEL_TRANSPORT_IS_NOT_AN_OS_SANDBOX = (
    "A model response becomes project files only after schema validation and "
    "server-side path, protected-area, conflict, secret, and size checks. Those "
    "checks are a filesystem boundary over text, not an OS sandbox: code the "
    "model later writes is not prevented by them from opening an absolute path "
    "at run time."
)

#: Top-level verdicts. Each states what was really observed, and none of them
#: claims that a real remote provider answered -- that is what
#: ``GenerationEvidence.mock_transport`` and the integration test in
#: ``tests/test_project_generation_real_provider.py`` are for.
VERDICT_REAL_PROVIDER_NOT_VERIFIED = "real_provider_not_verified"
VERDICT_GENERATION_FAILED = "generation_failed"
VERDICT_MATERIALISATION_FAILED = "materialisation_failed"
VERDICT_TESTS_FAILED = "tests_failed"
VERDICT_ACCEPTANCE_FAILED = "acceptance_failed"
VERDICT_APPROVAL_REQUIRED = "approval_required"
VERDICT_BUILD_FAILED = "build_failed"
#: The operator's own stated bound on generative attempts was already exhausted,
#: so the provider was **not asked**. Distinct from ``generation_failed``, which
#: means a call happened and produced nothing usable: a reader must be able to
#: tell "we chose not to spend" from "we spent and got nothing".
VERDICT_GENERATION_REFUSED = "generation_refused"

#: The project's own tests **really ran and really passed** on the generation
#: path, so this invocation did verify the artefact it made. The verdict is
#: deliberately transport-neutral: which transport produced the text -- a remote
#: model or an injected one -- is a *separate fact*, carried by
#: ``GenerationEvidence.mock_transport`` / ``.provider_name`` / ``.model_name``.
#: Putting the transport in the verdict as well is what produced the old
#: misreading, where ``real_provider_not_verified`` sat beside a passing suite and
#: implied the project was unverified. It also never claims
#: ``REAL LLM PROJECT GENERATION VERIFIED``, which needs a real remote call.
VERDICT_PROJECT_VERIFIED = "project_verified"

#: The source described more files than the workspace was allowed to receive: at
#: least one declared file was refused by a path, protected-area, secret, size, or
#: conflict check, while the rest were written and their tests may well have
#: passed. It is a success for the *path* and a failure for the *workflow's*
#: promise to say what happened, so it is neither "verified" nor "failed".
VERDICT_MATERIALISATION_INCOMPLETE = "materialisation_incomplete"

#: The project was produced by the operator-declared creation command, its own
#: tests really ran and really passed, and the ``AcceptanceGate`` returned PASS.
#: No model, provider, or API key was involved in producing or judging it. This
#: is the only verdict the declared-command path can reach, and it still does not
#: claim that model-generated code is any good.
VERDICT_DECLARED_COMMAND_VERIFIED = "declared_command_verified"

# ---------------------------------------------------------------------------
# Separated stage statuses
# ---------------------------------------------------------------------------
#
# The factory reports four independent stages. Each has its own field and its own
# mutually exclusive vocabulary, because collapsing any two of them is how
# "a model answered", "a process exited 0", and "the operator's criteria held"
# get mistaken for one another. Every value is derived from an observed fact;
# none is a summary string.
#
# ``"unknown"`` is a *real* value, not a placeholder: it means the fact could not
# be established, which is a different statement from failure and a very
# different statement from success. Nothing is ever rounded to success.

#: Values :attr:`ProjectWorkflowResult.generation_status` may take.
GENERATION_DISABLED = "disabled"
GENERATION_REFUSED_LIMIT = "refused_limit"
GENERATION_FAILED_TRANSPORT = "failed_transport"
GENERATION_FAILED_RESPONSE = "failed_response"
GENERATION_FAILED_MATERIALISATION = "failed_materialisation"
GENERATION_SUCCEEDED = "succeeded"
GENERATION_STATUSES = (
    GENERATION_DISABLED,
    GENERATION_REFUSED_LIMIT,
    GENERATION_FAILED_TRANSPORT,
    GENERATION_FAILED_RESPONSE,
    GENERATION_FAILED_MATERIALISATION,
    GENERATION_SUCCEEDED,
)

#: Values :attr:`ProjectWorkflowResult.execution_status` may take.
#:
#: There is deliberately **no** ``limit_reached`` member. The harness's
#: ``LIMIT_REACHED`` is a fact about the harness loop and is reported verbatim in
#: :attr:`ProjectWorkflowResult.harness_status`; it is not a fact about the process
#: the run dispatched, and the acceptance harness reaches it on an ordinary
#: successful run. A value here that only conflating the two could produce would be
#: an invitation to conflate them.
EXECUTION_NOT_DISPATCHED = "not_dispatched"
EXECUTION_DISPATCHED = "dispatched"
EXECUTION_WAITING_FOR_APPROVAL = "waiting_for_approval"
EXECUTION_FAILED = "failed"
EXECUTION_SUCCEEDED = "succeeded"
EXECUTION_UNKNOWN = "unknown"
EXECUTION_STATUSES = (
    EXECUTION_NOT_DISPATCHED,
    EXECUTION_DISPATCHED,
    EXECUTION_WAITING_FOR_APPROVAL,
    EXECUTION_FAILED,
    EXECUTION_SUCCEEDED,
    EXECUTION_UNKNOWN,
)

#: Default bound on generative attempts for one workflow invocation. One: the
#: workflow asks the provider once and judges what comes back. It does not retry,
#: and it does not spend a second time hoping for a better answer.
DEFAULT_MAX_GENERATION_ATTEMPTS = 1


def model_project_source(configured: bool, mock_transport: bool) -> str:
    """Name the source of the project *text*, or say it is not established.

    The three answers are distinct facts and the third one is not a failure:

    ``"declared_command"``
        No generation provider is composed; an operator-declared command is the
        only possible producer of the artefacts.
    ``"injected_transport"``
        A generation provider is composed and the operator declared its transport
        as injected/mock. Text came from a transport this project itself supplied.
    ``"model_transport"``
        A generation provider is composed whose transport is not declared as
        injected. That is a *transport*, not proof that a remote model answered:
        nothing in this module can establish that, and no field here claims it.
    """
    if not configured:
        return "declared_command"
    return "injected_transport" if mock_transport else "model_transport"

#: Relative paths excluded from the workspace diff. CPython writes byte-code
#: caches next to the generated sources as an import side effect; reporting them
#: as "created project files" would misrepresent the project's contents.
_SNAPSHOT_EXCLUDED_DIRECTORY_NAMES = frozenset({"__pycache__"})


class ProjectWorkflowError(ValueError):
    """The workflow is composed incorrectly or cannot be evaluated."""


class UnknownWorkflowError(LookupError):
    """The requested declaration is not part of this workflow composition."""


# ---------------------------------------------------------------------------
# Read-only views of the specification (available BEFORE execution)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CriterionView:
    """One acceptance criterion, as the operator declared it."""

    criterion_id: str
    description: str
    requirement_id: str
    required: bool = True
    #: The deterministic, non-authoritative workspace check this criterion is
    #: judged by. It names a workspace fact and never an executable.
    expectation_kind: str = ""
    expectation_path: str = ""
    expectation_exists: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "criterion_id": self.criterion_id,
            "description": self.description,
            "requirement_id": self.requirement_id,
            "required": self.required,
            "expectation_kind": self.expectation_kind,
            "expectation_path": self.expectation_path,
            "expectation_exists": self.expectation_exists,
        }


@dataclass(frozen=True)
class ProjectSpecificationView:
    """The task specification and plan, visible before the action runs.

    Everything here is derived from the operator's declaration and acceptance
    spec. Nothing is reachable from a request, a decision, or a model.
    """

    task_id: str
    title: str
    goal: str
    requirements: tuple[dict[str, Any], ...] = ()
    acceptance_criteria: tuple[CriterionView, ...] = ()
    build_declaration_id: str = ""
    verification_declaration_id: str = ""
    #: The declared argv of each step. This is operator-declared and visible to
    #: the operator; it is never taken from a caller.
    build_command: tuple[str, ...] = ()
    verification_command: tuple[str, ...] = ()
    #: The deterministic planner's plan for the declared goal, produced by the
    #: same ``Planner`` the harness uses, so the plan is available before the
    #: action runs. It carries intentions only and no authority.
    planned_steps: tuple[dict[str, Any], ...] = ()
    plan_fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "goal": self.goal,
            "requirements": [dict(item) for item in self.requirements],
            "acceptance_criteria": [c.to_dict() for c in self.acceptance_criteria],
            "build_declaration_id": self.build_declaration_id,
            "verification_declaration_id": self.verification_declaration_id,
            "build_command": list(self.build_command),
            "verification_command": list(self.verification_command),
            "planned_steps": [dict(item) for item in self.planned_steps],
            "plan_fingerprint": self.plan_fingerprint,
        }


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkflowStepView:
    """One run the workflow performed, as it was actually recorded."""

    step: str
    declaration_id: str
    run_id: str
    state: str
    success: bool
    error: Optional[str] = None
    acceptance_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "declaration_id": self.declaration_id,
            "run_id": self.run_id,
            "state": self.state,
            "success": self.success,
            "error": self.error,
            "acceptance_status": self.acceptance_status,
        }


@dataclass(frozen=True)
class VerificationOutcome:
    """The result of really executing the generated project's own test suite.

    ``exit_code`` is the real process exit code the local adapter observed, not
    a string the workflow composed. ``success`` is ``False`` unless that exit
    code equals the declared expectation.
    """

    step_declaration_id: str
    command: tuple[str, ...]
    declared_expectation: str
    declared_expected_exit_code: int = 0
    observed_exit_code: Optional[int] = None
    outcome_status: str = ""
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    truncated: bool = False
    run_id: str = ""
    state: str = ""
    error: Optional[str] = None
    messages: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_declaration_id": self.step_declaration_id,
            "command": list(self.command),
            "declared_expectation": self.declared_expectation,
            "declared_expected_exit_code": self.declared_expected_exit_code,
            "observed_exit_code": self.observed_exit_code,
            "outcome_status": self.outcome_status,
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "truncated": self.truncated,
            "run_id": self.run_id,
            "state": self.state,
            "error": self.error,
            "messages": list(self.messages),
        }


@dataclass(frozen=True)
class ProjectWorkflowResult:
    """The complete, directly inspectable outcome of one workflow invocation.

    ``TaskRunResponse`` has no field for created files and no field for the
    acceptance verdict, so this is a **dedicated** response type rather than a
    redefinition of that public contract. No field of ``TaskRunResponse`` is
    changed by this module.

    The fields below ``run_record`` are the model-driven path's additions. Every
    one of them has a default, so a caller of the declared-command path sees the
    same values it always did and no existing field changed meaning.
    """

    run_id: str
    state: str
    success: bool
    acceptance_status: str
    specification: ProjectSpecificationView
    steps: tuple[WorkflowStepView, ...] = ()
    verification: Optional[VerificationOutcome] = None
    created_files: tuple[str, ...] = ()
    deleted_files: tuple[str, ...] = ()
    verification_created_files: tuple[str, ...] = ()
    verification_deleted_files: tuple[str, ...] = ()
    error: Optional[str] = None
    messages: tuple[str, ...] = ()
    #: The durable run record of the build run, read back through the existing
    #: ``RunStore``. It is the raw object, so ``run_record.events`` can be
    #: inspected for the recorded acceptance verdict.
    run_record: Any = None
    #: The single honest top-level verdict. It is derived from the observed
    #: facts below and never from a message string.
    verdict: str = ""
    #: The *declared* (prospective) verdict name this workflow can reach when a
    #: real provider is verified end to end. It is data so that a report can
    #: contrast what was achieved with what remains unproven, without a document
    #: having to embed a prompt.
    verdict_expected_with_real_provider: str = ""
    #: Which provider produced the text, how much it used, and how the text
    #: arrived. ``None`` when no generation stage ran at all.
    generation: Optional[GenerationEvidence] = None
    #: The model text, parsed and validated. Present even when it failed, so the
    #: fail-closed reason is inspectable; empty on the declared-command path.
    parsed_project: Optional[ParsedModelProject] = None
    #: What was really written to disk for the model's files.
    materialisation: Optional[MaterialisationOutcome] = None
    #: True when the caller composed a generation provider.
    generation_configured: bool = False
    #: True when a declared creation command was dispatched for this invocation.
    declared_command_dispatched: bool = False

    # --- the four separated stage statuses ---------------------------------
    #
    # These are additions, not redefinitions: ``success``, ``state``,
    # ``acceptance_status``, ``verification``, and ``generation`` keep exactly the
    # meaning they had. The point of the four fields below is that no reader has
    # to infer one stage from another's field.

    #: One of :data:`GENERATION_STATUSES`. Answers "what happened when we asked
    #: the provider?", which is a different question from "did a process exit 0?".
    generation_status: str = GENERATION_DISABLED
    #: One of :data:`EXECUTION_STATUSES`. Answers the narrow question "what did the
    #: adapter observe for the process this run dispatched?". It is derived from
    #: the executed ``ExecutionResult`` -- ``EXECUTION_SUCCESS`` is
    #: :data:`EXECUTION_SUCCEEDED`, a real non-zero exit is
    #: :data:`EXECUTION_FAILED`, and no result at all is
    #: :data:`EXECUTION_NOT_DISPATCHED`. ``unknown`` means the question is open.
    execution_status: str = EXECUTION_NOT_DISPATCHED
    #: The build run's **harness lifecycle status**, verbatim
    #: (``COMPLETED`` / ``LIMIT_REACHED`` / ``FAILED`` / ``WAITING_FOR_APPROVAL``),
    #: or ``""`` when no declared build was dispatched. It is kept separate from
    #: :attr:`execution_status` on purpose, and the separation is load-bearing: the
    #: acceptance harness currently reaches ``LIMIT_REACHED`` on an ordinary,
    #: fully successful declared run -- it exhausts its iteration budget without
    #: ever entering the terminal ``COMPLETED`` phase -- while the action it
    #: dispatched really did exit 0 and the acceptance gate really did pass. The
    #: two are therefore never collapsed, and ``LIMIT_REACHED`` is never presented
    #: as an ordinary completed success *or* used to overwrite a successful
    #: execution.
    harness_status: str = ""
    #: ``"pass"`` / ``"fail"`` / ``"not_evaluated"`` -- see the required
    #: ``acceptance_status`` field above, which keeps its original meaning. It is
    #: the *acceptance* verdict and never the test-run verdict.
    #: The declared verification command's real process exit code, or ``None``
    #: when no process exit code was observed. ``None`` is not ``0``.
    verification_exit_code: Optional[int] = None
    #: Which of the three possible producers of the project *text* this
    #: invocation used. See :func:`model_project_source`. It is a statement about
    #: a transport, not about a remote model having answered.
    project_source: str = "declared_command"
    #: How many generative provider *attempts this workflow initiated*, or
    #: ``None`` when no generation stage ran at all (so the question does not
    #: apply -- which is not the same answer as ``0``). It is correlated with
    #: :attr:`max_generation_attempts`: ``0`` means the bound was already
    #: exhausted and the provider was deliberately not asked.
    #:
    #: Why this counts workflow-initiated attempts and not raw provider calls: the
    #: decision layer exposes no call record, so a raw count is not observable
    #: from here. A number this module cannot measure is not reported at all,
    #: rather than reported as a guess. The physical call count *is* measured, in
    #: the tests, from the transport the test itself composed.
    generation_attempts_spent: Optional[int] = None
    #: The operator's bound that was in force for this invocation.
    max_generation_attempts: int = DEFAULT_MAX_GENERATION_ATTEMPTS

    @property
    def build_run_id(self) -> str:
        """Run id of the build run, when the workflow reached the build step."""
        for step in self.steps:
            if step.step == "build":
                return step.run_id
        return ""

    @property
    def verification_run_id(self) -> str:
        """Run id of the verification run, when the workflow reached it."""
        for step in self.steps:
            if step.step == "verify":
                return step.run_id
        return ""

    @property
    def generated_files(self) -> tuple[str, ...]:
        """Files the model declared and that passed every check.

        Empty when no generation ran or when it failed closed. This is the
        model's *declared* file list, not a claim about what is on disk; the
        files really written are in :attr:`materialisation`.
        """
        if self.parsed_project is None:
            return ()
        return tuple(item.path for item in self.parsed_project.files)

    @property
    def materialisation_rejections(self) -> tuple[Any, ...]:
        """Every refusal, from parsing *and* from writing, in one place."""
        rejections: list[Any] = []
        if self.parsed_project is not None:
            rejections.extend(self.parsed_project.rejections)
        if self.materialisation is not None:
            rejections.extend(self.materialisation.rejections)
        return tuple(rejections)

    def to_dict(self) -> dict[str, Any]:
        """Bounded, JSON-safe projection for the existing API surface."""

        def _record_summary(record: Any) -> Any:
            summary = getattr(record, "summary", None)
            return summary() if callable(summary) else None

        return {
            "run_id": self.run_id,
            "state": self.state,
            "success": self.success,
            "acceptance_status": self.acceptance_status,
            "specification": self.specification.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "verification": (
                self.verification.to_dict() if self.verification is not None else None
            ),
            "verification_exit_code": self.verification_exit_code,
            "generation_status": self.generation_status,
            "execution_status": self.execution_status,
            "harness_status": self.harness_status,
            "project_source": self.project_source,
            "generation_attempts_spent": self.generation_attempts_spent,
            "max_generation_attempts": self.max_generation_attempts,
            "created_files": list(self.created_files),
            "deleted_files": list(self.deleted_files),
            "verification_created_files": list(self.verification_created_files),
            "verification_deleted_files": list(self.verification_deleted_files),
            "error": self.error,
            "messages": list(self.messages),
            "run_record": _record_summary(self.run_record),
            "verdict": self.verdict,
            "verdict_expected_with_real_provider": (
                self.verdict_expected_with_real_provider
            ),
            "generation_configured": self.generation_configured,
            "declared_command_dispatched": self.declared_command_dispatched,
            "generation": self.generation.to_dict() if self.generation else None,
            "parsed_project": (
                self.parsed_project.to_dict() if self.parsed_project else None
            ),
            "materialisation": (
                self.materialisation.to_dict() if self.materialisation else None
            ),
        }


# ---------------------------------------------------------------------------
# Composition registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectWorkflowRegistry:
    """Operator-owned binding from a build declaration to its verification step.

    The registry is the *only* thing a caller of :func:`run_project_workflow`
    names: a declaration id that must already appear as a key here. A caller
    therefore chooses *which* declared workflow runs, never *what* runs.
    """

    verification_declarations: Mapping[str, str] = field(default_factory=dict)
    steps: tuple[str, ...] = ("build", "verify")

    def __post_init__(self) -> None:
        mapping = dict(self.verification_declarations)
        for build_id, verify_id in mapping.items():
            if not isinstance(build_id, str) or not build_id.strip():
                raise ProjectWorkflowError(
                    "workflow registry keys must be non-empty declaration ids"
                )
            if not isinstance(verify_id, str) or not verify_id.strip():
                raise ProjectWorkflowError(
                    f"workflow registry entry {build_id!r} must name a non-empty "
                    "verification declaration id"
                )
        object.__setattr__(self, "verification_declarations", mapping)
        object.__setattr__(self, "steps", tuple(self.steps))

    def verification_declaration_for(self, build_declaration_id: str) -> str:
        """Return the verification declaration bound to ``build_declaration_id``."""
        key = str(build_declaration_id).strip()
        if key not in self.verification_declarations:
            raise UnknownWorkflowError(
                f"no workflow is composed for build declaration {key!r}"
            )
        return self.verification_declarations[key]


# ---------------------------------------------------------------------------
# Measured workspace diff
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Snapshot:
    files: Mapping[str, int]


def _is_link_like(entry: os.DirEntry) -> bool:
    """True when an entry is a link, a symlink, or a Windows reparse point.

    ``DirEntry.is_symlink()`` is not sufficient on Windows: an NTFS **junction**
    reports ``False`` there, and ``is_dir(follow_symlinks=False)`` reports
    ``True``, so a naive walk descends through it. A junction inside the workspace
    pointing outside it would then put files that are not in the workspace into
    the created-file diff -- reporting paths the workflow never created, and
    attributing someone else's files to this run. Checking each component for a
    reparse point closes that, in the same spirit as the containment check the
    materialiser already performs on resolved paths.
    """
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return True
    try:
        if os.path.isdir(entry.path) and os.path.isjunction(entry.path):
            return True
    except (OSError, AttributeError):
        # ``os.path.isjunction`` exists from CPython 3.12; on anything older the
        # symlink check above is all that is available, and that is stated rather
        # than silently assumed.
        try:
            return bool(os.lstat(entry.path).st_reparse_tag)
        except (OSError, AttributeError):
            return False
    return False


def _snapshot_workspace(root: Path) -> _Snapshot:
    """Walk ``root`` and record every regular file's relative path and size.

    Excluded: directories named in ``_SNAPSHOT_EXCLUDED_DIRECTORY_NAMES`` (the
    interpreter's byte-code cache) and anything link-like -- a symbolic link, a
    junction, or any other reparse point (see :func:`_is_link_like`) -- so the diff
    describes the files the workflow's commands created rather than their side
    effects, and never follows a link out of the workspace.
    """
    files: dict[str, int] = {}
    root = Path(root)
    if not root.is_dir():
        return _Snapshot(files)
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if _is_link_like(entry):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in _SNAPSHOT_EXCLUDED_DIRECTORY_NAMES:
                        continue
                    stack.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                relative = Path(entry.path).relative_to(root).as_posix()
                files[relative] = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return _Snapshot(files)


def _diff_snapshots(
    before: _Snapshot, after: _Snapshot
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    created = tuple(sorted(set(after.files) - set(before.files)))
    deleted = tuple(sorted(set(before.files) - set(after.files)))
    return created, deleted


# ---------------------------------------------------------------------------
# Small helpers over the durable run record
# ---------------------------------------------------------------------------


def _acceptance_event_metadata(record: Any, *, run_id: str, task_id: str) -> dict:
    """Read the recorded acceptance verdict for ``run_id``/``task_id``.

    Returns ``{}`` when no verdict was recorded: a missing verdict is never
    reported as a pass.
    """
    events = getattr(record, "events", ()) or ()
    for event in reversed(tuple(events)):
        event_type = getattr(event, "event_type", None)
        if getattr(event_type, "name", "") != EventType.ACCEPTANCE_COMPLETED.name:
            continue
        metadata = dict(getattr(event, "metadata", {}) or {})
        if metadata.get("run_id") not in (None, run_id):
            continue
        if metadata.get("task_id") not in (None, task_id):
            continue
        if "status" in metadata:
            return metadata
    return {}


def _acceptance_status(record: Any, *, run_id: str, task_id: str) -> str:
    metadata = _acceptance_event_metadata(record, run_id=run_id, task_id=task_id)
    return str(metadata.get("status") or "not_evaluated")


# ---------------------------------------------------------------------------
# Specification construction (before execution)
# ---------------------------------------------------------------------------


def _criterion_views(
    spec: AcceptanceSpec, criteria: Sequence[AcceptanceCriterion]
) -> tuple[CriterionView, ...]:
    views: list[CriterionView] = []
    for criterion in criteria:
        expectation = spec.expectations.get(criterion.criterion_id)
        views.append(
            CriterionView(
                criterion_id=criterion.criterion_id,
                description=criterion.description,
                requirement_id=criterion.requirement_id,
                required=bool(criterion.required),
                expectation_kind=type(expectation).__name__ if expectation else "",
                expectation_path=str(getattr(expectation, "relative_path", "") or ""),
                expectation_exists=bool(getattr(expectation, "exists", True)),
            )
        )
    return tuple(views)


def _planned_steps(goal: str, *, workflow_id: str) -> tuple[tuple[dict, ...], str]:
    """Plan the declared goal with the existing deterministic Planner.

    The run-bound plan the harness actually uses is bound to the run id, which
    only exists once the build run starts. This is the same deterministic
    planner applied to the same operator-declared goal, presented before the
    action runs so the plan is inspectable up front. It carries intentions only
    and grants no authority.
    """
    if not goal.strip():
        return (), ""
    try:
        plan: ExecutionPlan = Planner().plan_for_run(
            goal=goal, run_id=f"planning-{workflow_id}", task_id=workflow_id
        )
    except Exception:  # noqa: BLE001 - a preview must never block the run
        return (), ""
    return tuple(step.to_dict() for step in plan.steps), plan.fingerprint


def build_specification(
    service: ForgeApiService,
    registry: ProjectWorkflowRegistry,
    build_declaration_id: str,
) -> ProjectSpecificationView:
    """Return the specification, criteria, and plan -- before anything runs.

    Read-only: this performs no execution, freezes no scope, and writes nothing.
    """
    build_declaration, verify_declaration = _resolve_declarations(
        service, registry, build_declaration_id
    )
    spec = _acceptance_spec_for(service, build_declaration)
    goal = build_declaration.intent or build_declaration.declaration_id
    planned, fingerprint = _planned_steps(
        goal, workflow_id=build_declaration.declaration_id
    )
    return ProjectSpecificationView(
        task_id=f"task-accept-{build_declaration.declaration_id}",
        title=build_declaration.declaration_id,
        goal=goal,
        requirements=tuple(
            {
                "requirement_id": requirement.requirement_id,
                "description": requirement.description,
                "required": bool(requirement.required),
            }
            for requirement in _requirements_for(spec)
        ),
        acceptance_criteria=_criterion_views(spec, spec.criteria),
        build_declaration_id=build_declaration.declaration_id,
        verification_declaration_id=verify_declaration.declaration_id,
        build_command=tuple(build_declaration.command),
        verification_command=tuple(verify_declaration.command),
        planned_steps=planned,
        plan_fingerprint=fingerprint,
    )


def _requirements_for(spec: AcceptanceSpec) -> tuple[Requirement, ...]:
    """Derive one requirement per distinct criterion requirement id.

    The acceptance spec is the operator's statement of what must be true; the
    requirement view is a projection of it, not a second source of acceptance.
    Two criteria that share a requirement id are one requirement: it is
    required when either of them is, so the projection cannot weaken the
    operator's own required/optional declarations.
    """
    ordered: list[str] = []
    descriptions: dict[str, str] = {}
    required: dict[str, bool] = {}
    for criterion in spec.criteria:
        key = criterion.requirement_id.strip() or criterion.criterion_id
        if key not in descriptions:
            ordered.append(key)
            descriptions[key] = criterion.description
            required[key] = bool(criterion.required)
        else:
            required[key] = required[key] or bool(criterion.required)
    return tuple(
        Requirement(
            requirement_id=key,
            description=descriptions[key],
            required=required[key],
        )
        for key in ordered
    )


# ---------------------------------------------------------------------------
# Composition validation (new check: a mismatched binding fails closed)
# ---------------------------------------------------------------------------


def _resolve_declarations(
    service: ForgeApiService,
    registry: ProjectWorkflowRegistry,
    build_declaration_id: str,
) -> tuple[ExecutionDeclaration, ExecutionDeclaration]:
    """Resolve both declarations and prove they are one workflow.

    This is a **composition-consistency** check, not a new authorization
    mechanism and not a grant. It refuses, before any scope is frozen or any
    process is started, a binding whose two declared commands do not share the
    operator's profile, working directory, and executable. Without it, a
    mismatched registry entry would be discovered only as a mid-run denial, or
    (worse) a build and its verification could silently target different
    perimeters.

    The authority question -- whether these commands may run at all -- remains
    entirely with the existing ``RunScope``/``ExecutionPolicy``/approval chain.
    """
    if not isinstance(service, ForgeApiService):
        raise ProjectWorkflowError("service must be a ForgeApiService")
    if not isinstance(registry, ProjectWorkflowRegistry):
        raise ProjectWorkflowError("registry must be a ProjectWorkflowRegistry")

    build_key = str(build_declaration_id).strip()
    if not build_key:
        raise ProjectWorkflowError("build_declaration_id must be a non-empty string")

    verify_id = registry.verification_declaration_for(build_key)

    build_declaration = service.declarations.get(build_key)
    if build_declaration is None:
        raise UnknownWorkflowError(
            f"undeclared build execution: {build_key!r}"
        )
    verify_declaration = service.declarations.get(verify_id)
    if verify_declaration is None:
        raise UnknownWorkflowError(
            f"undeclared verification execution: {verify_id!r}"
        )
    if build_declaration.declaration_id == verify_declaration.declaration_id:
        raise ProjectWorkflowError(
            "a workflow's build and verification steps must be two distinct "
            "declarations"
        )
    for field_name in ("profile_id", "working_directory"):
        build_value = getattr(build_declaration, field_name)
        verify_value = getattr(verify_declaration, field_name)
        if build_value != verify_value:
            raise ProjectWorkflowError(
                f"workflow declarations disagree on {field_name}: "
                f"{build_key!r} declares {build_value!r} but {verify_id!r} declares "
                f"{verify_value!r}"
            )
    if build_declaration.executable != verify_declaration.executable:
        raise ProjectWorkflowError(
            f"workflow declarations disagree on the executable: {build_key!r} "
            f"declares {build_declaration.executable!r} but {verify_id!r} declares "
            f"{verify_declaration.executable!r}"
        )
    return build_declaration, verify_declaration


def _acceptance_spec_for(
    service: ForgeApiService, declaration: ExecutionDeclaration
) -> AcceptanceSpec:
    """Return the operator's acceptance spec for a declaration, or fail closed."""
    spec = service.acceptance_specs.get(declaration.declaration_id)
    if spec is None:
        raise ProjectWorkflowError(
            f"no acceptance criteria are declared for {declaration.declaration_id!r}, "
            "so the workflow cannot state what must be true before it runs"
        )
    return spec


# ---------------------------------------------------------------------------
# Verification execution with a measured exit code
# ---------------------------------------------------------------------------


class _RunExecutorProbe:
    """Capture the real ``ExecutionResult`` a dispatched run produced.

    It wraps the *existing* ``RunExecutor`` the service was composed with. Every
    keyword argument is forwarded unchanged, the result is returned untouched,
    and nothing about the authorization chain is altered -- ``RunExecutor``
    still builds the request through the operator's factory and still dispatches
    through ``ExecutionCoordinator``. The wrapper exists because the
    declared-verification response reports the run state and stdout only, while
    the objective evidence for a test suite is the process exit code carried by
    ``ExecutionResult``.
    """

    def __init__(self, executor: Any) -> None:
        self._executor = executor
        self.runs: list[Any] = []

    def __getattr__(self, item: str) -> Any:
        return getattr(self._executor, item)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        run = self._executor.execute(*args, **kwargs)
        self.runs.append(run)
        return run

    def execution_results(self) -> list[ExecutionResult]:
        """Every ``ExecutionResult`` observed across the captured runs."""
        results: list[ExecutionResult] = []
        for run in self.runs:
            results.extend(getattr(run, "execution_results", ()) or ())
        return results


def _run_verification(
    service: ForgeApiService,
    declaration: ExecutionDeclaration,
    *,
    purpose_run_id: str,
    idempotency_key: Optional[str],
) -> tuple[Any, Optional[ExecutionResult]]:
    """Execute the declared verification command and capture its real result.

    ``run_declared_verification`` reports its state and stdout, but the objective
    evidence for a test suite is the process exit code, which only the executed
    ``ExecutionResult`` carries. The probe observes that dispatch through the
    service's own executor. When the probe yields nothing usable the answer is
    ``None`` -- an unknown exit code, never a guessed one.
    """
    original_runtime = service.runtime
    if original_runtime.run_executor is None:
        # No executor means no dispatch path at all; let the entry point report
        # its own refusal rather than inventing a result here.
        return (
            service.run_declared_verification(
                declaration.declaration_id,
                purpose_run_id=purpose_run_id,
                idempotency_key=idempotency_key,
            ),
            None,
        )
    probe = _RunExecutorProbe(original_runtime.run_executor)
    # ``RuntimeContext`` is frozen, so the probe is installed by rebinding the
    # whole runtime to an equivalent one whose only difference is the wrapped
    # executor. Every other field -- settings, registries, orchestrator,
    # harness -- is carried over untouched, and the original runtime is restored
    # in ``finally``.
    service.runtime = replace(original_runtime, run_executor=probe)
    try:
        response = service.run_declared_verification(
            declaration.declaration_id,
            purpose_run_id=purpose_run_id,
            idempotency_key=idempotency_key,
        )
    finally:
        service.runtime = original_runtime
    return response, _result_for_declaration(
        probe.execution_results(), declaration.declaration_id
    )


def _result_for_declaration(
    results: Sequence[ExecutionResult], declaration_id: str
) -> Optional[ExecutionResult]:
    """Select the executed result that belongs to ``declaration_id``.

    The extension point, so a dispatch for some other declaration can never be
    attributed to this workflow's verification step. When no result matches the
    declaration, the answer is ``None`` -- an unknown exit code, never a guessed
    one.
    """
    for result in reversed(tuple(results)):
        recorded = result.metadata.get("declaration_id")
        if recorded is not None and str(recorded) == declaration_id:
            return result
    return None


def _verification_outcome(
    declaration: ExecutionDeclaration,
    response: Any,
    result: Optional[ExecutionResult],
) -> VerificationOutcome:
    messages: list[str] = []
    observed = getattr(result, "exit_code", None) if result is not None else None
    outcome_status = ""
    stdout = ""
    stderr = ""
    duration = 0.0
    truncated = False
    if result is not None:
        outcome_status = getattr(result.outcome_status, "value", "") or ""
        stdout = result.stdout or ""
        stderr = result.stderr or ""
        duration = float(result.duration_seconds or 0.0)
        truncated = bool(result.truncated)
        if observed is None:
            messages.append(
                "the declared verification command produced no process exit code"
            )
    else:
        messages.append(
            "no execution result was produced for the declared verification "
            "command, so its exit code is unknown"
        )

    exit_matches = observed is not None and observed == declaration.expected_exit_code
    success = bool(
        result is not None
        and outcome_status == "EXECUTION_SUCCESS"
        and exit_matches
    )
    if observed is not None and not exit_matches:
        messages.append(
            f"observed exit code {observed} differs from the declared expected "
            f"exit code {declaration.expected_exit_code}"
        )
    if result is not None and outcome_status != "EXECUTION_SUCCESS":
        messages.append(
            "the verification process did not complete successfully "
            f"(outcome {outcome_status or 'unknown'})"
        )
    if success:
        messages.append(
            f"the declared verification command exited {observed}, matching the "
            "declared expectation"
        )

    return VerificationOutcome(
        step_declaration_id=declaration.declaration_id,
        command=tuple(declaration.command),
        declared_expectation="expected_exit_code",
        declared_expected_exit_code=declaration.expected_exit_code,
        observed_exit_code=observed,
        outcome_status=outcome_status,
        success=success,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        truncated=truncated,
        run_id=getattr(response, "run_id", "") or "",
        state=getattr(response, "state", "") or "",
        error=getattr(response, "error", None),
        messages=tuple(messages),
    )


def _step_idempotency_key(
    idempotency_key: Optional[str], step: str
) -> Optional[str]:
    """Derive the idempotency key for one step of the workflow.

    The caller's key identifies the *workflow*. Each step is a separate durable
    operation, so it gets its own key derived from the caller's; a replay of the
    same workflow therefore replays both steps instead of re-running the tests.
    The existing key format is ``[A-Za-z0-9._:-]{8,200}``, so the derived key is
    dropped to ``None`` when appending the step suffix would exceed 200
    characters. That is deliberate: an over-long key would be *refused*, and a
    workflow must not become unrunnable because the operator chose a long key.
    Dropping it only removes the replay protection for that step, never an
    authorization check.
    """
    if idempotency_key is None:
        return None
    derived = f"{idempotency_key}:{step}"
    return derived if len(derived) <= 200 else None


def _failed_verification(
    declaration: ExecutionDeclaration,
    *,
    run_id: str = "",
    state: str = "",
    error: Optional[str] = None,
    message: str = "",
) -> VerificationOutcome:
    return VerificationOutcome(
        step_declaration_id=declaration.declaration_id,
        command=tuple(declaration.command),
        declared_expectation="expected_exit_code",
        declared_expected_exit_code=declaration.expected_exit_code,
        run_id=run_id,
        state=state,
        error=error,
        messages=(message,) if message else (),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _run_workflow_verification(
    service: ForgeApiService,
    declaration: ExecutionDeclaration,
    *,
    after_build: _Snapshot,
    purpose_run_id: str,
    idempotency_key: Optional[str],
) -> tuple[VerificationOutcome, tuple[str, ...], tuple[str, ...]]:
    """Really execute the declared verification command and measure its result.

    Extracted so the declared-command path and the model-driven path perform
    verification through **one** implementation. It attempts verification
    unconditionally: a verification that ran and failed is a result, and hiding
    it behind an earlier step's failure would report less than what was observed.
    A verification whose command cannot be dispatched at all still reports the
    real failure, because there is nothing to verify.

    Returns ``(outcome, created, deleted)``. The created/deleted files are the
    measured diff of the workspace against the post-build snapshot, so they
    describe what verification itself touched rather than what the project
    already contained.
    """
    try:
        verify_response, verify_result = _run_verification(
            service,
            declaration,
            purpose_run_id=purpose_run_id,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:  # noqa: BLE001 - report the break, never fake it
        return (
            _failed_verification(
                declaration,
                error=type(exc).__name__,
                message=(
                    "the verification command could not be dispatched: "
                    f"{type(exc).__name__}: {exc}"
                ),
            ),
            (),
            (),
        )

    outcome = _verification_outcome(declaration, verify_response, verify_result)
    after_verify = _snapshot_workspace(Path(service.workspace.root))
    created, deleted = _diff_snapshots(after_build, after_verify)
    return outcome, created, deleted


# ---------------------------------------------------------------------------
# Model-driven generation: the existing dispatch path, then a safe write
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ModelDispatch:
    """One generation task dispatched through the composed provider path."""

    record: Any
    task_result: Optional[TaskResult]
    run_id: str
    state: str
    error: Optional[str]
    #: True when there is no response to judge at all -- the provider raised, or
    #: no executor was composed -- rather than the provider answering with
    #: something unusable. The two need different statuses: "the transport
    #: failed" and "a response arrived that could not be used" are different
    #: observations, and collapsing them would hide a broken transport behind a
    #: parsing complaint (or vice versa).
    #:
    #: It is deliberately **not** derived from ``TaskResult.success``. That flag
    #: is false for an empty model answer as well as for a broken transport, so
    #: using it here would report a provider that answered with nothing as a
    #: failed transport.
    transport_failed: bool = False


def _dispatch_generation_task(
    service: ForgeApiService,
    *,
    prompt: str,
    task_id: str,
    run_id: str,
) -> _ModelDispatch:
    """Ask the composed provider for a project **through the existing RunExecutor**.

    This is the same dispatch the acceptance slice uses: ``RunExecutor.execute``
    -> ``Orchestrator.dispatch`` -> ``Dispatcher`` -> ``ProviderAgent.run`` ->
    ``Provider.generate``. Nothing new is invented, and -- importantly -- this
    function grants nothing: a model response is text, and the text has no
    authority to run anything. The probe around the executor captures the real
    ``TaskResult``, which is where the model's text lives.

    A run that fails is returned as a failure. It is never replaced with a mock
    response, and no exception raised by the provider is swallowed into a
    success.
    """
    original_runtime = service.runtime
    executor = original_runtime.run_executor
    if executor is None:
        return _ModelDispatch(
            record=None,
            task_result=None,
            run_id=run_id,
            state="NOT_DISPATCHED",
            error="no run executor is composed in this runtime",
            transport_failed=True,
        )
    # The generation run gets durable history through the service's own run store,
    # exactly as the acceptance and declared-verification entry points do, so the
    # physical provider events (``provider_attempt`` / ``provider_result``, with
    # their usage) survive the call and a report does not have to trust the
    # returned object alone. A store that cannot bind is not fatal: the run still
    # happens and the evidence on the result still carries the identity. A failed
    # bind is never replaced by a fabricated record.
    generation_store: Any = None
    try:
        generation_store = service.run_store.bind_run(run_id, task_id=task_id)
    except Exception:  # noqa: BLE001 - persistence must not block a run
        generation_store = None

    probe = _RunExecutorProbe(executor)
    service.runtime = replace(original_runtime, run_executor=probe)
    try:
        run = executor.execute(
            task=Task(
                id=task_id,
                description=prompt,
                context={"run_id": run_id, "forge_generation": True},
            ),
            run_id=run_id,
            run_scope=None,
            allowed_tool_ids=frozenset(),
            workspace=service.workspace,
            run_store=generation_store,
        )
    except Exception as exc:  # noqa: BLE001 - report the break, never fake output
        return _ModelDispatch(
            record=None,
            task_result=None,
            run_id=run_id,
            state="FAILED",
            error=f"{type(exc).__name__}: {exc}",
            transport_failed=True,
        )
    finally:
        service.runtime = original_runtime

    record = _safe_load_record(service, run_id)
    result = getattr(run, "result", None)
    #: ``transport_failed`` means "there is no response to judge". That is exactly
    #: the case where the dispatch returned no ``TaskResult`` at all, which can
    #: only happen when the exception handler below caught the break -- a provider
    #: that raised, an executor that refused, a guard that denied. When a
    #: ``TaskResult`` exists, a response exists, even when the response is an empty
    #: string: "the provider answered with nothing" is a response failure, and
    #: reporting it as a failed transport would blame the wrong layer.
    return _ModelDispatch(
        record=record,
        task_result=result,
        run_id=run_id,
        state=str(getattr(getattr(run, "state", None), "value", "") or ""),
        error=(
            getattr(getattr(run, "error", None), "message", None)
            or getattr(result, "error", None)
        ),
        transport_failed=result is None,
    )


def _model_output_text(result: Any) -> str:
    """The raw text a task result carries, regardless of its success flag."""
    output = getattr(result, "output", "")
    return output if isinstance(output, str) else ""


def _generation_run_id(build_declaration_id: str) -> str:
    """The run id of the model-generation dispatch for a build declaration.

    Named and module-level so a reader -- a test, a report, an operator with the
    run store open -- can locate the durable generation history without
    re-deriving a string format. It follows the existing run-id conventions
    (``run-accept-``, ``run-verify-``) rather than inventing a new one.
    """
    return f"run-generate-{build_declaration_id}"


def _model_output(dispatch: _ModelDispatch) -> str:
    """The model's raw text, or ``""`` when the dispatch produced none.

    ``TaskResult.output`` is where the real model text lives on the existing
    path. An absent or unsuccessful result yields ``""``, which the schema reader
    then refuses as ``empty_model_output`` -- an honest answer, never a mock.
    """
    result = dispatch.task_result
    if result is None or not getattr(result, "success", False):
        return ""
    output = getattr(result, "output", "")
    return output if isinstance(output, str) else ""


def _generation_evidence(
    validator: AIDecisionProvider,
    *,
    transport: str,
    dispatch: _ModelDispatch,
    raw_text: str,
    decision: Optional[Decision],
    validation_errors: tuple[str, ...],
    mock_transport: bool,
    response_produced_project: bool,
) -> GenerationEvidence:
    """Build the data-only proof of which provider produced the project text.

    ``response_produced_project`` is the load-bearing argument. The fields
    ``provider_name``, ``model_name``, ``usage_*`` and ``raw_model_text`` describe
    **the model response that produced the project**, not "a call that was
    started". When the response produced no project -- empty output, malformed
    JSON, a schema violation, an unsafe path, an oversized body -- they are all
    empty, because in that case there is no producer of project text and no
    project-sized measurement to attribute.

    Without this distinction a fail-closed outcome would carry the configured
    provider's name and a token count beside it, and a reader could conclude that
    a model wrote something. ``response_text_observed`` records the separate,
    weaker fact that model text did arrive, so the information is not lost: it is
    labelled for what it is.
    """
    result = dispatch.task_result
    text_observed = bool(isinstance(raw_text, str) and raw_text.strip())
    report_identity = bool(response_produced_project)
    provider_name = ""
    model_name = ""
    usage = None
    if report_identity:
        # The task result is the object the provider itself filled in during the
        # dispatch, so it is the authoritative source. The validator's own
        # observability fields (``last_provider_name`` / ``last_model_name`` /
        # ``last_usage``) are the fallback and must agree with it.
        provider_name = (
            str(getattr(result, "provider", "") or "")
            or str(getattr(validator, "last_provider_name", "") or "")
        )
        model_name = (
            str(getattr(result, "model_name", "") or "")
            or str(getattr(validator, "last_model_name", "") or "")
        )
        usage = getattr(result, "usage", None)
        if usage is None:
            usage = getattr(validator, "last_usage", None)

    return GenerationEvidence(
        transport=transport,
        provider_name=provider_name,
        model_name=model_name,
        usage_input_tokens=getattr(usage, "input_tokens", None),
        usage_output_tokens=getattr(usage, "output_tokens", None),
        usage_estimated_cost=getattr(usage, "estimated_cost", None),
        decision=decision,
        validation_errors=validation_errors,
        mock_transport=mock_transport,
        response_text_observed=text_observed,
        raw_model_text=raw_text if report_identity else "",
    )


def _declared_command_is_dispatchable(
    service: ForgeApiService, declaration: ExecutionDeclaration
) -> bool:
    """True when a declared creation command can actually be dispatched.

    A composition with no harness cannot run the acceptance slice at all, and a
    generation-only composition is a legitimate one. This asks the service, in
    the same terms the service itself uses, rather than guessing from a config
    value.
    """
    return getattr(service.runtime, "harness", None) is not None


def _parse_generation_project(
    raw_text: str,
    *,
    max_file_bytes: Optional[int],
    max_project_bytes: Optional[int],
    max_file_count: Optional[int],
) -> ParsedModelProject:
    """Read the model text as a project, with the caller's bounds where given."""
    kwargs: dict[str, int] = {}
    if max_file_bytes is not None:
        kwargs["max_file_bytes"] = max_file_bytes
    if max_project_bytes is not None:
        kwargs["max_project_bytes"] = max_project_bytes
    if max_file_count is not None:
        kwargs["max_file_count"] = max_file_count
    return parse_model_project(raw_text, **kwargs)


def _honest_verdict(
    *,
    generation_path: bool,
    generation_failed: bool,
    materialisation_failed: bool,
    materialisation_ran: bool,
    approval_pending: bool,
    build_ok: bool,
    verification: Optional[VerificationOutcome],
    acceptance_status: str,
    generation_refused: bool = False,
) -> str:
    """Derive the single top-level verdict from observed facts.

    Order matters and is deliberate: the first thing that actually failed is the
    verdict. A later stage's success can never overwrite an earlier failure.

    The acceptance verdict participates only on the declared-command path, because
    that is the only path where the ``AcceptanceGate`` runs. On the model path
    ``acceptance_status`` is ``not_evaluated`` by construction, so using it as a
    verdict input there would report a failure that never happened.

    Note what this does **not** do: it never returns
    ``REAL LLM PROJECT GENERATION VERIFIED``. That verdict requires a real remote
    provider call, which no test in this repository makes.
    """
    if approval_pending:
        return VERDICT_APPROVAL_REQUIRED
    if generation_refused:
        # The bound was exhausted, so nothing was asked and nothing was spent.
        # This is a deliberate refusal, not a failure of a call, and the two must
        # not share a name.
        return VERDICT_GENERATION_REFUSED
    if generation_failed:
        return VERDICT_GENERATION_FAILED
    if materialisation_failed or (generation_path and not materialisation_ran and not build_ok):
        return VERDICT_MATERIALISATION_FAILED
    if not build_ok:
        # The build's ``success`` means "the action ran *and* its verification
        # passed", so a build that ran and then failed its own acceptance gate
        # arrives here too. Those are different facts and they do not share a
        # verdict: when the action really ran and the gate is what said no, the
        # acceptance verdict is the accurate one. Saying ``build_failed`` there
        # would blame the build for a judgement it did not make.
        if not generation_path and acceptance_status == "fail":
            return VERDICT_ACCEPTANCE_FAILED
        return VERDICT_BUILD_FAILED
    if verification is None or not verification.success:
        return VERDICT_TESTS_FAILED
    if not generation_path and acceptance_status != "pass":
        return VERDICT_ACCEPTANCE_FAILED
    return VERDICT_REAL_PROVIDER_NOT_VERIFIED


def run_project_workflow(
    service: ForgeApiService,
    registry: ProjectWorkflowRegistry,
    build_declaration_id: str,
    *,
    idempotency_key: Optional[str] = None,
    ai_provider: Optional[AIDecisionProvider] = None,
    materialise: bool = True,
    permitted_overwrite_paths: Sequence[str] = (),
    max_file_bytes: Optional[int] = None,
    max_project_bytes: Optional[int] = None,
    max_file_count: Optional[int] = None,
    max_generation_attempts: int = DEFAULT_MAX_GENERATION_ATTEMPTS,
) -> ProjectWorkflowResult:
    """Run the vertical workflow and report what really happened.

    TWO SOURCES OF THE PROJECT, ONE WORKFLOW
    ========================================

    **Without ``ai_provider``** nothing changes: the project is produced by the
    operator-declared creation command and the workflow proves the workflow and
    its verification machinery. See the module docstring for that scope.

    **With ``ai_provider``** the project's *text* is produced by a model through
    the existing provider path, and the declared creation command is **not
    dispatched** -- the model's files are what the declared verification command
    then tests. Composing a provider therefore replaces the source of the text;
    it does not add a second execution path, a second permission system, or a
    second verification mechanism.

    HONEST SCOPE OF THE MODEL PATH
    ==============================

    ``ai_provider`` is an operator-composed instance of the project's existing
    advisory decision provider. Whoever composes it decides what transport sits
    behind it:

    * a real provider adapter -> a real model call (requires the owner's explicit
      permission and a configured key; nothing in this repository does it);
    * an injected or mock provider -> mock text, which proves this **path** and
      proves nothing about model-generated code.

    :class:`GenerationEvidence` records which of the two happened in its
    ``mock_transport`` field, and this workflow never substitutes one for the
    other. This workflow also never reports
    ``REAL LLM PROJECT GENERATION VERIFIED``: that verdict is reserved for a real
    provider call, which is not made here.

    ORDER OF OPERATIONS (model path)
    ================================

    1. **specification** -- the operator's ``AcceptanceSpec`` and plan, read-only.
    2. **generation** -- one generated task through
       ``RunExecutor`` -> ``Orchestrator.dispatch`` -> ``ProviderAgent`` ->
       ``Provider.generate``; the text is read from ``TaskResult.output``, and
       the provider identity and usage from ``last_provider_name`` /
       ``last_model_name`` / ``last_usage``. The text is then parsed against the
       explicit schema and its ``decision`` member is validated through the
       existing fail-closed ladder. An empty, malformed, or rejected response
       stops here with its own reason code and creates nothing.
    3. **materialisation** -- every path, protected area, conflict, secret, and
       size check runs before the first byte is written; refused files are
       reported and never created.
    4. **verification** -- the declared verification command really executes the
       project's own test suite; its real exit code is read from the adapter's
       ``ExecutionResult``. A non-zero exit is ``tests_failed``, never "verified".
    5. **result** -- one response carrying the provider evidence, the generation
       outcome, the file list, the rejections, the test exit code, the acceptance
       verdict, and one honest top-level ``verdict``.

    The acceptance verdict (``acceptance_status``) exists only on the
    declared-command path, because the ``AcceptanceGate`` is driven by that
    slice. On the model path it is ``not_evaluated`` and is never presented as a
    test result. The two measures stay in separate fields.

    THE GENERATION BUDGET
    =====================

    ``max_generation_attempts`` is the operator's bound on how many times this
    invocation may ask the provider for a project. It is checked **before** the
    first call, so exhausting it refuses without spending anything: the result
    then carries ``generation_status == refused_limit`` and
    ``generation_attempts_spent == 0``, and no declared command is run either --
    a refusal must not be routed around by falling back to another source of
    text. The bound is per invocation and is not a retry loop; raising it is the
    operator's decision, and this workflow never raises it on its own.
    """
    if (
        isinstance(max_generation_attempts, bool)
        or not isinstance(max_generation_attempts, int)
        or max_generation_attempts < 0
    ):
        raise ProjectWorkflowError(
            "max_generation_attempts must be a non-negative integer, got "
            f"{max_generation_attempts!r}"
        )
    build_declaration, verify_declaration = _resolve_declarations(
        service, registry, build_declaration_id
    )
    spec = _acceptance_spec_for(service, build_declaration)
    specification = build_specification(
        service, registry, build_declaration.declaration_id
    )

    workspace_root = Path(service.workspace.root)
    before = _snapshot_workspace(workspace_root)

    messages: list[str] = (
        [MODEL_TRANSPORT_IS_NOT_AN_OS_SANDBOX]
        if ai_provider is not None
        else [DECLARED_COMMAND_IS_NOT_A_GENERATOR]
    )
    if ai_provider is not None and getattr(ai_provider, "mock_transport", False):
        messages.append(MOCK_TRANSPORT_IS_NOT_REAL_GENERATION)

    generated = ai_provider is not None and materialise
    generation: Optional[GenerationEvidence] = None
    parsed_project: Optional[ParsedModelProject] = None
    materialisation: Optional[MaterialisationOutcome] = None
    generation_failed = False
    materialisation_failed = False
    materialisation_ran = False
    # Every stage status starts at the honest "no attempt was made" answer and is
    # moved only by an observed fact below. Nothing here is inferred at the end
    # from ``success``.
    generation_status = (
        GENERATION_DISABLED if ai_provider is None else GENERATION_REFUSED_LIMIT
    )
    generation_refused = False
    generation_attempts_spent: Optional[int] = None

    # --- steps 2-3: generate, then write, or stop with a named reason ---
    if generated and max_generation_attempts < 1:
        # Refused before the call, so nothing is spent and nothing is asked. The
        # count is ``0`` rather than ``None``: the stage ran, and the answer to
        # "how many attempts did it spend?" is a definite zero.
        generation_refused = True
        generation_attempts_spent = 0
        messages.append(
            "the operator's generation budget for this invocation is "
            f"{max_generation_attempts}, so the provider was not asked at all"
        )
    elif generated:
        generation_attempts_spent = 0
        # Left deliberately unset until a dispatch outcome is observed: if this
        # stage raises before then, the honest final answer is "unknown", which
        # the safety net at the end supplies. Pre-seeding it with a failure would
        # be a claim about a stage that never reported.
        generation_status = ""
        generation_run_id = _generation_run_id(build_declaration.declaration_id)
        prompt = ai_provider.build_generation_prompt(
            _generation_task_specification(specification, build_declaration, spec),
            spec.criteria,
        )
        dispatch = _dispatch_generation_task(
            service,
            prompt=prompt,
            task_id=f"task-generate-{build_declaration.declaration_id}",
            run_id=generation_run_id,
        )
        # One attempt was initiated. This counts *workflow-initiated* attempts,
        # which is exactly what ``max_generation_attempts`` bounds; the number of
        # individual provider calls a transport makes inside one attempt (a
        # fallback chain, an internal retry) is the transport's own business and
        # is reported from its own record in the test that measures it.
        generation_attempts_spent = 1
        raw_text = _model_output(dispatch)
        decision, validation_errors = decision_for_model_text(
            ai_provider,
            raw_text,
            run_id=generation_run_id,
            task_id=f"task-generate-{build_declaration.declaration_id}",
        )
        parsed_project = _parse_generation_project(
            raw_text,
            max_file_bytes=max_file_bytes,
            max_project_bytes=max_project_bytes,
            max_file_count=max_file_count,
        )
        # The evidence is assembled AFTER the response has been judged, because
        # what it may claim depends on that judgement. See
        # ``_generation_evidence``: a fail-closed outcome reports no producer
        # identity and no measurement, so a reader can never mistake "a call
        # produced nothing usable" for "a model wrote this project".
        generation = _generation_evidence(
            ai_provider,
            transport="task_result",
            dispatch=dispatch,
            raw_text=raw_text,
            decision=decision,
            validation_errors=validation_errors,
            mock_transport=bool(getattr(ai_provider, "mock_transport", False)),
            response_produced_project=parsed_project.ok,
        )
        if not parsed_project.ok:
            generation_failed = True
            generation_status = (
                GENERATION_FAILED_TRANSPORT
                if dispatch.transport_failed
                else GENERATION_FAILED_RESPONSE
            )
            messages.append(
                "the model response could not become a project: "
                f"{parsed_project.fail_closed_reason}"
            )
        else:
            materialisation_ran = True
            materialisation = materialise_project(
                parsed_project.files,
                workspace_root=workspace_root,
                permitted_overwrite_paths=permitted_overwrite_paths,
            )
            if not materialisation.ok:
                materialisation_failed = True
                generation_status = GENERATION_FAILED_MATERIALISATION
                messages.append(
                    "no model file could be written into the workspace: "
                    f"{materialisation.fail_closed_reason}"
                )
            else:
                generation_status = GENERATION_SUCCEEDED
                messages.append(
                    f"the model response was materialised as "
                    f"{len(materialisation.file_list)} file(s) under the workspace root"
                )

    after_generation = _snapshot_workspace(workspace_root)

    # --- the declared creation command, on the path that uses one ---
    build_step: Optional[WorkflowStepView] = None
    build_response: Any = None
    build_record: Any = None
    acceptance_status = "not_evaluated"
    approval_pending = False
    build_ok = False
    declared_command_dispatched = False
    execution_status = EXECUTION_NOT_DISPATCHED
    harness_status = ""

    dispatch_declared_command = (
        not generated
        and not generation_refused
        and _declared_command_is_dispatchable(service, build_declaration)
    )
    if generated:
        # The model's files are the project. A declared creation command would
        # overwrite them, so it is not dispatched, and no acceptance verdict is
        # manufactured for a run that never happened.
        messages.append(
            "the operator-declared creation command was not dispatched: the "
            "project was produced by the composed generation provider"
        )
    elif generation_refused:
        # A refusal to generate must not be routed around by producing the
        # project some other way. Nothing runs, and the result says so.
        messages.append(
            "the operator-declared creation command was not dispatched either: a "
            "refused generation budget stops the invocation rather than silently "
            "changing the source of the project"
        )
    elif not dispatch_declared_command:
        messages.append(
            "no generation provider is composed and no agent harness is "
            "available, so no project source could be produced"
        )

    if dispatch_declared_command:
        declared_command_dispatched = True
        build_response = service.run_accepted_task(
            build_declaration.declaration_id,
            idempotency_key=idempotency_key,
        )
        build_record = _safe_load_record(service, build_response.run_id)
        acceptance_status = _acceptance_status(
            build_record, run_id=build_response.run_id, task_id=build_response.task_id
        )
        build_ok = bool(build_response.success)
        approval_pending = build_response.state == "WAITING_FOR_APPROVAL"
        execution_status = _execution_status_for_build(build_response)
        harness_status = str(build_response.state or "")
        build_step = WorkflowStepView(
            step="build",
            declaration_id=build_declaration.declaration_id,
            run_id=build_response.run_id,
            state=build_response.state,
            success=bool(build_response.success),
            error=build_response.error,
            acceptance_status=acceptance_status,
        )
        if approval_pending:
            messages.append(
                "the action required approval and none was granted; the forbidden "
                "action was not performed"
            )
    elif generated:
        # The generation itself is the "build" step of this path.
        build_ok = bool(generation is not None and not generation_failed
                        and not materialisation_failed and materialisation_ran)
        build_step = WorkflowStepView(
            step="build",
            declaration_id=build_declaration.declaration_id,
            run_id=_generation_run_id(build_declaration.declaration_id),
            state=(
                "GENERATED" if build_ok
                else "GENERATION_FAILED" if generation_failed
                else "MATERIALISATION_FAILED"
            ),
            success=build_ok,
            error=(
                None if build_ok
                else (parsed_project.fail_closed_reason if generation_failed
                      else (materialisation.fail_closed_reason if materialisation
                            else "generation_not_run"))
            ),
            acceptance_status=acceptance_status,
        )

    after_build = _snapshot_workspace(workspace_root)
    measured_created, measured_deleted = _diff_snapshots(before, after_build)

    # --- step 4: real verification, whenever anything could be verified ---
    verification: Optional[VerificationOutcome] = None
    verification_created: tuple[str, ...] = ()
    verification_deleted: tuple[str, ...] = ()
    steps: tuple[WorkflowStepView, ...]
    if approval_pending:
        steps = (build_step,) if build_step is not None else ()
    else:
        verification, verification_created, verification_deleted = (
            _run_workflow_verification(
                service,
                verify_declaration,
                after_build=after_build,
                purpose_run_id=(
                    build_response.run_id if build_response is not None
                    else _generation_run_id(build_declaration.declaration_id)
                ),
                idempotency_key=_step_idempotency_key(idempotency_key, "verify"),
            )
        )
        verify_step = WorkflowStepView(
            step="verify",
            declaration_id=verify_declaration.declaration_id,
            run_id=verification.run_id,
            state=verification.state,
            success=verification.success,
            error=verification.error,
        )
        steps = (build_step, verify_step) if build_step is not None else (verify_step,)

    # --- step 5: the honest aggregate verdict ---
    verification_ok = verification is not None and verification.success
    #: Files the response declared that were refused, from **both** stages that can
    #: refuse one: the parse (an unsafe path, a credential name, an oversized body)
    #: and the write (a protected area, a conflict with an existing file, a failed
    #: write). A generation whose every file was written has none. The run may
    #: still have produced a *working* project from what survived -- and the tests
    #: below say so -- but it is not the project that was asked for, and a result
    #: that called it verified would be describing a different artefact.
    refused_model_files = tuple(
        sorted({item.path for item in materialisation_rejections_of(parsed_project, materialisation)})
    )
    materialisation_incomplete = bool(generated and refused_model_files)

    error: Optional[str] = None
    if approval_pending:
        error = "approval_required"
    elif generation_refused:
        error = "generation_refused"
    elif generation_failed:
        error = parsed_project.fail_closed_reason if parsed_project else "generation_failed"
    elif materialisation_failed:
        error = (
            materialisation.fail_closed_reason
            if materialisation is not None
            else "materialisation_failed"
        )
    elif materialisation_incomplete:
        error = "materialisation_incomplete"
    elif build_response is not None and not build_ok:
        error = build_response.error or f"build_{build_response.state.lower()}"
    elif not generated and not declared_command_dispatched:
        error = "no_project_source"
    elif verification is None:
        error = "verification_not_evaluated"
    elif not verification_ok:
        if verification.observed_exit_code is not None:
            error = "verification_exit_code_mismatch"
        elif verification.outcome_status:
            error = f"verification_{verification.outcome_status.lower()}"
        else:
            error = "verification_not_evaluated"

    # A success means: the source was produced, **every** file the source named
    # was written, and the declared verification passed. A refused file is not a
    # success even when the surviving files happen to pass their tests, because
    # the artefact would then differ from the one the source described.
    success = bool(build_ok and verification_ok and not materialisation_incomplete)
    if generated:
        state = (
            "VERIFIED" if success
            else "GENERATION_FAILED" if generation_failed
            else "MATERIALISATION_FAILED" if materialisation_failed
            else "MATERIALISATION_INCOMPLETE" if materialisation_incomplete
            else "VERIFICATION_FAILED" if verification is not None and not verification.success
            else "PROJECT_CREATED_TESTS_FAILED"
        )
    else:
        state = build_response.state if build_response is not None else "NOT_DISPATCHED"
        if success:
            state = "VERIFIED"
        elif verification is not None and not verification.success:
            state = "VERIFICATION_FAILED"

    verdict = _honest_verdict(
        generation_path=generated,
        generation_failed=generation_failed,
        materialisation_failed=materialisation_failed,
        materialisation_ran=materialisation_ran,
        approval_pending=approval_pending,
        build_ok=build_ok,
        verification=verification,
        acceptance_status=acceptance_status,
        generation_refused=generation_refused,
    )
    # A success needs its own name. ``_honest_verdict``'s last resort,
    # ``real_provider_not_verified``, is the honest answer for a *failure* -- it
    # says what was never established -- but beside a passing test suite it reads
    # as though the project were unverified when only a real remote model was.
    # The two success paths therefore get a verdict that names the producer and
    # the verification together, and neither claims a remote model answered.
    if verdict == VERDICT_REAL_PROVIDER_NOT_VERIFIED and success:
        verdict = (
            VERDICT_PROJECT_VERIFIED
            if generated
            else VERDICT_DECLARED_COMMAND_VERIFIED
        )
    elif generated and materialisation_incomplete and verification_ok:
        # The surviving project really did pass its own tests. That is worth
        # saying -- and it is not "verified", because a file the source asked for
        # was refused. The verdict names the incomplete write instead of either
        # hiding it or discarding the passing run.
        verdict = VERDICT_MATERIALISATION_INCOMPLETE
    if generation_status not in GENERATION_STATUSES:
        # No dispatch outcome was observed, so the honest answer is that the
        # question is open -- not that it failed, and certainly not that it
        # succeeded.
        generation_status = "unknown"
    if execution_status == EXECUTION_NOT_DISPATCHED and generated and materialisation_ran:
        # The generation itself was the execution of this path. Saying
        # "not dispatched" here would describe the wrong stage.
        execution_status = EXECUTION_DISPATCHED

    result_run_id = (
        build_response.run_id
        if build_response is not None
        else _generation_run_id(build_declaration.declaration_id)
    )

    return ProjectWorkflowResult(
        run_id=result_run_id,
        state=state,
        success=success,
        acceptance_status=acceptance_status,
        specification=specification,
        steps=steps,
        verification=verification,
        created_files=measured_created,
        deleted_files=measured_deleted,
        verification_created_files=verification_created,
        verification_deleted_files=verification_deleted,
        error=error,
        messages=tuple(messages),
        run_record=build_record,
        verdict=verdict,
        verdict_expected_with_real_provider="REAL LLM PROJECT GENERATION VERIFIED",
        generation=generation,
        parsed_project=parsed_project,
        materialisation=materialisation,
        generation_configured=ai_provider is not None,
        declared_command_dispatched=declared_command_dispatched,
        generation_status=generation_status,
        execution_status=execution_status,
        harness_status=harness_status,
        verification_exit_code=(
            verification.observed_exit_code if verification is not None else None
        ),
        project_source=model_project_source(
            ai_provider is not None,
            bool(getattr(ai_provider, "mock_transport", False)),
        ),
        generation_attempts_spent=generation_attempts_spent,
        max_generation_attempts=max_generation_attempts,
    )


def materialisation_rejections_of(
    parsed_project: Optional[ParsedModelProject],
    materialisation: Optional[MaterialisationOutcome],
) -> tuple[Any, ...]:
    """Every refusal a generation produced, from the parse and from the write.

    Both stages can refuse a declared file, and a reader that consulted only one
    of them would under-report what was lost.
    """
    rejections: list[Any] = []
    if parsed_project is not None:
        rejections.extend(parsed_project.rejections)
    if materialisation is not None:
        rejections.extend(materialisation.rejections)
    return tuple(rejections)


def _execution_status_for_build(build_response: Any) -> str:
    """Classify the *executed action* of a declared acceptance run.

    Deliberately **not** a mapping of the harness lifecycle status. That status is
    a fact about the harness loop, and it is reported verbatim in
    :attr:`ProjectWorkflowResult.harness_status`; it is not a fact about the
    process the run dispatched. Using it here is what would let a genuinely
    successful action be reported as ``limit_reached`` merely because the harness
    exhausted its iteration budget -- which is what the current acceptance harness
    does on an ordinary successful run.

    The classification therefore follows ``success`` and the state, in that order:

    * ``WAITING_FOR_APPROVAL`` -> the action was not performed at all;
    * ``success`` -> the adapter observed ``EXECUTION_SUCCESS`` *and* the
      ``AcceptanceGate`` returned PASS, so the action really did run and complete;
    * any other terminal state -> the action failed;
    * anything unrecognised -> ``unknown``, which is never a success.
    """
    state = str(getattr(build_response, "state", "") or "")
    if state == "WAITING_FOR_APPROVAL":
        return EXECUTION_WAITING_FOR_APPROVAL
    if bool(getattr(build_response, "success", False)):
        return EXECUTION_SUCCEEDED
    if state in ("FAILED", "LIMIT_REACHED"):
        return EXECUTION_FAILED
    return EXECUTION_UNKNOWN


def _generation_task_specification(
    specification: ProjectSpecificationView,
    declaration: ExecutionDeclaration,
    spec: AcceptanceSpec,
) -> Any:
    """Build the task specification the generation prompt renders.

    Derived from the operator's own declaration and acceptance spec -- the same
    inputs the declared-command path uses -- so the model's brief and the
    operator's criteria cannot describe different projects.
    """
    from app.tasks.specification import TaskSpecification

    return TaskSpecification(
        task_id=specification.task_id,
        title=declaration.declaration_id,
        description=declaration.intent,
        requirements=_requirements_for(spec),
        acceptance_criteria=spec.criteria,
    )


def _safe_load_record(service: ForgeApiService, run_id: str) -> Any:
    """Load the durable run record, tolerating an unavailable store."""
    try:
        return service.run_store.load(run_id)
    except Exception:  # noqa: BLE001 - a missing record is not a fake pass
        return None
