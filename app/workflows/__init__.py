"""End-to-end vertical workflows composed from existing Forge components.

A workflow in this package is an **operator-composed coordinator**: it sequences
components that already exist (specification, planner, the acceptance slice, the
declared-verification slice, the provider dispatch path) and reports what really
happened. It introduces no second orchestrator, no second memory system, no
second authorization mechanism, and no parallel execution path.

See :mod:`app.workflows.project_workflow` for the first such workflow and for its
honestly stated limits. Two sources of the project text are supported and are
never presented as each other:

* **operator-declared command** (the default, no ``ai_provider``) -- the project
  is written by a declared ``python`` command and no model is involved;
* **model response** (``ai_provider`` composed) -- the project text comes from a
  model through the existing provider path, is schema-validated and path-checked
  by :mod:`app.workflows.project_generation`, and is then tested by the declared
  verification command.

**No real provider call is made by this repository.** An injected transport proves
the path and proves nothing about model-generated code quality.

:mod:`app.workflows.step_graph` and :mod:`app.workflows.step_runner` add the
managed multi-step execution mechanism (v0.1). They are the same kind of thing,
not a second execution plane: a step names an operator-declared
``declaration_id``, and every executing step is dispatched through the *existing*
``RunScope`` -> ``RunExecutor`` (``generate_decision=False``) ->
``ExecutionCoordinator`` -> ``AuthorizedExecution`` -> ``LocalExecutionAdapter``
chain, one step at a time and strictly sequentially.
"""

from __future__ import annotations

from app.workflows.project_generation import (
    MOCK_TRANSPORT_IS_NOT_REAL_GENERATION,
    GenerationEvidence,
    MaterialisationOutcome,
    ParsedModelProject,
    RejectedProjectPath,
    materialise_project,
    parse_model_project,
    secret_content_reason,
)
from app.workflows.project_workflow import (
    DECLARED_COMMAND_IS_NOT_A_GENERATOR,
    MODEL_TRANSPORT_IS_NOT_AN_OS_SANDBOX,
    CriterionView,
    ProjectSpecificationView,
    ProjectWorkflowError,
    ProjectWorkflowRegistry,
    ProjectWorkflowResult,
    UnknownWorkflowError,
    VerificationOutcome,
    WorkflowStepView,
    build_specification,
    run_project_workflow,
)
from app.workflows.step_graph import (
    StepAction,
    StepDefinition,
    StepGraph,
    StepGraphError,
    StepGraphSnapshot,
    StepGraphStateError,
    StepGraphStatus,
    StepGraphValidationError,
    StepRecord,
    StepStatus,
    StepVerification,
    VerificationKind,
    VerificationStatus,
)
from app.workflows.step_runner import StepGraphResult, StepGraphRunner

__all__ = [
    "DECLARED_COMMAND_IS_NOT_A_GENERATOR",
    "MODEL_TRANSPORT_IS_NOT_AN_OS_SANDBOX",
    "MOCK_TRANSPORT_IS_NOT_REAL_GENERATION",
    "CriterionView",
    "GenerationEvidence",
    "MaterialisationOutcome",
    "ParsedModelProject",
    "ProjectSpecificationView",
    "ProjectWorkflowError",
    "ProjectWorkflowRegistry",
    "ProjectWorkflowResult",
    "RejectedProjectPath",
    "StepAction",
    "StepDefinition",
    "StepGraph",
    "StepGraphError",
    "StepGraphResult",
    "StepGraphRunner",
    "StepGraphSnapshot",
    "StepGraphStateError",
    "StepGraphStatus",
    "StepGraphValidationError",
    "StepRecord",
    "StepStatus",
    "StepVerification",
    "UnknownWorkflowError",
    "VerificationKind",
    "VerificationOutcome",
    "VerificationStatus",
    "WorkflowStepView",
    "build_specification",
    "materialise_project",
    "parse_model_project",
    "run_project_workflow",
    "secret_content_reason",
]
