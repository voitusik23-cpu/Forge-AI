"""Managed multi-step execution v0.1: the step-graph contract.

WHAT THIS MODULE IS
===================

A *contract and a validator* for a bounded, sequential, dependency-ordered set of
declared execution actions. It is deliberately **not**:

* a second orchestrator -- it plans nothing, chooses nothing, and drives the
  existing ``RunExecutor`` -> ``ExecutionCoordinator`` -> ``AuthorizedExecution``
  -> ``LocalExecutionAdapter`` chain one step at a time
  (:mod:`app.workflows.step_runner` is the driver);
* a second authorization mechanism -- a step names an **operator-declared**
  ``declaration_id`` and nothing else carries authority. A graph cannot introduce
  a command, a workspace, an allowlist entry, or an approval;
* a permission to execute -- a validated graph is a *plan*. Every executing step
  is separately re-authorized by the frozen ``RunScope``, the ``ApprovalPolicy``,
  the ``ExecutionPolicy`` and the coordinator, exactly as any other declared
  execution is.

This module has no execution, no I/O, no clock and no coordinator import, so the
whole contract is testable without spawning anything.

THE STATUS VOCABULARY
=====================

``StepStatus`` is what is *known about one step*:

``PENDING``
    Not started, and no claim has been made for it.
``RUNNING``
    A claim exists and the step was dispatched. **Never a final answer**: a
    persisted ``RUNNING`` is deliberately read back as ``UNKNOWN``, because the
    process that wrote it may have died between the write and the outcome.
``WAITING_APPROVAL``
    The authorization chain refused to run the action because approval is
    required and unresolved. Nothing was performed -- not "failed".
``SUCCEEDED``
    The action ran, its real process exit code matched the declared expectation,
    and every declared verification check passed.
``FAILED``
    The step did not reach a verified success: the process failed, timed out, or
    errored, **or** the process succeeded but a declared verification check did
    not pass, **or** the chain refused it before dispatch (permission, policy,
    approval rejection, idempotency conflict).
``CANCELLED``
    Cancellation was requested before this step started. It was never
    dispatched. A step that *was* running is never reported as ``CANCELLED``.
``BLOCKED``
    A dependency did not succeed, so this step was not allowed to start.
``UNKNOWN``
    It cannot be established whether the action completed. Typically: a claim and
    a dispatch are durably recorded but no outcome survived.

``StepGraphStatus`` is what is *known about the whole attempt*:

``COMPLETED``
    Every declared step is ``SUCCEEDED`` and every declared verification check
    passed. Nothing else produces ``COMPLETED``.
``FAILED``
    At least one step is ``FAILED``.
``CANCELLED``
    Cancellation was requested and at least one step was cancelled before start.
``REFUSED``
    No step failed, but the attempt was refused or is waiting for approval.
``INDETERMINATE``
    At least one step is ``UNKNOWN``: the attempt's outcome is not established.
``INCOMPLETE``
    The caller's step budget was exhausted with steps still ``PENDING``. A later
    :meth:`app.workflows.step_runner.StepGraphRunner.resume` may continue it.

Authority failures dominate the overall verdict
(``INDETERMINATE`` > ``CANCELLED`` > ``FAILED`` > ``REFUSED`` > ``INCOMPLETE`` >
``COMPLETED``), so an unknown or cancelled attempt can never be summarised as a
completed one.

THE GUARANTEE, STATED HONESTLY
==============================

The runner provides **at-most-once dispatch per (graph run, step) claim, as
observed through the existing durable idempotency ledger, and only while that
ledger is readable**. It is *not* exactly-once:

* the ledger is consulted and written on the same filesystem, with no
  distributed transaction, so two processes racing on a lost claim can both be
  admitted in the window before either record exists;
* when the ledger cannot be read or written the runner refuses the step rather
  than treating the absence of a record as "never ran", but a step already
  dispatched whose outcome cannot be recorded leaves the guarantee degraded --
  the result says so instead of hiding it;
* the ledger records *that an action was claimed and how it ended*, never that a
  process's external side effects were atomic.

Everything the runner reports rests on a real ``ExecutionResult`` produced by
the existing execution plane, or is reported as ``UNKNOWN``. A step is never
``SUCCEEDED`` because a plan, a model, or an agent said so.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from app.execution.declaration import (
    ExecutionDeclaration,
    to_execution_request,
)
from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.execution.profile import ProjectExecutionProfile
from app.execution.request import ExecutionRequest

__all__ = [
    "PARAMETER_NAMES",
    "STEP_GRAPH_SCHEMA",
    "STEP_GRAPH_PROJECT_STATE_KEY",
    "REASON_CYCLE",
    "REASON_DUPLICATE_DEPENDENCY",
    "REASON_DUPLICATE_PARAMETER",
    "REASON_DUPLICATE_STEP_ID",
    "REASON_GRAPH_ID_INVALID",
    "REASON_INVALID_PARAMETER_VALUE",
    "REASON_MALFORMED_DEPENDENCY",
    "REASON_MALFORMED_STEP_ID",
    "REASON_NO_STEPS",
    "REASON_PARAMETER_WIDENS_LIMIT",
    "REASON_SELF_DEPENDENCY",
    "REASON_UNKNOWN_DECLARATION",
    "REASON_UNKNOWN_DEPENDENCY",
    "REASON_UNKNOWN_PARAMETER",
    "StepAction",
    "StepDefinition",
    "StepGraph",
    "StepGraphError",
    "StepGraphSnapshot",
    "StepGraphStateError",
    "StepGraphStatus",
    "StepGraphValidationError",
    "StepRecord",
    "StepStatus",
    "StepVerification",
    "VerificationKind",
    "VerificationStatus",
    "build_step_request",
    "declared_plan_of",
    "derive_graph_status",
    "graph_from_declared_plan",
    "read_step_graph_record",
    "ready_step_ids",
]

#: The persisted document version. A reader that does not recognise it refuses
#: the record instead of guessing at a shape.
STEP_GRAPH_SCHEMA = "forge.step_graph.v1"

#: The key a step-graph record occupies inside ``RunStateSnapshot.project_state``.
#: Named here, next to the schema, so a read-only consumer (the recovery planner
#: in :mod:`app.runtime.recovery`) does not have to duplicate the literal and
#: cannot drift away from the writer.
STEP_GRAPH_PROJECT_STATE_KEY = "step_graph"

#: Step and graph ids are restricted to the same character set the existing
#: ``RunStore`` accepts for a run id, because every step is persisted under a run
#: id derived from them. No truncation is ever performed: an over-long
#: combination is refused instead of silently colliding with another.
_STEP_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_GRAPH_ID = re.compile(r"^[A-Za-z0-9._-]{1,96}$")

#: The closed parameter vocabulary a step may vary. Every entry is either
#: containment-checked (``working_directory``) or narrowing-only
#: (``timeout_seconds``); no parameter can widen the operator's declaration, add
#: a command, add an environment variable, or enable network access. An
#: environment override is *absent by design*: the execution policy requires a
#: request's environment to equal the profile's, so allowing it here would only
#: create a channel that always fails closed later.
PARAMETER_NAMES = frozenset({"working_directory", "timeout_seconds"})

#: Reason codes returned by :meth:`StepGraph.validate`. Each names the evidence
#: for the refusal, and callers should not have to parse prose.
REASON_GRAPH_ID_INVALID = "graph_id_invalid"
REASON_NO_STEPS = "no_steps"
REASON_MALFORMED_STEP_ID = "malformed_step_id"
REASON_DUPLICATE_STEP_ID = "duplicate_step_id"
REASON_MALFORMED_DEPENDENCY = "malformed_dependency"
REASON_DUPLICATE_DEPENDENCY = "duplicate_dependency"
REASON_SELF_DEPENDENCY = "self_dependency"
REASON_UNKNOWN_DEPENDENCY = "unknown_dependency"
REASON_CYCLE = "cycle"
REASON_UNKNOWN_DECLARATION = "unknown_declaration"
REASON_UNKNOWN_PARAMETER = "unknown_parameter"
REASON_DUPLICATE_PARAMETER = "duplicate_parameter"
REASON_INVALID_PARAMETER_VALUE = "invalid_parameter_value"
REASON_PARAMETER_WIDENS_LIMIT = "parameter_widens_declared_limit"

#: The ``StepRecord.execution_outcome`` values that prove a command really started.
#:
#: Written by the execution ADAPTER from a process it attempted to create:
#: ``EXECUTION_SUCCESS`` / ``EXECUTION_FAILURE`` / ``EXECUTION_TIMEOUT`` come from a
#: process that ran to a verdict, and ``EXECUTION_ERROR`` covers a spawn that was
#: attempted. Every other outcome on this surface is written by the AUTHORIZATION
#: CHAIN (``APPROVAL_WAITING``, ``POLICY_DENIED``, ...) or by the runner
#: (``REFUSED_*``), i.e. strictly before a process can exist, and ``""`` means no
#: outcome was recorded at all. ``UNKNOWN`` is deliberately NOT here: the runner
#: writes it when a dispatched step produced no execution result, which is exactly
#: as consistent with "no process was created" as with "it started and the result
#: was lost".
COMMAND_STARTED_OUTCOMES = frozenset(
    {
        "EXECUTION_SUCCESS",
        "EXECUTION_FAILURE",
        "EXECUTION_TIMEOUT",
        "EXECUTION_ERROR",
    }
)


class StepGraphError(ValueError):
    """Base class: a step graph was malformed, invalid, or untrustworthy."""


class StepGraphValidationError(StepGraphError):
    """The graph was refused. Nothing was executed and nothing will be."""

    def __init__(self, reasons: Sequence[str]) -> None:
        self.reasons = tuple(reasons)
        super().__init__(
            "step graph refused before execution: " + "; ".join(self.reasons)
        )


class StepGraphStateError(StepGraphError):
    """A persisted step-graph record exists but cannot be trusted for a resume."""


class StepStatus(str, Enum):
    """What is known about one step. See the module docstring for each value."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"
    UNKNOWN = "UNKNOWN"


class StepGraphStatus(str, Enum):
    """What is known about one whole attempt."""

    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUSED = "REFUSED"
    INDETERMINATE = "INDETERMINATE"
    INCOMPLETE = "INCOMPLETE"


class VerificationKind(str, Enum):
    """The observed fact a step's verification rests on.

    Both kinds read a fact the execution plane produced -- the real process exit
    code, or an artifact the adapter actually harvested -- so neither can be
    satisfied by a claim.
    """

    EXIT_CODE = "EXIT_CODE"
    ARTIFACT_PRODUCED = "ARTIFACT_PRODUCED"


class VerificationStatus(str, Enum):
    """The verdict of one declared verification check.

    ``FAILED`` and ``ERROR`` are different observations and are never collapsed.
    ``FAILED`` means the check was evaluated and did not hold. ``ERROR`` means
    the check could not be evaluated at all, so nothing is proven either way.
    """

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


@dataclass(frozen=True)
class StepVerification:
    """One declared, checkable post-condition for a step.

    A malformed verification raises at construction: it is a small closed
    structure and a bad one is a composition error, not a data-driven condition.
    """

    kind: VerificationKind
    expected_exit_code: int = 0
    artifact_target: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, VerificationKind):
            try:
                object.__setattr__(self, "kind", VerificationKind(self.kind))
            except ValueError as exc:
                raise StepGraphError(
                    f"unknown verification kind: {self.kind!r}"
                ) from exc
        if self.kind is VerificationKind.EXIT_CODE:
            if isinstance(self.expected_exit_code, bool) or not isinstance(
                self.expected_exit_code, int
            ):
                raise StepGraphError("expected_exit_code must be an int")
            if not 0 <= self.expected_exit_code <= 255:
                raise StepGraphError(
                    "expected_exit_code must be a conventional exit code (0-255)"
                )
        if self.kind is VerificationKind.ARTIFACT_PRODUCED:
            if not isinstance(self.artifact_target, str) or not self.artifact_target.strip():
                raise StepGraphError(
                    "ARTIFACT_PRODUCED verification requires an artifact_target"
                )
            try:
                object.__setattr__(
                    self,
                    "artifact_target",
                    normalize_workspace_relative_path(self.artifact_target),
                )
            except PathSecurityError as exc:
                raise StepGraphError(
                    f"unsafe artifact target: {self.artifact_target!r}"
                ) from exc

    def expectation_text(self) -> str:
        """A bounded, safe description of what must be true."""
        if self.kind is VerificationKind.EXIT_CODE:
            return f"exit_code={self.expected_exit_code}"
        return f"artifact_produced={self.artifact_target}"

    def artifact_targets(self) -> tuple[str, ...]:
        """The artifact targets this verification forces the step to declare.

        Asking the adapter to harvest the target is what makes the check
        observable: presence is read from a real harvested ``Artifact``, not from
        a filesystem the step process might have written to elsewhere.
        """
        if self.kind is VerificationKind.ARTIFACT_PRODUCED:
            return (self.artifact_target,)
        return ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "expected_exit_code": self.expected_exit_code,
            "artifact_target": self.artifact_target,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepVerification":
        try:
            kind = VerificationKind(str(data["kind"]))
        except (KeyError, ValueError) as exc:
            raise StepGraphStateError(
                f"saved verification has an unknown kind: {data.get('kind')!r}"
            ) from exc
        return cls(
            kind=kind,
            expected_exit_code=int(data.get("expected_exit_code", 0) or 0),
            artifact_target=str(data.get("artifact_target", "") or ""),
        )


@dataclass(frozen=True)
class StepAction:
    """The planned action of one step: an operator declaration plus parameters.

    The declaration is the only thing that can carry a command. ``parameters``
    is a closed, canonical, order-insensitive set of narrowing overrides, and it
    is part of the value the approval is bound to: changing any parameter changes
    the execution intent's fingerprint, so a previously granted approval no
    longer matches and the chain refuses the action (see
    :func:`app.workflows.step_runner.StepGraphRunner.run`).
    """

    declaration_id: str
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.declaration_id, str) or not self.declaration_id.strip():
            raise StepGraphError("declaration_id must be a non-empty string")
        object.__setattr__(self, "declaration_id", self.declaration_id.strip())
        raw = self.parameters
        if not isinstance(raw, tuple):
            raise StepGraphError("parameters must be a tuple of (name, value) pairs")
        pairs: list[tuple[str, str]] = []
        for entry in raw:
            if (
                not isinstance(entry, (tuple, list))
                or len(entry) != 2
                or not isinstance(entry[0], str)
                or not isinstance(entry[1], str)
            ):
                raise StepGraphError(
                    "each parameter must be a (name, value) pair of strings"
                )
            pairs.append((entry[0], entry[1]))
        names = [name for name, _ in pairs]
        if len(set(names)) != len(names):
            raise StepGraphError("parameter names must be unique within a step")
        # Sorted by name: the canonical form is what equality, hashing and the
        # planned-action fingerprint all use, so declaration order can never
        # change an identity.
        object.__setattr__(self, "parameters", tuple(sorted(pairs, key=lambda p: p[0])))

    def parameter(self, name: str) -> str | None:
        for key, value in self.parameters:
            if key == name:
                return value
        return None

    def invalid_reasons(self, step_id: str) -> tuple[str, ...]:
        """Parameter problems reported by ``StepGraph.validate``."""
        reasons: list[str] = []
        for name, value in self.parameters:
            if name not in PARAMETER_NAMES:
                reasons.append(f"{REASON_UNKNOWN_PARAMETER}:{step_id}->{name}")
                continue
            if name == "working_directory":
                if not value.strip():
                    reasons.append(f"{REASON_INVALID_PARAMETER_VALUE}:{step_id}->{name}")
                    continue
                try:
                    normalize_workspace_relative_path(value)
                except PathSecurityError:
                    reasons.append(
                        f"{REASON_INVALID_PARAMETER_VALUE}:{step_id}->{name}"
                    )
            elif name == "timeout_seconds":
                try:
                    seconds = float(value)
                except (TypeError, ValueError):
                    reasons.append(
                        f"{REASON_INVALID_PARAMETER_VALUE}:{step_id}->{name}"
                    )
                    continue
                if seconds <= 0:
                    reasons.append(
                        f"{REASON_INVALID_PARAMETER_VALUE}:{step_id}->{name}"
                    )
        return tuple(reasons)

    def to_dict(self) -> dict[str, Any]:
        return {
            "declaration_id": self.declaration_id,
            "parameters": [[name, value] for name, value in self.parameters],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepAction":
        raw = data.get("parameters") or ()
        pairs: list[tuple[str, str]] = []
        if not isinstance(raw, (list, tuple)):
            raise StepGraphStateError("saved parameters must be a sequence of pairs")
        for entry in raw:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise StepGraphStateError("saved parameters must be (name, value) pairs")
            pairs.append((str(entry[0]), str(entry[1])))
        return cls(declaration_id=str(data.get("declaration_id", "")), parameters=tuple(pairs))


@dataclass(frozen=True)
class StepDefinition:
    """One declared step: an id, an action, its dependencies, its check.

    Every declared step is mandatory in v0.1 -- there is no "optional" step that
    could be skipped while still allowing an overall ``COMPLETED``.
    """

    step_id: str
    action: StepAction
    depends_on: tuple[str, ...] = ()
    verification: StepVerification | None = None
    repeatable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, str):
            raise StepGraphError("step_id must be a string")
        if not isinstance(self.action, StepAction):
            raise StepGraphError("a step requires a StepAction")
        if not isinstance(self.depends_on, tuple):
            raise StepGraphError("depends_on must be a tuple of step ids")
        if self.verification is not None and not isinstance(
            self.verification, StepVerification
        ):
            raise StepGraphError("verification must be a StepVerification or None")
        if not isinstance(self.repeatable, bool):
            raise StepGraphError("repeatable must be a bool")

    def fingerprint(self) -> str:
        """The identity that a resume must find unchanged.

        Covers the action, its parameters, the dependency set, the verification
        and the repeatability. Deliberately *not* covered: anything a caller
        supplies at run time. A persisted step whose fingerprint no longer
        matches the definition is refused, never silently re-planned.
        """
        payload = {
            "step_id": self.step_id,
            "action": self.action.to_dict(),
            "depends_on": list(self.depends_on),
            "verification": self.verification.to_dict() if self.verification else None,
            "repeatable": bool(self.repeatable),
        }
        blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action.to_dict(),
            "depends_on": list(self.depends_on),
            "verification": (
                self.verification.to_dict() if self.verification else None
            ),
            "repeatable": bool(self.repeatable),
            "fingerprint": self.fingerprint(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepDefinition":
        """Reconstruct a definition from its persisted form.

        This is the inverse of :meth:`to_dict`. It exists so the **declared plan**
        can be read back after a restart instead of being re-supplied by a caller
        who still remembers it (see
        :func:`graph_from_declared_plan`). A malformed member is refused with
        :class:`StepGraphStateError`, never guessed at, so a damaged record can
        never be silently reinterpreted as a different plan.

        ``fingerprint`` is deliberately **not** trusted as input: it is recomputed
        from the reconstructed definition, and the caller compares the two. A
        saved fingerprint that disagrees with its own saved fields is therefore
        detectable rather than self-certifying.
        """
        if not isinstance(data, Mapping):
            raise StepGraphStateError("saved step definition must be a mapping")
        step_id = data.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            raise StepGraphStateError("saved step definition has no step_id")
        raw_action = data.get("action")
        if not isinstance(raw_action, Mapping):
            raise StepGraphStateError(
                f"saved step {step_id!r} has no action mapping"
            )
        action = StepAction.from_dict(raw_action)
        raw_depends = data.get("depends_on") or ()
        if not isinstance(raw_depends, (list, tuple)):
            raise StepGraphStateError(
                f"saved step {step_id!r} has a non-sequence depends_on"
            )
        depends_on: list[str] = []
        for dependency in raw_depends:
            if not isinstance(dependency, str):
                raise StepGraphStateError(
                    f"saved step {step_id!r} has a non-string dependency"
                )
            depends_on.append(dependency)
        raw_verification = data.get("verification")
        verification: StepVerification | None = None
        if raw_verification is not None:
            if not isinstance(raw_verification, Mapping):
                raise StepGraphStateError(
                    f"saved step {step_id!r} has a malformed verification"
                )
            verification = StepVerification.from_dict(raw_verification)
        repeatable = data.get("repeatable", False)
        if not isinstance(repeatable, bool):
            raise StepGraphStateError(
                f"saved step {step_id!r} has a non-boolean repeatable"
            )
        try:
            return cls(
                step_id=step_id,
                action=action,
                depends_on=tuple(depends_on),
                verification=verification,
                repeatable=repeatable,
            )
        except StepGraphError as exc:
            raise StepGraphStateError(
                f"saved step {step_id!r} is not a valid declaration: {exc}"
            ) from exc


@dataclass(frozen=True)
class StepGraph:
    """An ordered, acyclic set of declared steps.

    Declaration order is the deterministic tie-break for everything this module
    returns, so no answer here depends on dict or set iteration order.
    """

    graph_id: str
    steps: tuple[StepDefinition, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.graph_id, str):
            raise StepGraphError("graph_id must be a string")
        if not isinstance(self.steps, tuple):
            raise StepGraphError("steps must be a tuple of StepDefinition values")
        for step in self.steps:
            if not isinstance(step, StepDefinition):
                raise StepGraphError("steps must be StepDefinition values")

    # -- shape ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self.steps)

    @property
    def step_ids(self) -> tuple[str, ...]:
        """Declared ids in declaration order, duplicates included."""
        return tuple(step.step_id for step in self.steps)

    def _declaration_index(self) -> dict[str, int]:
        index: dict[str, int] = {}
        for position, step in enumerate(self.steps):
            index.setdefault(step.step_id, position)
        return index

    def step(self, step_id: str) -> StepDefinition:
        """The step with this id, or raise. Duplicates are invalid, not resolved."""
        matches = [step for step in self.steps if step.step_id == step_id]
        if not matches:
            raise StepGraphError(f"unknown step id: {step_id!r}")
        if len(matches) > 1:
            raise StepGraphError(f"step id is declared more than once: {step_id!r}")
        return matches[0]

    def verification_for(self, step_id: str) -> StepVerification | None:
        return self.step(step_id).verification

    # -- validation ----------------------------------------------------
    def validate(
        self,
        *,
        known_declarations: Iterable[str] | None = None,
        declarations: Mapping[str, ExecutionDeclaration] | None = None,
    ) -> tuple[str, ...]:
        """Return every reason this graph must be refused, deterministically.

        Empty tuple means valid. The order of the returned reasons is fixed by
        declaration order and by the fixed check order below, so two runs over
        the same graph report the same list.

        ``declarations`` is the operator's own registry. When it is supplied the
        graph is additionally refused for naming an undeclared action, and for a
        parameter that tries to *extend* a declared limit rather than narrow it.
        """
        reasons: list[str] = []
        if not isinstance(self.graph_id, str) or not _GRAPH_ID.match(self.graph_id or ""):
            reasons.append(REASON_GRAPH_ID_INVALID)
        if not self.steps:
            reasons.append(REASON_NO_STEPS)

        if declarations is not None:
            known: frozenset[str] | None = frozenset(declarations)
        elif known_declarations is not None:
            known = frozenset(known_declarations)
        else:
            known = None

        seen: set[str] = set()
        for step in self.steps:
            step_id = step.step_id
            if not isinstance(step_id, str) or not _STEP_ID.match(step_id):
                reasons.append(f"{REASON_MALFORMED_STEP_ID}:{step_id!r}")
            if step_id in seen:
                reasons.append(f"{REASON_DUPLICATE_STEP_ID}:{step_id}")
            seen.add(step_id)
            if known is not None and step.action.declaration_id not in known:
                reasons.append(
                    f"{REASON_UNKNOWN_DECLARATION}:{step_id}->"
                    f"{step.action.declaration_id}"
                )
            reasons.extend(step.action.invalid_reasons(step_id))
            if declarations is not None:
                declaration = declarations.get(step.action.declaration_id)
                declared_timeout = (
                    declaration.timeout_seconds if declaration is not None else None
                )
                requested = step.action.parameter("timeout_seconds")
                if requested is not None and declared_timeout is not None:
                    try:
                        if float(requested) > float(declared_timeout):
                            reasons.append(
                                f"{REASON_PARAMETER_WIDENS_LIMIT}:{step_id}"
                                f"->timeout_seconds"
                            )
                    except (TypeError, ValueError):
                        pass  # already reported as an invalid parameter value

        declared = set(self.step_ids)
        for step in self.steps:
            step_id = step.step_id
            seen_deps: set[str] = set()
            for dependency in step.depends_on:
                if not isinstance(dependency, str) or not dependency.strip():
                    reasons.append(f"{REASON_MALFORMED_DEPENDENCY}:{step_id}")
                    continue
                if dependency == step_id:
                    reasons.append(f"{REASON_SELF_DEPENDENCY}:{step_id}")
                    continue
                if dependency in seen_deps:
                    reasons.append(f"{REASON_DUPLICATE_DEPENDENCY}:{step_id}->{dependency}")
                    continue
                seen_deps.add(dependency)
                if dependency not in declared:
                    reasons.append(
                        f"{REASON_UNKNOWN_DEPENDENCY}:{step_id}->{dependency}"
                    )

        for component in self._cycle_components():
            reasons.append(f"{REASON_CYCLE}:" + "->".join(component))
        return tuple(reasons)

    def assert_valid(
        self,
        *,
        known_declarations: Iterable[str] | None = None,
        declarations: Mapping[str, ExecutionDeclaration] | None = None,
    ) -> None:
        """Refuse the graph loudly. Callers must do this before any execution."""
        reasons = self.validate(
            known_declarations=known_declarations, declarations=declarations
        )
        if reasons:
            raise StepGraphValidationError(reasons)

    # -- determinism ---------------------------------------------------
    def _edges(self) -> tuple[dict[str, list[str]], dict[str, int]]:
        """Return (dependency -> dependents, indegree), first declaration wins."""
        index = self._declaration_index()
        dependents: dict[str, list[str]] = {sid: [] for sid in index}
        indegree: dict[str, int] = {sid: 0 for sid in index}
        for step in self.steps:
            if step.step_id not in index:
                continue
            for dependency in dict.fromkeys(step.depends_on):
                if dependency in index and dependency != step.step_id:
                    dependents[dependency].append(step.step_id)
                    indegree[step.step_id] += 1
        return dependents, indegree

    def topological_order(self) -> tuple[str, ...]:
        """A deterministic topological order, or a shorter prefix if cyclic.

        Ready steps are always taken in declaration order, so the result depends
        only on the graph's declared content.
        """
        import heapq

        index = self._declaration_index()
        dependents, indegree = self._edges()
        heap = [index[sid] for sid, degree in indegree.items() if degree == 0]
        heapq.heapify(heap)
        order: list[str] = []
        by_position = {position: sid for sid, position in index.items()}
        while heap:
            position = heapq.heappop(heap)
            sid = by_position[position]
            order.append(sid)
            for child in dependents[sid]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(heap, index[child])
        return tuple(order)

    def _cycle_components(self) -> tuple[tuple[str, ...], ...]:
        """Genuine cycle members only, as sorted components in declaration order.

        Kahn's residual holds every node that is *downstream* of a cycle as well
        as the cycle itself, so the residual is reduced to strongly connected
        components: only a component with more than one member, or a
        self-dependency, is a cycle.
        """
        index = self._declaration_index()
        dependents, indegree = self._edges()
        residual = {sid for sid, degree in indegree.items() if degree > 0}
        components: list[tuple[str, ...]] = []
        # A self-dependency is a one-node cycle and must be reported as one, even
        # though the edge walk deliberately ignores it (a self-edge would
        # otherwise make its own node look merely "downstream").
        for step in self.steps:
            if step.step_id in step.depends_on:
                components.append((step.step_id,))
        if not residual:
            components.sort(key=lambda component: index[component[0]])
            return tuple(components)
        order = sorted(residual, key=lambda sid: index[sid])
        adjacency = {
            sid: [child for child in dependents[sid] if child in residual]
            for sid in order
        }
        # Iterative Tarjan, traversing neighbours in declaration order.
        counter = [0]
        stack: list[str] = []
        on_stack: set[str] = set()
        positions: dict[str, int] = {}
        low: dict[str, int] = {}

        for root in order:
            if root in positions:
                continue
            work: list[tuple[str, int]] = [(root, 0)]
            while work:
                node, child_index = work[-1]
                if child_index == 0:
                    positions[node] = low[node] = counter[0]
                    counter[0] += 1
                    stack.append(node)
                    on_stack.add(node)
                neighbours = adjacency[node]
                if child_index < len(neighbours):
                    work[-1] = (node, child_index + 1)
                    child = neighbours[child_index]
                    if child not in positions:
                        work.append((child, 0))
                    elif child in on_stack:
                        low[node] = min(low[node], positions[child])
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == positions[node]:
                    members: list[str] = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        members.append(member)
                        if member == node:
                            break
                    if len(members) > 1:
                        components.append(tuple(sorted(members, key=lambda m: index[m])))
        components.sort(key=lambda component: index[component[0]])
        return tuple(components)

    # -- scheduling ----------------------------------------------------
    def dependency_map(self) -> dict[str, tuple[str, ...]]:
        """Each step's declared dependencies, first declaration wins."""
        index = self._declaration_index()
        return {
            step.step_id: tuple(dict.fromkeys(step.depends_on))
            for step in self.steps
            if step.step_id in index
        }

    def ready_steps(self, statuses: Mapping[str, StepStatus]) -> tuple[str, ...]:
        """``PENDING`` steps whose every dependency is ``SUCCEEDED``.

        Returned in deterministic topological order, never in mapping order.
        """
        dependencies = self.dependency_map()
        ready: list[str] = []
        for step_id in self.topological_order():
            if statuses.get(step_id) is not StepStatus.PENDING:
                continue
            if all(
                statuses.get(dependency) is StepStatus.SUCCEEDED
                for dependency in dependencies[step_id]
            ):
                ready.append(step_id)
        return tuple(ready)

    def blocked_steps(self, statuses: Mapping[str, StepStatus]) -> tuple[str, ...]:
        """``PENDING`` steps that may not start because a dependency did not succeed."""
        ready = set(self.ready_steps(statuses))
        return tuple(
            step_id
            for step_id in self.topological_order()
            if statuses.get(step_id) is StepStatus.PENDING and step_id not in ready
        )


def ready_step_ids(
    graph: StepGraph, statuses: Mapping[str, StepStatus]
) -> tuple[str, ...]:
    """Module-level convenience wrapper for :meth:`StepGraph.ready_steps`."""
    return graph.ready_steps(statuses)


# ---------------------------------------------------------------------------
# per-step evidence
# ---------------------------------------------------------------------------


@dataclass
class StepRecord:
    """Everything known about one step, kept as five separate observations.

    The distinctions this record exists to preserve:

    * **planned** vs **performed** -- what the graph asked for, and what the
      execution plane actually dispatched (``""`` when nothing was performed);
    * **execution error** vs **verification error** -- two different failures
      with two different fields, never collapsed into one message;
    * **execution outcome** vs **step status** -- a process that exited 0 while a
      declared verification failed is ``execution_outcome=EXECUTION_SUCCESS``
      with ``status=FAILED``, and both facts stay readable;
    * **performed** vs **command_started** -- whether the execution plane was named,
      and whether a process really came into existence. Block #10 measured the cost
      of not having the second: an attempt refused for want of approval reported
      ``performed_executable: "python"`` while nothing had run;
    * **evidence** -- the bounded reason codes the status rests on.

    Nothing secret is stored here: no stdout, no stderr, no environment value,
    and only the *executable* of the performed command (never its argv), which
    mirrors the existing execution-record discipline in
    :meth:`app.orchestrator.run.RunExecutor._record_execution_outcome`.
    """

    step_id: str
    status: StepStatus = StepStatus.PENDING
    action_fingerprint: str = ""
    declaration_id: str = ""
    parameters: tuple[tuple[str, str], ...] = ()
    verification_kind: str = ""
    # what was actually performed
    performed_executable: str = ""
    performed_working_directory: str = ""
    step_run_id: str = ""
    request_id: str = ""
    # the real result
    execution_exit_code: int | None = None
    execution_outcome: str = ""
    execution_error: str = ""
    # the real verification
    verification_status: VerificationStatus = VerificationStatus.NOT_RUN
    verification_expected: str = ""
    verification_observed: str = ""
    verification_error: str = ""
    evidence: tuple[str, ...] = ()
    attempts: int = 0
    updated_at: str = ""

    def add_evidence(self, *codes: str) -> None:
        merged = list(self.evidence)
        for code in codes:
            if code and code not in merged:
                merged.append(code)
        self.evidence = tuple(merged)

    @property
    def performed(self) -> bool:
        """Whether the execution plane was actually asked to run something."""
        return bool(self.step_run_id)

    @property
    def command_started(self) -> bool:
        """Whether a command for this step actually LEFT the authorization chain.

        Block #10 states the distinction this project was missing. The marker is the
        recorded ``execution_outcome``, and it is the only honest one:

        * ``EXECUTION_SUCCESS`` / ``EXECUTION_FAILURE`` / ``EXECUTION_TIMEOUT`` are
          written by the ADAPTER, from a process it really started -- so each of them
          proves a command started, including the ones that failed;
        * ``EXECUTION_ERROR`` is written by the adapter after the spawn itself failed,
          which is still an attempt to start a process;
        * ``APPROVAL_WAITING``, ``APPROVAL_REJECTED``, ``PERMISSION_DENIED``,
          ``POLICY_DENIED`` and the ``REFUSED_*`` values are written by the
          AUTHORIZATION CHAIN (or the runner) before any process exists, so none of
          them is a start. ``""`` means no outcome was recorded at all.

        ``step_run_id`` is deliberately NOT the marker, and this is a MEASURED
        correction: the step run id is assigned before the grant is attempted and is
        NOT cleared when approval defers the dispatch, so a step recorded
        ``WAITING_APPROVAL`` still carries one. Using it made this property report
        ``true`` for an attempt that created no process -- the exact confusion the
        field exists to remove. ``performed`` keeps ``step_run_id`` as its marker,
        because "the execution plane was named" and "a process exists" are two
        different facts.
        """

        return self.execution_outcome in COMMAND_STARTED_OUTCOMES

    @property
    def verified(self) -> bool:
        """Whether a declared check passed. ``NOT_RUN`` is not a pass."""
        return self.verification_status is VerificationStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "status": self.status.value,
            "action_fingerprint": self.action_fingerprint,
            "declaration_id": self.declaration_id,
            "parameters": [[name, value] for name, value in self.parameters],
            "verification_kind": self.verification_kind,
            "performed_executable": self.performed_executable,
            "performed_working_directory": self.performed_working_directory,
            # THIS CALL STARTED A PROCESS FOR THIS STEP. See
            # ``StepRecord.command_started``: it is read from the recorded execution
            # outcome, never from ``performed_executable`` (the DECLARED executable
            # this step intended to run, which is reported even for a step that was
            # refused before dispatch) and never from ``step_run_id`` (assigned
            # before the grant is attempted).
            "command_started": self.command_started,
            "step_run_id": self.step_run_id,
            "request_id": self.request_id,
            "execution_exit_code": self.execution_exit_code,
            "execution_outcome": self.execution_outcome,
            "execution_error": self.execution_error,
            "verification_status": self.verification_status.value,
            "verification_expected": self.verification_expected,
            "verification_observed": self.verification_observed,
            "verification_error": self.verification_error,
            "evidence": list(self.evidence),
            "attempts": int(self.attempts),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepRecord":
        if not isinstance(data, Mapping):
            raise StepGraphStateError("saved step record must be a mapping")
        step_id = data.get("step_id")
        if not isinstance(step_id, str) or not step_id:
            raise StepGraphStateError("saved step record has no step_id")
        try:
            status = StepStatus(str(data.get("status", StepStatus.PENDING.value)))
        except ValueError as exc:
            raise StepGraphStateError(
                f"saved step {step_id!r} has an unknown status: {data.get('status')!r}"
            ) from exc
        try:
            verification_status = VerificationStatus(
                str(data.get("verification_status", VerificationStatus.NOT_RUN.value))
            )
        except ValueError as exc:
            raise StepGraphStateError(
                f"saved step {step_id!r} has an unknown verification status"
            ) from exc
        raw_parameters = data.get("parameters") or ()
        parameters: list[tuple[str, str]] = []
        if not isinstance(raw_parameters, (list, tuple)):
            raise StepGraphStateError("saved parameters must be a sequence")
        for entry in raw_parameters:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                raise StepGraphStateError("saved parameters must be (name, value) pairs")
            parameters.append((str(entry[0]), str(entry[1])))
        raw_evidence = data.get("evidence") or ()
        if not isinstance(raw_evidence, (list, tuple)):
            raise StepGraphStateError("saved evidence must be a sequence")
        exit_code = data.get("execution_exit_code")
        return cls(
            step_id=step_id,
            status=status,
            action_fingerprint=str(data.get("action_fingerprint", "") or ""),
            declaration_id=str(data.get("declaration_id", "") or ""),
            parameters=tuple(parameters),
            verification_kind=str(data.get("verification_kind", "") or ""),
            performed_executable=str(data.get("performed_executable", "") or ""),
            performed_working_directory=str(
                data.get("performed_working_directory", "") or ""
            ),
            step_run_id=str(data.get("step_run_id", "") or ""),
            request_id=str(data.get("request_id", "") or ""),
            execution_exit_code=None if exit_code is None else int(exit_code),
            execution_outcome=str(data.get("execution_outcome", "") or ""),
            execution_error=str(data.get("execution_error", "") or ""),
            verification_status=verification_status,
            verification_expected=str(data.get("verification_expected", "") or ""),
            verification_observed=str(data.get("verification_observed", "") or ""),
            verification_error=str(data.get("verification_error", "") or ""),
            evidence=tuple(str(code) for code in raw_evidence),
            attempts=int(data.get("attempts", 0) or 0),
            updated_at=str(data.get("updated_at", "") or ""),
        )


@dataclass
class StepGraphSnapshot:
    """The durable document for one graph run.

    Stored inside the existing ``RunStore`` snapshot under
    ``project_state['step_graph']``; the store's own ``state``/``status`` fields
    stay empty so this record never masquerades as a live ``Run`` lifecycle.

    Since Block #7 the document also carries ``declared_plan``: the
    serialization of the graph that was **declared** when the attempt started.
    Without it, continuing an interrupted attempt required a caller who still
    remembered the plan in-process, so a restart could not re-validate against
    what was actually declared. The plan is data, not authority: a recorded plan
    permits nothing by itself, and a resume that reads it back still has to pass
    the same fingerprint re-validation, the same ``RunScope`` freeze, the same
    ``ApprovalPolicy`` check and the same coordinator as a first attempt.
    """

    graph_id: str
    graph_run_id: str
    status: StepGraphStatus = StepGraphStatus.INCOMPLETE
    steps: tuple[StepRecord, ...] = ()
    stopped_reason: str = ""
    cancelled: bool = False
    idempotency_degraded: bool = False
    attempts: int = 0
    updated_at: str = ""
    declared_plan: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": STEP_GRAPH_SCHEMA,
            "graph_id": self.graph_id,
            "graph_run_id": self.graph_run_id,
            "status": self.status.value,
            "stopped_reason": self.stopped_reason,
            "cancelled": bool(self.cancelled),
            "idempotency_degraded": bool(self.idempotency_degraded),
            "attempts": int(self.attempts),
            "updated_at": self.updated_at,
            "steps": [record.to_dict() for record in self.steps],
            "declared_plan": [dict(entry) for entry in self.declared_plan],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "StepGraphSnapshot":
        if not isinstance(data, Mapping):
            raise StepGraphStateError("saved step-graph record must be a mapping")
        schema = str(data.get("schema", ""))
        if schema != STEP_GRAPH_SCHEMA:
            raise StepGraphStateError(
                f"unsupported step-graph record schema: {schema!r}"
            )
        graph_id = data.get("graph_id")
        graph_run_id = data.get("graph_run_id")
        if not isinstance(graph_id, str) or not graph_id:
            raise StepGraphStateError("saved step-graph record has no graph_id")
        if not isinstance(graph_run_id, str) or not graph_run_id:
            raise StepGraphStateError("saved step-graph record has no graph_run_id")
        try:
            status = StepGraphStatus(str(data.get("status", StepGraphStatus.INCOMPLETE.value)))
        except ValueError as exc:
            raise StepGraphStateError(
                f"saved step-graph record has an unknown status: {data.get('status')!r}"
            ) from exc
        raw_steps = data.get("steps")
        if not isinstance(raw_steps, (list, tuple)):
            raise StepGraphStateError("saved step-graph record has no step list")
        raw_plan = data.get("declared_plan")
        declared_plan: list[Mapping[str, Any]] = []
        if raw_plan is not None:
            if not isinstance(raw_plan, (list, tuple)):
                raise StepGraphStateError(
                    "saved step-graph record has a non-sequence declared_plan"
                )
            for entry in raw_plan:
                if not isinstance(entry, Mapping):
                    raise StepGraphStateError(
                        "saved step-graph record has a malformed declared_plan entry"
                    )
                declared_plan.append(dict(entry))
        return cls(
            graph_id=graph_id,
            graph_run_id=graph_run_id,
            status=status,
            steps=tuple(StepRecord.from_dict(entry) for entry in raw_steps),
            stopped_reason=str(data.get("stopped_reason", "") or ""),
            cancelled=bool(data.get("cancelled", False)),
            idempotency_degraded=bool(data.get("idempotency_degraded", False)),
            attempts=int(data.get("attempts", 0) or 0),
            updated_at=str(data.get("updated_at", "") or ""),
            declared_plan=tuple(declared_plan),
        )


def declared_plan_of(graph: StepGraph) -> tuple[Mapping[str, Any], ...]:
    """The persistable form of a graph's declared steps, in declaration order.

    Declaration order is preserved because it is the deterministic tie-break the
    whole module uses; a set or a dict would lose it.
    """
    return tuple(step.to_dict() for step in graph.steps)


def read_step_graph_record(
    project_state: Mapping[str, Any] | None,
) -> tuple[StepGraphSnapshot | None, str]:
    """Read a step-graph record out of a ``RunStore`` snapshot's project state.

    Returns ``(record, reason)``. ``record`` is ``None`` when there is nothing to
    read *or* when what is there cannot be trusted, and ``reason`` then names
    which of the two happened, so a caller can distinguish "no attempt was ever
    recorded" from "an attempt was recorded in a form this build refuses" without
    catching an exception. A recovery scan must not treat a corrupt record as
    "nothing was running", and it must not crash on one either.
    """
    if not isinstance(project_state, Mapping):
        return None, "no_project_state"
    payload = project_state.get(STEP_GRAPH_PROJECT_STATE_KEY)
    if payload is None:
        return None, "no_step_graph_record"
    if not isinstance(payload, Mapping):
        # The key is present and holds something that is not a record at all. Reporting
        # this as "no record" conflated an invalid structure with an absent one, and a
        # caller that trusts the reason would then start a new attempt over it.
        return None, (
            f"untrustworthy_step_graph_record:not_a_mapping:{type(payload).__name__}"
        )
    try:
        return StepGraphSnapshot.from_dict(payload), ""
    except StepGraphStateError as exc:
        return None, f"untrustworthy_step_graph_record:{exc}"
    except (ValueError, TypeError) as exc:
        # A malformed numeric or shape field raises a plain ValueError from int()/str()
        # deeper down. This reader's documented contract is that it REPORTS such a
        # record rather than raising it -- the recovery scan iterates records with no
        # ``try`` of its own, so an escaping exception aborts the whole scan and a
        # damaged record ends up looking like a broken project. It is therefore
        # reported exactly like an explicitly refused one.
        return None, f"untrustworthy_step_graph_record:{type(exc).__name__}: {exc}"


def graph_from_declared_plan(
    graph_id: str, plan: Sequence[Mapping[str, Any]]
) -> StepGraph:
    """Rebuild the graph a record says was **declared**, without a caller.

    This is what makes a restart able to re-validate against the declared plan
    instead of against whatever the caller happens to remember. Every saved
    step's recorded ``fingerprint`` is checked against the fingerprint recomputed
    from its own saved fields, so a tampered or damaged record is refused here
    rather than being accepted as a plan nobody declared.

    Raises :class:`StepGraphStateError` when the saved plan is empty or any entry
    is malformed or self-inconsistent.
    """
    if not isinstance(plan, Sequence) or isinstance(plan, (str, bytes)):
        raise StepGraphStateError("a declared plan must be a sequence of steps")
    if not plan:
        raise StepGraphStateError(
            "the saved step-graph record carries no declared plan, so the plan it "
            "was validated against cannot be recovered; re-validate against the "
            "graph supplied by the caller instead"
        )
    definitions: list[StepDefinition] = []
    for entry in plan:
        try:
            definition = StepDefinition.from_dict(entry)
        except StepGraphStateError:
            raise
        except (ValueError, TypeError) as exc:
            # The docstring promises StepGraphStateError for every malformed entry; a
            # plain ValueError from a non-numeric field used to escape instead.
            raise StepGraphStateError(
                f"a saved step in the declared plan is malformed: {type(exc).__name__}: {exc}"
            ) from exc
        recorded = entry.get("fingerprint")
        if recorded is not None and str(recorded) != definition.fingerprint():
            raise StepGraphStateError(
                f"saved step {definition.step_id!r} declares a fingerprint that does "
                "not match its own saved fields; the record is not self-consistent "
                "and is refused rather than re-planned"
            )
        definitions.append(definition)
    try:
        return StepGraph(graph_id=graph_id, steps=tuple(definitions))
    except StepGraphError as exc:
        raise StepGraphStateError(
            f"the saved declared plan is not a valid graph: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# overall verdict
# ---------------------------------------------------------------------------


def derive_graph_status(
    records: Sequence[StepRecord],
    *,
    cancelled: bool = False,
    budget_exhausted: bool = False,
) -> StepGraphStatus:
    """Derive the attempt verdict from the step records, fail-closed.

    Precedence is deliberately pessimistic: an unknown or cancelled attempt is
    never summarised as a completed or merely failed one.
    """
    statuses = [record.status for record in records]
    if not statuses:
        return StepGraphStatus.INCOMPLETE
    if any(status is StepStatus.UNKNOWN for status in statuses):
        return StepGraphStatus.INDETERMINATE
    if any(status is StepStatus.RUNNING for status in statuses):
        # In-memory only: a RUNNING step is never a settled answer.
        return StepGraphStatus.INDETERMINATE
    if cancelled or any(status is StepStatus.CANCELLED for status in statuses):
        return StepGraphStatus.CANCELLED
    if any(status is StepStatus.FAILED for status in statuses):
        return StepGraphStatus.FAILED
    if any(
        status in (StepStatus.WAITING_APPROVAL, StepStatus.BLOCKED)
        for status in statuses
    ):
        return StepGraphStatus.REFUSED
    if all(status is StepStatus.SUCCEEDED for status in statuses):
        return StepGraphStatus.COMPLETED
    if budget_exhausted:
        return StepGraphStatus.INCOMPLETE
    return StepGraphStatus.INCOMPLETE


# ---------------------------------------------------------------------------
# request construction (pure)
# ---------------------------------------------------------------------------


def build_step_request(
    definition: StepDefinition,
    declaration: ExecutionDeclaration,
    *,
    profile: ProjectExecutionProfile,
    run_id: str,
    request_id: str,
) -> ExecutionRequest:
    """Translate a validated step into an ``ExecutionRequest``. Pure.

    The command comes from the operator's declaration and nowhere else. The only
    things a step may vary are the parameters in the closed vocabulary:

    * ``working_directory`` -- a containment-checked relative path, re-checked
      against the frozen workspace by ``RunScope`` and again by the adapter
      immediately before the child is created;
    * ``timeout_seconds`` -- narrowing only. A step may shorten the declaration's
      timeout (or the profile's, when the declaration has none) and never extend
      it, so a plan cannot buy itself more time than the operator declared.

    ``approval_required`` keeps its default: approval stays a server-side
    ``ApprovalPolicy`` decision and a graph cannot weaken or pre-empt it.
    """
    base = to_execution_request(declaration, profile=profile, run_id=run_id)
    working_directory = base.working_directory
    timeout = base.timeout_seconds

    override_directory = definition.action.parameter("working_directory")
    if override_directory is not None:
        working_directory = normalize_workspace_relative_path(override_directory)

    override_timeout = definition.action.parameter("timeout_seconds")
    if override_timeout is not None:
        requested = float(override_timeout)
        declared_limit = (
            base.timeout_seconds if base.timeout_seconds is not None else profile.timeout_seconds
        )
        if requested > float(declared_limit):
            raise StepGraphError(
                f"step {definition.step_id!r} asks for {requested}s but the declared "
                f"limit is {declared_limit}s; a plan may narrow a limit, never extend it"
            )
        timeout = requested

    artifact_targets = tuple(base.artifact_targets)
    verification = definition.verification
    if verification is not None:
        for target in verification.artifact_targets():
            if target not in artifact_targets:
                artifact_targets = artifact_targets + (target,)

    metadata = dict(base.metadata)
    metadata["step_graph_step_id"] = definition.step_id

    return ExecutionRequest(
        command=tuple(base.command),
        request_id=request_id,
        working_directory=working_directory,
        environment_variables=dict(base.environment_variables),
        timeout_seconds=timeout,
        profile=base.profile,
        artifact_targets=artifact_targets,
        metadata=metadata,
        approval_required=False,
    )


def planned_payload(definition: StepDefinition) -> dict[str, Any]:
    """Bounded projection of a step's plan, safe to persist and to display."""
    return {
        "step_id": definition.step_id,
        "declaration_id": definition.action.declaration_id,
        "parameters": [[name, value] for name, value in definition.action.parameters],
        "depends_on": list(definition.depends_on),
        "verification": (
            definition.verification.to_dict() if definition.verification else None
        ),
        "repeatable": bool(definition.repeatable),
    }
