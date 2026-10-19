"""The operating surface: one server-side projection the UI may read, and the
small set of operations it may ask for.

Why this module exists
----------------------
``app/api/service.py`` is the *execution* contract: it turns operator
declarations into authorized runs. It deliberately says nothing about how a
human operator should *see* the project -- understanding, requirements, plan,
approvals, memory, change impact. Block #8 needs that view to exist, and it must
exist in exactly one place, on the server.

The rules this module obeys, and why
------------------------------------
**The server is authoritative; the client never upgrades a status.** Everything
here is either (a) a read of state the server already owns, or (b) a call into an
existing authority chain. No method in this file can set a step to ``SUCCEEDED``,
mark a memory entry ``CONFIRMED``, resolve an approval, or widen a ``RunScope``.
A caller that supplies a status field has it ignored, because none of the
response builders read one.

**No method here grants authority.** Execution always goes through the *existing*
``StepGraphRunner`` (which re-checks ``RunScope``, ``ApprovalPolicy``,
``ExecutionCoordinator`` and the workspace on every dispatch) or through an
existing ``ForgeApiService`` entry point. This module composes those calls; it
does not reimplement, wrap or bypass them.

**Project Memory grants nothing.** ``app/runtime/recovery.py`` and
``app/understanding/memory.py`` import no execution authority; this module keeps
that property by never deriving an execution input from a memory record. Loading
memory cannot dispatch anything.

**No client-supplied workspace path is trusted.** The workspace comes from the
service's own composition (``ForgeApiService.workspace``). The only path-like
input any caller may supply is a *relative* path, and it is validated through
``Workspace.normalize_relative_path`` before it is compared with anything -- so
``..``, an absolute path, a drive letter or a reserved device name is refused
rather than resolved.

**Error strings are sanitized.** A response never carries exception text
verbatim, because a nested exception message can quote a configured value. See
``_safe_reason``.

The honest limit of the boundary
--------------------------------
This module is a *server-side contract* boundary, not process isolation. It
refuses a request it cannot justify; it cannot stop a hostile process that
already runs as this user. That distinction is recorded in
``docs/SOURCE_OF_TRUTH.md`` and must not be described as a sandbox.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import re
from typing import Any, Mapping, Sequence

from app.context.models import ContextSourceType, ContextTrustLevel
from app.runtime.run_store import RunStore
from app.understanding.memory import (
    FreshnessIndex,
    MemoryItemKind,
    MemoryRecord,
    MemoryScope,
    MemoryStatus,
    ProjectMemory,
    ProjectMemoryStore,
    freshness_of,
)
from app.understanding.models import UnderstandingSnapshot
from app.understanding.snapshotter import UnderstandingSnapshotter
from app.tools.workspace import Workspace, WorkspacePathError

__all__ = [
    "OperatingSurfaceError",
    "Refused",
    "GraphRunInFlight",
    "SurfaceCompositionError",
    "MAX_DEPENDENT_NODES",
    "OWNER_RECORDABLE_KINDS",
    "OperatingSurface",
]


class OperatingSurfaceError(RuntimeError):
    """A request the surface could not carry out. Carries a machine reason."""

    #: Machine-readable code, stable across builds.
    code = "operating_surface_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class Refused(OperatingSurfaceError):
    """The request was refused before anything changed. Nothing was executed."""

    code = "refused"


class GraphRunInFlight(OperatingSurfaceError):
    """The same graph run id is already being driven in this process.

    Its own type because it is a *safety* refusal, not an error: the other caller
    is still producing steps, so this one must not read or repeat them.
    """

    code = "graph_run_in_progress"


class SurfaceCompositionError(OperatingSurfaceError):
    """The composition handed this surface is not usable, so nothing may run.

    A **controlled configuration error**, deliberately distinct from
    :class:`Refused`. A refusal says "this well-formed request is not allowed"; this
    says "the process was assembled wrongly". Both stop before anything executes,
    which is the property that matters, but they are not the same fact and are not
    reported as the same thing.

    It is raised rather than returned so a misconfiguration cannot be mistaken for
    the ordinary ``None`` answer, which means "this composition does not offer that
    feature". A caller that conflated the two would learn to treat a broken approval
    policy as a missing one.
    """

    code = "surface_composition_invalid"


def _validate_step_graph_ingredients(ingredients: Any) -> Any:
    """Return a validated approval policy, or refuse before anything is built.

    Three distinct cases, all of them fail-closed and none of them a traceback from
    a raw ``KeyError`` or an ``AttributeError`` deep inside the runner:

    1. the ``approval_policy`` **key is absent** -- the composition does not match
       the contract this surface requires, so it is a configuration error;
    2. the value is **``None``** -- the composition states no approval position.
       That is a legitimate state, so it is reported as the ordinary ``None``
       ("no step-graph runner") rather than as an error, matching the answer
       ``RunExecutor`` (``approval_policy_required``) and ``StepGraphRunner`` (which
       refuses to be constructed without a policy) already give;
    3. the value has the **wrong type** and does not satisfy the
       ``ApprovalPolicy`` contract -- a policy that cannot be enforced is not a
       policy, so it is a configuration error.

    What this function will never do is substitute a policy. A default would require
    approval only for its mandatory tool set, so substituting it would let a
    deployment that stated no approval position execute declared host commands.

    Called **before** any ``ExecutionCoordinator`` is constructed, so a
    misconfiguration cannot leave an authority component half-built.
    """

    from app.tools.approval import ApprovalPolicy

    if not isinstance(ingredients, Mapping):
        raise SurfaceCompositionError(
            "the step-graph composition must be a mapping, got "
            f"{type(ingredients).__name__}",
            code="step_graph_composition_invalid",
        )
    if "approval_policy" not in ingredients:
        # An absent key is not the same fact as a None value: the composition does
        # not implement the contract at all.
        raise SurfaceCompositionError(
            "the step-graph composition does not supply an 'approval_policy' key, "
            "so this surface cannot establish what approval a step would require",
            code="approval_policy_missing",
        )
    policy = ingredients["approval_policy"]
    if policy is None:
        # Fail closed, but as the ordinary "no runner" answer rather than an error.
        return None
    if not isinstance(policy, ApprovalPolicy):
        raise SurfaceCompositionError(
            "the step-graph composition supplied an approval policy of type "
            f"{type(policy).__name__}, which is not an ApprovalPolicy; a policy "
            "that cannot be enforced is not a policy",
            code="approval_policy_invalid_type",
        )
    if not callable(getattr(policy, "evaluate", None)):
        # The class is right but the object does not satisfy the contract it is
        # used through. Checked separately so a stub that only *looks* like a
        # policy is refused rather than failing later inside the coordinator.
        raise SurfaceCompositionError(
            "the supplied approval policy has no callable 'evaluate', so it does "
            "not satisfy the ApprovalPolicy contract",
            code="approval_policy_contract_unsatisfied",
        )
    return policy


def _coordinator_harvests_artifacts(coordinator: Any) -> bool:
    """Whether the coordinator's adapter actually harvests declared artifacts.

    The step runner decides between two very different verdicts from this one
    boolean: ``FAILED`` ("the verifier looked and the artifact is not there") and
    ``ERROR`` ("this composition cannot evaluate the check at all"). Asserting it
    instead of reading it is how an honest "unevaluable" became a wrong claim
    about a file that really existed.

    Fail-closed in the direction that matters: an adapter that does not publish the
    capability is reported as **not** harvesting, which yields the unevaluable
    verdict rather than a FAILED one. ``False`` never executes anything and never
    passes a check; it only declines to claim absence.
    """

    adapter = getattr(coordinator, "_adapter", None)
    return bool(getattr(adapter, "harvests_artifacts", False))


#: Hard ceiling on how many dependent topology nodes one change-impact report may
#: name. Truncation is always reported through ``truncated``; it is never silent.
MAX_DEPENDENT_NODES = 50

#: The kinds an operator-facing request may record directly.
#:
#: Deliberately does **not** include ``CONFIRMED_FACT`` or
#: ``VERIFIED_CONCLUSION``. A confirmed fact has to cite evidence fact ids that
#: the understanding layer actually produced (``MemoryRecord.__post_init__``
#: enforces that), so "the owner typed a sentence and it became confirmed" is
#: impossible on this surface. What the owner says is recorded as what it is: a
#: hypothesis, or explicitly unknown data.
OWNER_RECORDABLE_KINDS = frozenset(
    {MemoryItemKind.HYPOTHESIS, MemoryItemKind.UNKNOWN_DATA}
)

#: Trust levels the operator-facing recorder may use. ``CONFIRMED`` and
#: ``VERIFIED`` are absent on purpose: this surface cannot assert either.
_OWNER_TRUST = frozenset(
    {ContextTrustLevel.INFERRED, ContextTrustLevel.UNVERIFIED}
)

#: A run id this surface will accept for a client-chosen graph run. It becomes
#: part of every derived step run id (``step.<graph_run_id>.<step_id>``), so it is
#: the one identifier that can reach the storage layout, and it is therefore
#: allow-listed rather than sanitized.
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _bounded_int(value: object, *, default: int, low: int, high: int) -> int:
    """A caller-supplied bound, clamped into a safe range.

    Clamping rather than refusing is deliberate here and nowhere else on this
    surface: the value is a *budget*, so a smaller one is strictly more
    conservative. A caller cannot raise a budget above the ceiling, and a malformed
    value falls back to the default rather than to "unlimited".
    """

    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _bounded_float(value: object, *, default: float, low: float, high: float) -> float:
    """The same clamp for a wall-clock budget. See :func:`_bounded_int`."""

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN is never a budget
        return default
    return max(low, min(high, number))


def _safe_reason(exc: BaseException) -> str:
    """A short, sanitized description of a failure, safe to return to a caller.

    Never the exception's own text: a nested message can quote a configured value
    (a path, an environment entry, a command line). Only the exception *class* and
    a machine code the surface chose itself are reported. The full detail stays in
    the server's own logs.
    """

    return type(exc).__name__


def _reason_codes(exc: BaseException) -> tuple[str, ...]:
    """Structured reason codes an exception carries, if it is one of ours."""

    reasons = getattr(exc, "reasons", None)
    if isinstance(reasons, (list, tuple)):
        return tuple(str(reason) for reason in reasons)
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return (code,)
    return ()


@dataclass(frozen=True)
class _Baseline:
    """The understanding snapshot a change-impact report is measured against."""

    snapshot: UnderstandingSnapshot
    seeded: bool = False


#: Block #8 kind -> the engine's own change kind. The engine is the single source
#: of the reasoning; this mapping exists so the Block #8 keys keep their exact
#: Block #8 *values* (``CHANGED``, ``ADDED``, ``DELETED``) while every derived
#: field is read from one place instead of being recomputed twice.
_BLOCK8_KIND = {
    "MODIFIED": "CHANGED",
    "ADDED": "ADDED",
    "DELETED": "DELETED",
}

#: Fields the engine computes and the surface carries through verbatim. Listed
#: explicitly so a new engine field is a deliberate addition here and not a silent
#: drift between the report and the verification plan.
_ENGINE_FIELDS = (
    "classification",
    "basis",
    "basis_is_empty",
    "support_rank",
    "support_rank_meaning",
    "ambiguous_move",
    "move_candidates",
    "probable_rename_to",
    "affected_requirements",
    "affected_tests",
    "stale_facts",
)


def _surface_changes(
    base: UnderstandingSnapshot,
    current: UnderstandingSnapshot,
    analysis: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """The Block #8 ``changes`` list, with every derived field taken from the engine.

    One representation, computed once. An earlier revision built this list in
    ``change_impact`` and passed the *engine's* payload to the verification planner,
    so the two could disagree: the planner read ``reasoning_note`` from a finding
    the report had never seen. Both now read the same objects.
    """

    base_map = {entry.relative_path: entry.fingerprint for entry in base.files}
    current_map = {entry.relative_path: entry.fingerprint for entry in current.files}
    changes: list[dict[str, Any]] = []
    for path in sorted(set(base_map) | set(current_map)):
        was = base_map.get(path)
        now = current_map.get(path)
        if was == now:
            continue
        finding = analysis["findings_by_path"].get(path, {})
        entry: dict[str, Any] = {
            "path": path,
            "kind": _BLOCK8_KIND.get(
                str(finding.get("kind", "")),
                "ADDED" if was is None else "DELETED" if now is None else "CHANGED",
            ),
            "baseline_fingerprint": was,
            "current_fingerprint": now,
            "stale": was is not None and now != was,
        }
        for field_name in _ENGINE_FIELDS:
            entry[field_name] = finding.get(field_name, [] if field_name.endswith("s") else "")
        entry["affected_facts"] = list(finding.get("affected_facts", ()))
        entry["affected_fact_ids"] = list(finding.get("affected_fact_ids", ()))
        entry["dependent_nodes"] = list(finding.get("dependent_nodes", ()))
        entry["dependent_nodes_truncated"] = bool(
            finding.get("dependent_nodes_truncated", False)
        )
        entry["reasoning_note"] = finding.get("note", "")
        changes.append(entry)
    return changes


class OperatingSurface:
    """Server-side projection and operator-facing operations over one service.

    Constructed by ``app.api.server.create_api_app`` from the ``ForgeApiService``
    it is given, so the HTTP surface and the execution surface cannot disagree
    about which workspace, declarations, or run store is authoritative.
    """

    #: The memory document id used in the shared ``RunStore``. One store, one
    #: memory -- this is deliberately *not* a second Project Memory store.
    MEMORY_ID = "project-memory"

    #: The run id the baseline understanding snapshot is persisted under.
    BASELINE_ID = "project-baseline"

    def __init__(
        self,
        service: Any,
        *,
        snapshotter: UnderstandingSnapshotter | None = None,
        memory_store: ProjectMemoryStore | None = None,
    ) -> None:
        self._service = service
        self._workspace: Workspace = service.workspace
        self._run_store: RunStore = service.run_store
        self._snapshotter = snapshotter or UnderstandingSnapshotter()
        # The memory store is a codec over the SAME RunStore the service uses.
        # Constructing a second store (or a second root) here would be exactly the
        # second Project Memory store the block forbids.
        self._memory_store = memory_store or ProjectMemoryStore(
            self._run_store, memory_id=self.MEMORY_ID
        )
        self._baseline: _Baseline | None = None
        self._coordinator: Any = None

    # ------------------------------------------------------------------
    # composition
    #
    # This module -- not ``app/api/service.py`` -- is where the step-graph runner
    # and the recovery planner are assembled. The API service is asserted by
    # ``test_api_layer_has_no_direct_adapter_or_process_access`` to contain no
    # reference to the adapter or to process spawning, and that boundary is kept:
    # the service hands out ingredients, and the composition happens here.
    #
    # Nothing below grants authority. The runner re-checks ``RunScope``,
    # ``ApprovalPolicy``, ``ExecutionCoordinator`` and the workspace on every
    # dispatch, and the planner is read-only by construction.
    # ------------------------------------------------------------------
    def _step_graph_coordinator(self) -> Any:
        """The one execution coordinator a step-graph run may dispatch through.

        Built from the service's own workspace root, so a step run is confined to
        the workspace the service was composed with. Cached, because the
        coordinator is stateless apart from its adapter and rebuilding it per call
        would give two runs two different objects for one authority.
        """

        if self._coordinator is None:
            from app.execution.adapter import LocalExecutionAdapter
            from app.execution.authorizer import ExecutionCoordinator

            self._coordinator = ExecutionCoordinator(
                LocalExecutionAdapter(
                    workspace_root=self._workspace.root,
                    # The operator's own workspace is the target: a step is *meant*
                    # to change it. Isolation is the adapter's separate, explicit
                    # setting and is deliberately not enabled here, so a console run
                    # has a visible effect the operator can then review in Change
                    # Impact. This is the documented contrast with
                    # ``run_declared_verification``, which does isolate.
                    isolate_workspace=False,
                    # Harvesting is a SEPARATE declaration, not a consequence of
                    # isolation. It used to be reachable only inside the isolation
                    # branch, so this composition -- which does not isolate -- silently
                    # harvested nothing while the runner still asserted
                    # ``artifacts_are_harvested=True``. A step that really wrote its
                    # declared artifact was then reported FAILED with
                    # ``artifact_not_produced``: a wrong claim about the operator's own
                    # file, and a FAILED step blocks its dependents. Enabling it here
                    # makes the assertion below true instead of aspirational.
                    harvests_artifacts=True,
                )
            )
        return self._coordinator

    def build_step_graph_runner(self) -> Any:
        """A ``StepGraphRunner`` over this service, or ``None`` when not composed.

        ``None`` means "no host execution through this path", never "a runner with
        weaker checks".
        """

        ingredients = self._service.step_graph_composition()
        if ingredients is None:
            return None
        # VALIDATE FIRST, BUILD NOTHING. The policy is settled before the
        # ExecutionCoordinator below is constructed, so a misconfiguration can never
        # leave an authority component half-built, and no command can run. The three
        # fail-closed cases are handled in `_validate_step_graph_ingredients`.
        approval_policy = _validate_step_graph_ingredients(ingredients)
        if approval_policy is None:
            # "No approval position stated" is the ordinary fail-closed reading of
            # None used throughout this module: this composition offers no step-graph
            # runner, so there is no host execution through it. Substituting a
            # default here would mean a deployment that never stated an approval
            # position still ran declared host commands -- the permissive reading of
            # a security-relevant default, on exactly the guarantee this project
            # treats as non-negotiable (recommendation != authorization != approval
            # != execution).
            return None
        from app.workflows.step_runner import StepGraphRunner

        return StepGraphRunner(
            runtime=ingredients["runtime"],
            workspace=ingredients["workspace"],
            execution_profile=ingredients["execution_profile"],
            declarations=ingredients["declarations"],
            run_store=ingredients["run_store"],
            idempotency_guard=ingredients["idempotency_guard"],
            # The composition's OWN policy, never a substitute. The runner enforces
            # it on every dispatch and the resolver stays exactly as the operator
            # configured it -- ``None`` when unresolved, which is what keeps a write
            # step WAITING_APPROVAL.
            approval_policy=approval_policy,
            execution_coordinator=self._step_graph_coordinator(),
            approval_resolver=ingredients["approval_resolver"],
            allowed_tool_ids=ingredients["allowed_tool_ids"],
            # DERIVED, NEVER ASSERTED. The runner's ``artifacts_are_harvested`` decides
            # whether a missing artifact is evidence of absence (FAILED) or merely
            # unevaluable (ERROR). Deriving it from the adapter that was just composed
            # is what keeps the two from disagreeing: a hard-coded ``True`` over an
            # adapter that does not harvest converts an honest "cannot be evaluated"
            # into a false FAILED. It is enabled on this path because a step graph's
            # declared verification may reference an artifact
            # (``ARTIFACT_PRODUCED``). Harvesting only *records the digest* of a file
            # the step already declared in ``artifact_targets``; it grants nothing,
            # starts nothing, and measures inside the same workspace the step writes
            # to. It is not enabled for the declared-execution path, which does not
            # use this runner.
            artifacts_are_harvested=_coordinator_harvests_artifacts(
                self._step_graph_coordinator()
            ),
        )

    def build_recovery_planner(self) -> Any:
        """A ``RecoveryPlanner`` over this service's run store, or ``None``.

        Read-only by construction: the planner discovers unfinished attempts and
        decides, but it never executes and never writes a claim. Claims are issued
        only by an explicit ``RecoveryClaimLog.acquire`` call, which the HTTP
        surface does not expose, so a route can plan a recovery and still be unable
        to resume anything.
        """

        ingredients = self._service.recovery_composition()
        if ingredients is None:
            return None
        from app.runtime.recovery import RecoveryPlanner

        return RecoveryPlanner(
            run_store=ingredients["run_store"],
            idempotency_guard=ingredients["idempotency_guard"],
            declarations=ingredients["declarations"],
            workspace=ingredients["workspace"],
            runner_factory=self.build_step_graph_runner,
        )

    # ------------------------------------------------------------------
    # identity and read-only projections
    # ------------------------------------------------------------------
    @property
    def workspace(self) -> Workspace:
        """The authoritative workspace, for in-process inspection only."""

        return self._workspace

    @property
    def memory_store(self) -> ProjectMemoryStore:
        return self._memory_store

    @property
    def snapshotter(self) -> UnderstandingSnapshotter:
        return self._snapshotter


    def _require_project(self) -> str:
        """The authorized project, or a controlled refusal.

        Memory scopes, snapshots and event reconciliation are project-scoped: writing them under an
        empty identifier would put unrelated work into one shared namespace. This method exists so those
        call sites fail closed before any side effect instead of persisting an ambiguous scope.
        """

        project_id = self._project_id()
        if not project_id:
                                    raise PermissionError(
                                                                    "this surface has no authorized project: project-scoped state cannot be written"
                                    )
        return project_id
    def _project_id(self) -> str:
        """The authorized project of this surface, or an empty string when there is none.

        An empty project list means the surface has no project at all. It used to fabricate the
        literal "default" here, which callers could then bind as execution authority; that made a
        missing identity indistinguishable from a real one. The empty string is the repository's
        missing-value convention, and every project-sensitive path must fail closed on it.
        """

        projects = self._service.list_projects()
        if not projects:
            return ""
        return str(getattr(projects[0], "project_id", "") or "")
        projects = self._service.list_projects()
        if not projects:
            return "default"
        return str(getattr(projects[0], "project_id", "default"))

    def project_overview(self) -> dict[str, Any]:
        """The project list, the current stage, and what the UI may do next.

        The stage is derived from observed state, never from a claim: an
        unfinished run classifies as ``INDETERMINATE`` through
        ``RunStore.classify_record``, and a project with no recorded run is
        ``INITIAL``. Nothing here can report a stage the server cannot evidence.
        """

        projects = [entry.to_dict() for entry in self._service.list_projects()]
        # NEWEST FIRST BY RECORDED TIME, never by run id. ``list_run_ids`` is
        # lexicographic: taking ``[-1]`` from it made the "latest run" the id that
        # sorted last, so after a verify run the latest was reported as
        # ``step.chain-verify.s1`` purely because ``'s' > 'r'``. The surface's own
        # docstring promised the latest run, so the ordering had to become real
        # rather than the claim being dropped.
        run_ids = self._run_store.list_run_ids_by_recency()
        classification = None
        latest_run_id = ""
        if run_ids:
            latest_run_id = run_ids[0]
            try:
                classification = self._run_store.classify_record(latest_run_id).to_dict()
            except Exception as exc:  # noqa: BLE001 - a read path must not fail
                # The fallback uses the REAL key and vocabulary. A fabricated
                # ``{"classification": "UNKNOWN"}`` would not be an
                # ``ExecutionClassification`` value at all, and ``_stage_of`` --
                # which reads ``outcome`` -- would silently read it as "nothing".
                classification = {
                    "outcome": "UNKNOWN",
                    "reason": _safe_reason(exc),
                    "detail": "",
                    "evidence": [],
                    "is_terminal": False,
                    "is_success": False,
                }

        memory = self._memory_store.load()
        stage = self._stage_of(classification)
        return {
            "project_id": self._project_id(),
            "projects": projects,
            "stage": stage,
            "latest_run_id": latest_run_id,
            "latest_classification": classification,
            "counts": {
                "declarations": len(getattr(self._service, "declarations", {})),
                "acceptance_specs": len(getattr(self._service, "acceptance_specs", {})),
                "recorded_runs": len(run_ids),
                "memory_current": len(memory.current()),
                "memory_stale": len(memory.stale()),
            },
            "capabilities": {
                "goal_entry": True,
                "understanding": True,
                "memory": True,
                "change_impact": True,
                "step_graph": self._service.can_run_step_graph(),
                "accept_cycle": self._service.can_run_accept_cycle(),
                "declared_verification": self._service.can_run_declared_verification(),
                "recovery": self._service.can_plan_recovery(),
            },
        }

    @staticmethod
    def _stage_of(classification: Mapping[str, Any] | None) -> str:
        """Map a run classification onto one honest project stage.

        THE FIELD AND THE VOCABULARY BOTH MATTER. This read
        ``classification.get("classification")`` and compared it against
        ``SUCCESS`` / ``FAILED`` / ``REFUSED``. Neither exists:
        ``RunRecordClassification.to_dict()`` publishes the verdict under
        ``outcome``, and its vocabulary is the ``ExecutionClassification`` enum --
        ``TERMINAL_SUCCESS`` / ``TERMINAL_FAILURE`` / ``TERMINAL_TIMEOUT`` /
        ``TERMINAL_CANCELLED`` / ``TERMINAL_REFUSED`` / ``WAITING`` /
        ``INDETERMINATE`` / ``UNKNOWN``. So a real completed run fell through every
        branch and the stage was **permanently ``INITIAL``**: a project the server
        could evidence as finished was reported as untouched. Changing only the key
        would not have been enough -- the labels had to be translated too.

        ``INDETERMINATE`` maps to ``IN_PROGRESS`` rather than to a terminal stage,
        because that is exactly what it means: the record does not prove an outcome,
        so the work is not finished. Nothing here promotes an unknown to a success.
        """

        if not classification:
            return "INITIAL"
        outcome = str(classification.get("outcome", "")).strip().upper()
        if outcome == "TERMINAL_SUCCESS":
            return "VERIFIED"
        if outcome in {
            "TERMINAL_FAILURE",
            "TERMINAL_TIMEOUT",
            "TERMINAL_CANCELLED",
            "TERMINAL_REFUSED",
        }:
            return "FAILED"
        if outcome in {"WAITING", "INDETERMINATE", "UNKNOWN"}:
            return "IN_PROGRESS"
        return "INITIAL"

    def requirements(self) -> dict[str, Any]:
        """Operator requirements and acceptance criteria, with their expectations.

        Read-only over the operator's own declarations. A criterion that has no
        declared expectation is reported as ``not_evaluable`` rather than being
        dropped, because "there is a criterion nothing can check" is a fact the
        operator needs to see.
        """

        declarations = getattr(self._service, "declarations", {})
        specs = getattr(self._service, "acceptance_specs", {})
        entries: list[dict[str, Any]] = []
        for declaration_id, declaration in sorted(declarations.items()):
            spec = specs.get(declaration_id)
            criteria = []
            if spec is not None:
                for criterion in spec.criteria:
                    expectation = spec.expectations.get(criterion.criterion_id)
                    criteria.append(
                        {
                            "criterion_id": criterion.criterion_id,
                            "description": criterion.description,
                            "requirement_id": getattr(criterion, "requirement_id", ""),
                            "expectation": (
                                {
                                    "path": expectation.relative_path,
                                    "must_exist": bool(expectation.exists),
                                }
                                if expectation is not None
                                else None
                            ),
                            "evaluable": expectation is not None,
                        }
                    )
            entries.append(
                {
                    "requirement_id": declaration_id,
                    "intent": declaration.intent,
                    "executable": declaration.executable,
                    "working_directory": declaration.working_directory,
                    "criteria": criteria,
                    "has_declared_criteria": spec is not None,
                }
            )
        return {
            "project_id": self._project_id(),
            "requirements": entries,
            "note": (
                "criteria and expectations come from the operator composition; "
                "this route cannot add, remove, or relax one"
            ),
        }

    def run_history(self, *, limit: int = 50) -> dict[str, Any]:
        """Recorded runs, newest first, each with its honest classification.

        "Newest first" is the run's own recorded ``updated_at`` (see
        ``RunStore.list_run_ids_by_recency``), not the run id's sort order. Read-only.
        A record that claims to be in progress has no heartbeat, owner or lease, so
        it is reported as ``INDETERMINATE`` -- never as a live run.
        """

        capped = max(1, min(int(limit), 200))
        # Already newest-first from the store; slicing keeps the newest N rather
        # than reversing a lexicographic list and calling the result "newest".
        all_run_ids = list(self._run_store.list_run_ids_by_recency())
        newest_first = all_run_ids[:capped]
        entries: list[dict[str, Any]] = []
        for run_id in newest_first:
            record = self.run_record(run_id)
            if record is None:
                continue
            persisted = None
            try:
                persisted = self._run_store.load(run_id)
            except Exception:  # noqa: BLE001 - the snapshot is an addition, not a gate
                persisted = None
            snapshot = persisted.snapshot if persisted is not None else None
            entries.append(
                {
                    "run_id": run_id,
                    "state": record.get("state", ""),
                    "status": record.get("status", ""),
                    "classification": record.get("classification"),
                    # The SAME projection ``GET /api/runs/{run_id}`` returns, read from
                    # the run's own snapshot. Without it the list carried no
                    # ``project_state`` at all, so a client that counts code-writing
                    # outcomes from this endpoint always measured zero even when every
                    # run had recorded one. ``None`` (not ``{}``) when the run has no
                    # state, which is what the single-run endpoint reports too.
                    "project_state": record.get("project_state"),
                    "event_count": (
                        persisted.event_count if persisted is not None else 0
                    ),
                    "updated_at": (
                        getattr(snapshot, "updated_at", "") if snapshot else ""
                    ),
                }
            )
        return {
            "project_id": self._project_id(),
            "runs": entries,
            "total_recorded": len(all_run_ids),
            "truncated": len(all_run_ids) > capped,
        }

    # ------------------------------------------------------------------
    # goal entry
    # ------------------------------------------------------------------
    def capture_goal(self, goal: object) -> dict[str, Any]:
        """Record the operator's goal as a **hypothesis**, and plan against it.

        Recording the goal as a hypothesis is the honest classification: a goal is
        a statement of intent, not an observation about the project, so it may not
        be a ``CONFIRMED_FACT``. ``MemoryRecord`` enforces that independently --
        a ``CONFIRMED_FACT`` must cite evidence fact ids, and a goal has none.

        Writing the goal changes no execution input. The declaration a run uses
        still comes from the operator composition, so a goal cannot select a
        command.
        """

        if not isinstance(goal, str) or not goal.strip():
            raise Refused("goal must be a non-empty string", code="empty_goal")
        statement = goal.strip()
        if len(statement) > 2000:
            raise Refused("goal is too long", code="goal_too_long")

        memory = self._memory_store.load()
        memory_id = f"goal-{len(memory.records)}"
        while memory.by_id(memory_id) is not None:
            memory_id = f"{memory_id}x"
        entry = MemoryRecord(
            memory_id=memory_id,
            kind=MemoryItemKind.HYPOTHESIS,
            statement=statement,
            source=ContextSourceType.EXPLICIT_INPUT,
            source_id=memory_id,
            scope=MemoryScope(project_id=self._require_project()),
            trust_level=ContextTrustLevel.INFERRED,
            details={"role": "goal", "entered_by": "operator"},
        )
        updated = memory.record(entry)
        self._memory_store.save(updated)
        return {
            "recorded": True,
            "memory_id": memory_id,
            "kind": MemoryItemKind.HYPOTHESIS.value,
            "trust_level": ContextTrustLevel.INFERRED.value,
            "confirmed_fact": False,
            "note": (
                "the goal is recorded as a hypothesis; it is not a confirmed fact "
                "and it grants no execution authority"
            ),
            "requirements": self.requirements()["requirements"],
        }

    # ------------------------------------------------------------------
    # project understanding
    # ------------------------------------------------------------------
    def understanding(self) -> dict[str, Any]:
        """A bounded scan of the authoritative workspace, summarized.

        The workspace is the service's own. No request value selects it. Files the
        canonical scanner recognises as secret are recorded without a fingerprint
        and never read (``BoundedProjectScanner``), and only structural
        descriptors -- paths, fact types, symbols -- are returned.
        """

        snapshot = self._snapshotter.create_snapshot(
            self._workspace, project_id=self._require_project()
        )
        return {
            "project_id": snapshot.project_id,
            "snapshot_id": snapshot.snapshot_id,
            "workspace_fingerprint": snapshot.workspace_fingerprint,
            "created_at": snapshot.created_at,
            "file_count": len(snapshot.files),
            "fact_count": len(snapshot.facts),
            "manifest_count": len(snapshot.manifests),
            "topology": {
                "nodes": len(snapshot.topology.nodes),
                "edges": len(snapshot.topology.edges),
            },
            "facts": [fact.to_dict() for fact in snapshot.facts[:200]],
            "manifests": [entry.to_dict() for entry in snapshot.manifests],
            "warnings": [entry.to_dict() for entry in snapshot.warnings],
            "facts_truncated": len(snapshot.facts) > 200,
            "note": (
                "descriptors only: file contents, digests of secret-recognised "
                "files, and values are never returned"
            ),
        }

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------
    def plan(
        self, *, declarations: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """The operator's declared plan: requirement -> steps and dependencies.

        With no graph supplied, this reports the declared *requirement* set and
        an empty step list, because inventing steps would be inventing work the
        operator never authorized. A graph is validated against the operator's own
        declaration registry before it appears here, so a plan this route reports
        as valid is one the runner would also accept.
        """

        from app.execution.declaration import validate_declarations_against_profile

        declared = dict(declarations or {})
        dependencies: dict[str, list[str]] = {}
        validation_error = ""
        for step_id, raw in declared.items():
            depends = raw.get("depends_on") if isinstance(raw, Mapping) else None
            dependencies[str(step_id)] = [str(item) for item in (depends or [])]

        registry = getattr(self._service, "declarations", {})
        profile = self._service.execution_profile
        try:
            validate_declarations_against_profile(registry, profile)
        except Exception as exc:  # noqa: BLE001 - reported, never raised to a client
            validation_error = _safe_reason(exc)

        return {
            "project_id": self._project_id(),
            "requirements": self.requirements()["requirements"],
            "steps": [
                {"step_id": step_id, "depends_on": dependencies[step_id]}
                for step_id in sorted(declared)
            ],
            "declared_action_count": len(registry),
            "profile_id": getattr(profile, "profile_id", ""),
            "allowed_commands": sorted(getattr(profile, "allowed_commands", ())),
            "network_access": bool(getattr(profile, "network_access", False)),
            "registry_consistent": not validation_error,
            "registry_error": validation_error,
        }

    # ------------------------------------------------------------------
    # step graph: run and resume
    # ------------------------------------------------------------------
    @staticmethod
    def validate_run_id(graph_run_id: object) -> str:
        """Refuse anything that is not a safe run identifier.

        The id is interpolated into every step run id and therefore reaches the
        storage layout, so it is allow-listed rather than sanitized. A caller
        cannot select a path.

        Raised as ``PermissionError``: an identifier that does not match the
        allow-list is not a style problem, it is a caller trying to make the server
        compose a filesystem path out of their input. The route layer maps it to
        403 on a path parameter and to 400 on a JSON body -- one rule, two status
        codes, no second check.
        """

        if not isinstance(graph_run_id, str) or not _SAFE_RUN_ID.match(graph_run_id):
            raise PermissionError(
                "graph_run_id must match [A-Za-z0-9][A-Za-z0-9._-]{0,127}"
            )
        return graph_run_id

    @staticmethod
    def validate_run_id_for_path(graph_run_id: object) -> str:
        """Alias kept so a path-parameter call site reads as what it is."""

        return OperatingSurface.validate_run_id(graph_run_id)

    def start_graph(
        self,
        graph_run_id: object,
        steps: Sequence[Mapping[str, Any]],
        graph_id: str = "operator-plan",
    ) -> dict[str, Any]:
        """Start one attempt of a step graph, through the existing runner.

        Every guarantee is the runner's own: the graph is validated against the
        operator's declaration registry before any scope is frozen, each step is
        re-authorized by ``ExecutionCoordinator``/``ApprovalPolicy``, and a repeat
        with a graph run id that already has a record executes **nothing**.

        A concurrent second start of a live graph run is refused with a dedicated
        code, so a caller cannot mistake "somebody else is driving this" for "it
        finished".
        """

        run_id = self.validate_run_id(graph_run_id)
        runner = self.build_step_graph_runner()
        if runner is None:
            raise Refused(
                "no step-graph runner is composed: host execution is disabled",
                code="step_graph_unavailable",
            )
        graph = _graph_from_request(graph_id, steps)
        if graph is None:
            raise Refused("a step graph must declare at least one step", code="empty_graph")

        existing = runner.recorded(run_id)
        if existing is not None:
            # A durable record exists for this run id, but the run id is a CALLER-SUPPLIED
            # LABEL, not a proof that the recorded operation is the one being requested.
            # Rebuild the recorded graph from its saved plan and compare it by value:
            # ``StepGraph`` is a dataclass with value equality over ``StepDefinition``,
            # whose ``action`` carries ``declaration_id`` and the canonical
            # ``parameters`` -- so a request that changed a declaration, a parameter, a
            # dependency or a verification is NOT the recorded operation and must never
            # receive its result as its own success.
            #
            # ``graph_from_declared_plan`` is the reconstruction the ``resume`` path
            # already relies on, so this introduces no second fingerprint mechanism. If
            # the plan cannot be read back, identity is unprovable and the answer is a
            # closed refusal rather than a silent assumption of sameness.
            from app.workflows.step_graph import graph_from_declared_plan

            recorded_graph = None
            try:
                recorded_graph = graph_from_declared_plan(
                    existing.graph_id, existing.declared_plan
                )
            except Exception:  # noqa: BLE001 - an unreadable plan refuses closed
                recorded_graph = None
            if recorded_graph is None or recorded_graph != graph:
                # An unmapped code is classified as a state conflict by the route's own
                # closed rule and published verbatim, so the caller gets 409 plus a
                # machine-readable reason without inventing a new error taxonomy.
                raise Refused(
                    "this run id already records a different operation; a replayed "
                    "result is never returned for a changed request",
                    code="graph_run_id_reused_with_a_different_plan",
                )

            # The runner would do exactly this, but stating it here lets the HTTP
            # layer answer 200-with-recorded-attempt rather than an error, and it
            # keeps the "no second execution" property visible at the boundary.
            #
            # The saved step records are returned AS RECORDED. They describe the
            # attempt that ran, and an attempt record must not be rewritten per
            # response: clearing the recorded ``command_started`` made a single step
            # read "SUCCEEDED, exit 0, step_run_id set, command_started: false" -- a
            # record contradicting itself. What THIS call did is stated by the fields
            # beside it: ``replayed``, ``executed``, ``command_started`` and
            # ``commands_started`` describe this response, and ``recorded_result``
            # marks the payload as a past attempt rather than a claim about now.
            payload = existing.to_dict()
            return {
                "executed": False,
                "replayed": True,
                # A replayed attempt starts nothing, whatever the RECORDED attempt
                # did. Reported explicitly so a client never has to infer "did this
                # call create a process" from ``executed`` -- which means "the runner
                # drove an attempt" and is false here for that reason too.
                "command_started": False,
                "commands_started": 0,
                "recorded_result": True,
                "result": payload,
                "note": (
                    "a recorded attempt already exists; nothing was executed again. "
                    "The fields above describe this call; ``result`` is the recorded "
                    "attempt, returned unchanged."
                ),
            }

        try:
            result = runner.run(graph, run_id)
        except Exception as exc:  # noqa: BLE001 - classified, never leaked verbatim
            raise self._classify_graph_failure(exc) from exc
        return {
            "executed": result.executed,
            # THE FIELD A CALLER NEEDS, AND WHY IT IS NOT ``executed``. ``executed``
            # is the runner's own "this call drove the graph", which is TRUE for an
            # attempt that was processed and refused before any command: measured,
            # with ``ApprovalPolicy(approval_required_tools=("python",))`` and no
            # resolver, the route answered ``status: "REFUSED"`` with
            # ``executed: true`` although nothing ran. ``command_started`` is derived
            # from the step records' own ``step_run_id`` -- assigned only after the
            # approval and policy checks, cleared by both pre-dispatch refusal paths
            # -- so it is true exactly when a process may exist.
            "command_started": bool(result.executed and result.command_started),
            "commands_started": (
                int(result.commands_started) if result.executed else 0
            ),
            "replayed": False,
            "result": result.to_dict(),
        }

    def resume_graph(
        self,
        graph_run_id: object,
        steps: Sequence[Mapping[str, Any]] | None = None,
        graph_id: str = "operator-plan",
    ) -> dict[str, Any]:
        """Continue a recorded attempt through the existing runner.

        The declared plan is read back **from the record**, so a restart does not
        need a caller who still remembers it. When the caller also supplies steps,
        they are an independent cross-check and a mismatch is refused.

        A step recorded as ``RUNNING`` is read back as ``UNKNOWN`` and is never
        repeated -- an unknown result is not permission to retry.
        """

        run_id = self.validate_run_id(graph_run_id)
        runner = self.build_step_graph_runner()
        if runner is None:
            raise Refused(
                "no step-graph runner is composed: host execution is disabled",
                code="step_graph_unavailable",
            )
        graph = _graph_from_request(graph_id, steps) if steps else None
        try:
            result = runner.resume(graph, run_id)
        except Exception as exc:  # noqa: BLE001
            raise self._classify_graph_failure(exc) from exc
        return {
            "executed": result.executed,
            # A resume that processed steps it had not settled may dispatch; one that
            # found every step settled dispatches nothing. The step records decide,
            # exactly as they do for a fresh start.
            "command_started": bool(result.executed and result.command_started),
            "commands_started": (
                int(result.commands_started) if result.executed else 0
            ),
            "replayed": False,
            "result": result.to_dict(),
        }

    def graph_status(self, graph_run_id: object) -> dict[str, Any]:
        """The recorded state of one graph run. Read-only; never resumes anything."""

        run_id = self.validate_run_id(graph_run_id)
        runner = self.build_step_graph_runner()
        if runner is None:
            raise Refused(
                "no step-graph runner is composed: host execution is disabled",
                code="step_graph_unavailable",
            )
        record = runner.recorded(run_id)
        if record is None:
            return {
                "graph_run_id": run_id,
                "found": False,
                "status": None,
                "steps": [],
                "note": "no attempt is recorded under this id",
            }
        payload = record.to_dict()
        # A durable RUNNING with no outcome means the process that wrote it may have
        # died after dispatching the step. The runner makes that same re-reading when
        # it resumes; this route makes it when it *reports*, so a reader is never
        # shown "RUNNING" for an attempt nothing is currently driving. The stored
        # record is not rewritten: reporting is not a write.
        for step in payload.get("steps", []):
            if step.get("status") == "RUNNING":
                step["status"] = "UNKNOWN"
                step["read_back_as"] = "UNKNOWN"
        for step in payload.get("steps", []):
            if step.get("status") == "UNKNOWN":
                payload["status"] = "INDETERMINATE"
                break
        return {"graph_run_id": run_id, "found": True, **payload}

    @staticmethod
    def _classify_graph_failure(exc: BaseException) -> OperatingSurfaceError:
        """Turn a runner failure into a typed refusal, keeping its reason codes."""

        reasons = _reason_codes(exc)
        name = type(exc).__name__
        if name == "StepGraphValidationError":
            return Refused(
                f"the step graph was refused before anything ran: {', '.join(reasons)}",
                code="graph_invalid",
            )
        if name == "StepGraphStateError":
            return Refused(
                f"the recorded attempt cannot be resumed: {_safe_reason(exc)}",
                code="graph_state_refused",
            )
        return OperatingSurfaceError(
            f"the step graph could not be driven: {_safe_reason(exc)}",
            code="graph_failed",
        )

    def approvals(self, graph_run_id: object) -> dict[str, Any]:
        """Approval requests with their parameters and their consequences.

        Parameters and consequences are read from the record the server already
        holds (``StepRecord.parameters``, ``declaration_id``,
        ``verification_expected``) plus the operator's own declaration. Nothing is
        inferred from a client field, and the ``intent_fingerprint`` is the value
        the coordinator itself will compare -- so a parameter change *after* an
        approval is visible here as a different fingerprint.

        This route reports. It cannot grant: approval remains the sole decision of
        the server-side ``ApprovalPolicy`` and its resolver.
        """

        run_id = self.validate_run_id(graph_run_id)
        runner = self.build_step_graph_runner()
        if runner is None:
            raise Refused(
                "no step-graph runner is composed: host execution is disabled",
                code="step_graph_unavailable",
            )
        record = runner.recorded(run_id)
        declarations = getattr(self._service, "declarations", {})
        requests: list[dict[str, Any]] = []
        if record is not None:
            graph = None
            try:
                from app.workflows.step_graph import graph_from_declared_plan

                graph = graph_from_declared_plan(record.graph_id, record.declared_plan)
            except Exception:  # noqa: BLE001 - fingerprint is an addition, not a gate
                graph = None
            for step in record.steps:
                if step.status.value != "WAITING_APPROVAL":
                    continue
                declaration = declarations.get(step.declaration_id)
                fingerprint = ""
                if graph is not None:
                    try:
                        fingerprint = runner.intent_fingerprint(graph, step.step_id)
                    except Exception:  # noqa: BLE001
                        fingerprint = ""
                requests.append(
                    {
                        "step_id": step.step_id,
                        "declaration_id": step.declaration_id,
                        "status": step.status.value,
                        "parameters": [
                            {"name": name, "value": value}
                            for name, value in step.parameters
                        ],
                        "consequences": {
                            "executable": step.performed_executable
                            or getattr(declaration, "executable", ""),
                            "working_directory": step.performed_working_directory
                            or getattr(declaration, "working_directory", ""),
                            "artifact_targets": list(
                                getattr(declaration, "artifact_targets", ())
                            ),
                            "verification_expected": step.verification_expected,
                            "intent": getattr(declaration, "intent", ""),
                        },
                        "intent_fingerprint": fingerprint,
                        "attempts": step.attempts,
                        "granted_by_server": False,
                    }
                )
        return {
            "graph_run_id": run_id,
            "found": record is not None,
            "requests": requests,
            "awaiting": len(requests),
            "note": (
                "parameters and consequences come from the server's own record; "
                "approval is granted only by the server-side ApprovalPolicy and "
                "its resolver, never by this route or by the UI"
            ),
        }

    # ------------------------------------------------------------------
    # recovery
    # ------------------------------------------------------------------
    def recovery_scan(self) -> dict[str, Any]:
        """Unfinished attempts and, for each, what may happen next.

        Read-only. The planner never executes and never writes a claim, so this
        route cannot resume anything as a side effect.
        """

        planner = self.build_recovery_planner()
        if planner is None:
            raise Refused("recovery is not composed", code="recovery_unavailable")
        findings = planner.unfinished_runs()
        plans: list[dict[str, Any]] = []
        for finding in findings[:20]:
            try:
                plan = planner.plan(finding.graph_run_id)
            except Exception as exc:  # noqa: BLE001
                plans.append(
                    {
                        "graph_run_id": finding.graph_run_id,
                        "plan_error": _safe_reason(exc),
                    }
                )
                continue
            plans.append(plan.to_dict())
        return {
            "project_id": self._project_id(),
            "unfinished": [finding.to_dict() for finding in findings],
            "plans": plans,
            "truncated": len(findings) > 20,
            "note": (
                "a plan is data, not a grant: continuing an attempt requires a "
                "claim token this route does not issue"
            ),
        }

    def recovery_plan(self, graph_run_id: object) -> dict[str, Any]:
        """The per-step decision for one unfinished attempt."""

        run_id = self.validate_run_id(graph_run_id)
        planner = self.build_recovery_planner()
        if planner is None:
            raise Refused("recovery is not composed", code="recovery_unavailable")
        try:
            plan = planner.plan(run_id)
        except Exception as exc:  # noqa: BLE001
            raise OperatingSurfaceError(
                f"the attempt could not be planned: {_safe_reason(exc)}",
                code="recovery_plan_failed",
            ) from exc
        return plan.to_dict()

    # ------------------------------------------------------------------
    # the accept cycle and declared verification
    #
    # Both delegate straight to the existing ``ForgeApiService`` entry points.
    # This module does not reimplement, wrap or re-order their checks: it exists
    # only so the HTTP layer has one object to talk to.
    # ------------------------------------------------------------------
    def run_accepted_task(
        self, declaration_id: str, *, idempotency_key: str | None = None
    ) -> Any:
        """Delegate to ``ForgeApiService.run_accepted_task``. Grants nothing.

        The approval policy is settled first, in one place for both entry points
        below. A composition with no stated policy performs no host execution, and
        that is a server-side fact rather than a request conflict, so it is reported
        as a controlled configuration error and never as "you asked for something
        disallowed". Refusing here means no path can reach the accept cycle without
        a policy, including a path that does not consult ``RunExecutor``.
        """

        self._require_approval_policy("the accept cycle")
        return self._service.run_accepted_task(
            declaration_id, idempotency_key=idempotency_key
        )

    def run_declared_verification(
        self,
        declaration_id: str,
        *,
        purpose_run_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """Delegate to ``ForgeApiService.run_declared_verification``.

        As above: no approval policy, no declared verification, reported honestly.
        """

        self._require_approval_policy("declared verification")
        return self._service.run_declared_verification(
            declaration_id,
            purpose_run_id=purpose_run_id,
            idempotency_key=idempotency_key,
        )

    def _require_approval_policy(self, what: str) -> None:
        """Refuse a host-executing entry point that has no approval policy.

        ``None`` is the ordinary fail-closed state for "this deployment never
        stated an approval position" -- exactly what ``build_step_graph_runner``
        answers with ``None``. It is *not* the caller's fault and it is not
        retryable, so it is a ``SurfaceCompositionError`` (an HTTP 500 with
        ``configuration_error``) rather than a ``Refused`` (409). A 409 would tell
        the caller their request conflicted with server state and train them to
        retry a request that cannot ever succeed.
        """

        if getattr(self._service, "_approval_policy", None) is None:
            raise SurfaceCompositionError(
                f"{what} is unavailable: this composition states no approval policy, "
                "and a missing policy authorises no host execution",
                code="approval_policy_required",
            )

    def run_record(self, run_id: str) -> dict[str, Any] | None:
        """One run's durable record, or ``None`` when this store has no such run.

        Delegates to ``ForgeApiService.get_run_record``, which already adds the
        ``classification`` key. Exposed here so a route does not reach into the
        service's private state to ask the same question.
        """

        return self._service.get_run_record(run_id)

    # ------------------------------------------------------------------
    # Project Memory
    # ------------------------------------------------------------------
    def _freshness(self, snapshot: UnderstandingSnapshot) -> FreshnessIndex:
        return FreshnessIndex.build(snapshot.files)

    def _memory_projection(
        self, memory: ProjectMemory, index: FreshnessIndex
    ) -> dict[str, Any]:
        """Classify every memory entry into exactly one honesty bucket.

        The buckets are mutually exclusive and are computed from the record's own
        ``kind``/``status``/``trust_level`` plus a freshness verdict against the
        workspace. ``CONFIRMED`` never contains a hypothesis: the classification is
        read from the record, not assigned here.
        """

        buckets: dict[str, list[dict[str, Any]]] = {
            "confirmed_facts": [],
            "verified_conclusions": [],
            "execution_results": [],
            "hypotheses": [],
            "unknown_data": [],
            "stale": [],
        }
        for record in memory.records:
            verdict = freshness_of(record, index)
            entry = record.to_dict()
            entry["freshness"] = verdict.to_dict()
            entry["is_current"] = record.is_current
            if record.kind is MemoryItemKind.CONFIRMED_FACT:
                target = "confirmed_facts"
            elif record.kind is MemoryItemKind.VERIFIED_CONCLUSION:
                target = "verified_conclusions"
            elif record.kind is MemoryItemKind.EXECUTION_RESULT:
                target = "execution_results"
            elif record.kind is MemoryItemKind.UNKNOWN_DATA:
                target = "unknown_data"
            else:
                target = "hypotheses"
            if record.status in {MemoryStatus.STALE, MemoryStatus.INVALIDATED}:
                # A non-current entry is reported once, in the stale bucket, so a
                # reader can never count the same entry as both current and stale.
                buckets["stale"].append(entry)
                continue
            if not verdict.is_fresh:
                buckets["stale"].append(entry)
                continue
            buckets[target].append(entry)
        return {
            "project_id": memory.project_id or self._project_id(),
            "updated_at": memory.updated_at,
            "buckets": buckets,
            "counts": {name: len(items) for name, items in buckets.items()},
            "distinctions": {
                "confirmed_facts": (
                    "cited an evidence fact id produced by the understanding layer"
                ),
                "verified_conclusions": (
                    "an inference at INFERRED trust; it is never promoted to a fact"
                ),
                "hypotheses": (
                    "an assertion with no structural evidence, including goal entry"
                ),
                "stale": "the pinned file changed, was deleted, or was invalidated",
            },
        }

    def memory_view(self) -> dict[str, Any]:
        """Project Memory, with confirmed facts separated from hypotheses."""

        memory = self._memory_store.load()
        snapshot = self._snapshotter.create_snapshot(
            self._workspace, project_id=self._require_project()
        )
        return self._memory_projection(memory, self._freshness(snapshot))

    def record_hypothesis(
        self, statement: object, *, kind: str = "HYPOTHESIS", subject: str = ""
    ) -> dict[str, Any]:
        """Record an operator assertion at hypothesis-or-unknown strength.

        A caller may not choose ``CONFIRMED_FACT`` or ``VERIFIED_CONCLUSION``; the
        allow-list refuses it with a machine code. This is the rule the block asks
        for, implemented where the write happens rather than in the UI.

        ``subject`` IS A REFERENCE, NOT A PIN, AND IT IS STORED AS ONE. It used to be
        written into ``scope.paths``, which implied a freshness check that cannot
        happen: ``freshness_of`` reports ``UNPINNED`` for any record whose
        ``pin.is_file_pinned`` is false, and this surface never sets a pin -- it has
        no scanner fingerprint to pin with. So a hypothesis naming a file looked
        pinned and was permanently ``UNPINNED`` anyway, and the parameter implied a
        guarantee the code did not make. The path is still validated exactly as
        before (``_normalize_subject`` refuses absolute paths, ``..`` and reserved
        device names) and is still recorded, but under a key that says what it is.
        """

        if not isinstance(statement, str) or not statement.strip():
            raise Refused("statement must be a non-empty string", code="empty_statement")
        try:
            chosen = MemoryItemKind(str(kind).strip().upper())
        except ValueError:
            raise Refused(f"unknown memory kind {kind!r}", code="unknown_kind") from None
        if chosen not in OWNER_RECORDABLE_KINDS:
            raise Refused(
                f"{chosen.value} may not be recorded by a caller: only "
                f"{sorted(item.value for item in OWNER_RECORDABLE_KINDS)} are "
                "assertable here, because a confirmed fact or a verified conclusion "
                "must cite evidence this route cannot supply",
                code="kind_not_recordable",
            )

        details: dict[str, Any] = {"entered_by": "operator"}
        if subject:
            # Validated, then recorded as a non-pinning reference. It is deliberately
            # NOT placed in ``scope.paths``: that field feeds the freshness projection,
            # and nothing here can supply the fingerprint a pin requires.
            details["subject_reference"] = self._normalize_subject(subject)
            details["subject_reference_note"] = (
                "recorded as a reference; this entry is not pinned to the file and "
                "its freshness is UNPINNED"
            )

        memory = self._memory_store.load()
        memory_id = f"owner-{len(memory.records)}"
        while memory.by_id(memory_id) is not None:
            memory_id = f"{memory_id}x"
        entry = MemoryRecord(
            memory_id=memory_id,
            kind=chosen,
            statement=statement.strip(),
            source=ContextSourceType.EXPLICIT_INPUT,
            source_id=memory_id,
            scope=MemoryScope(project_id=self._require_project(), paths=()),
            trust_level=ContextTrustLevel.INFERRED,
            details=details,
        )
        self._memory_store.save(memory.record(entry))
        return {
            "recorded": True,
            "memory_id": memory_id,
            "kind": chosen.value,
            "trust_level": ContextTrustLevel.INFERRED.value,
            "confirmed_fact": chosen is MemoryItemKind.CONFIRMED_FACT,
            # Reported so a caller can see that the subject did NOT become a pin.
            "pinned": False,
            "subject_reference": details.get("subject_reference", ""),
        }

    def _normalize_subject(self, subject: object) -> str:
        """Validate a caller-supplied *relative* subject path.

        Refused rather than resolved: ``normalize_relative_path`` is the same
        validator the workspace itself uses, so an absolute path, a drive letter,
        a ``..`` component or a reserved device name never becomes a path here.
        """

        try:
            parts = Workspace.normalize_relative_path(subject)
        except WorkspacePathError as exc:
            raise Refused(
                f"subject must be a relative workspace path: {type(exc).__name__}",
                code="unsafe_subject_path",
            ) from exc
        return "/".join(parts)

    def invalidate_memory(self, memory_id: object, *, reason: object = "") -> dict[str, Any]:
        """Invalidate one entry explicitly, keeping its whole history.

        Nothing is deleted, and the reason is recorded verbatim in the entry's
        history. This is the only mutation of an existing entry this surface
        offers; it can weaken a claim, never strengthen one.
        """

        if not isinstance(memory_id, str) or not memory_id.strip():
            raise Refused("memory_id must be a non-empty string", code="empty_memory_id")
        text = str(reason).strip() if reason else "invalidated by the operator"
        memory = self._memory_store.load()
        try:
            updated = memory.invalidate(memory_id.strip(), reason=text)
        except KeyError as exc:
            raise Refused(
                f"unknown memory id {memory_id.strip()!r}", code="unknown_memory_id"
            ) from exc
        self._memory_store.save(updated)
        return {"invalidated": True, "memory_id": memory_id.strip(), "reason": text}

    def refresh_memory(self) -> dict[str, Any]:
        """Re-evaluate every pinned entry against the workspace as it is now.

        This is where a file change invalidates a fact: the entry's pin carries the
        scanner's own fingerprint, and a mismatch moves the entry to ``STALE`` with
        a reason code naming the observation (content changed, deleted, renamed).
        An entry whose pinned file is gone is never quietly kept as current.
        """

        memory = self._memory_store.load()
        snapshot = self._snapshotter.create_snapshot(
            self._workspace, project_id=self._require_project()
        )
        index = self._freshness(snapshot)
        updated, result = memory.refresh(index)
        updated, contradiction_ids = updated.detect_contradictions()
        projection = self._memory_projection(updated, index)
        changed = bool(result.stale_memory_ids) or bool(result.renamed) or bool(
            result.deleted
        )
        if changed or contradiction_ids:
            self._memory_store.save(updated)
        return {
            "refreshed": True,
            "wrote": bool(changed or contradiction_ids),
            "refresh": result.to_dict(),
            "contradictions_added": len(contradiction_ids),
            # A refresh checks every *file-pinned* entry. An entry with no pin has
            # no file to compare against, so it is not "checked" -- saying
            # otherwise would imply a freshness verdict this run did not make.
            "checked_note": (
                "only file-pinned entries are checked; an entry with no pinned "
                "source has nothing to compare against and is left as it is"
            ),
            "memory": projection,
        }

    # ------------------------------------------------------------------
    # change impact
    # ------------------------------------------------------------------
    def capture_baseline(self) -> dict[str, Any]:
        """Record the workspace as it is now, as the reference point for change.

        Deliberately separate from ``change_impact``: an operator chooses *when*
        the reference point is taken, so "what changed since I last looked" is a
        question with a real answer rather than one the server silently redefines
        on every request. This writes a snapshot and nothing else -- it starts no
        action and grants no authority.
        """

        snapshot = self._snapshotter.create_snapshot(
            self._workspace, project_id=self._require_project()
        )
        self._persist_baseline(snapshot)
        self._baseline = _Baseline(snapshot)
        return {
            "recorded": True,
            "baseline_run_id": self.BASELINE_ID,
            "snapshot_id": snapshot.snapshot_id,
            "workspace_fingerprint": snapshot.workspace_fingerprint,
            "file_count": len(snapshot.files),
            "fact_count": len(snapshot.facts),
            "created_at": snapshot.created_at,
        }

    def change_impact(
        self, *, baseline: object = "", read_only: bool = True
    ) -> dict[str, Any]:
        """Link observed file changes to dependent facts, requirements and checks.

        What this **does** prove, from data the server holds:

        * which files changed, were added, or were deleted, by comparing the
          scanner's own SHA-256 fingerprints -- not a timestamp, not a claim;
        * which structural facts the scanner extracted from those files, and which
          topology edges cite those facts (so a dependent module is *named*);
        * which memory entries are pinned to a changed file and are therefore stale;
        * which declared acceptance expectations name a changed path, so the check
          that must be re-run is named rather than guessed.

        What this **does not** claim: full traceability. A dependent *check* is
        named only when a declared expectation literally references the path. Where
        a requirement cannot be linked to a changed file by the data at hand, the
        requirement is reported under ``unlinked_requirements`` -- an honest "not
        established" rather than an invented edge.
        """

        applied = False
        base: UnderstandingSnapshot | None = None
        seeded_now = False
        if baseline:
            # An explicitly named baseline must be a safe run id, and it must be a
            # record this server actually holds. An unknown id is refused rather
            # than silently replaced by the default baseline, because answering a
            # question about a different baseline than the one asked for is worse
            # than refusing.
            wanted = self.validate_run_id(baseline)
            base = self._load_baseline(wanted)
            if base is None:
                raise Refused(
                    f"no baseline snapshot is recorded under {wanted!r}",
                    code="unknown_baseline",
                )
            applied = True
        elif self._baseline is not None:
            base = self._baseline.snapshot
        else:
            base = self._load_baseline(self.BASELINE_ID)
            if base is None:
                base = self._snapshotter.create_snapshot(
                    self._workspace, project_id=self._require_project()
                )
                if not read_only:
                    self._persist_baseline(base)
                seeded_now = True
            self._baseline = _Baseline(base)

        current = self._snapshotter.create_snapshot(
            self._workspace, project_id=self._require_project()
        )
        analysis = self.change_analysis(
            base=base, current=current, read_only=read_only
        )
        # ONE REPRESENTATION. The Block #8 keys and the Block #9 reasoning fields
        # are read from the same engine finding, so the report and the verification
        # plan cannot disagree about what changed or why.
        changes = _surface_changes(base, current, analysis)

        changed_paths = {entry["path"] for entry in changes}
        criteria, unlinked = self._criteria_touched(changed_paths)
        memory = self._memory_store.load()
        index = self._freshness(current)
        stale_entries = [
            {
                "memory_id": record.memory_id,
                "statement": record.statement,
                "kind": record.kind.value,
                "pinned_path": record.pin.source_path,
                "freshness": freshness_of(record, index).to_dict(),
            }
            for record in memory.records
            if record.pin.source_path in changed_paths
        ]

        return {
            "project_id": self._project_id(),
            "baseline_snapshot_id": base.snapshot_id,
            "baseline_seeded_now": seeded_now,
            "baseline_applied_from_request": applied,
            "current_snapshot_id": current.snapshot_id,
            "workspace_fingerprint": current.workspace_fingerprint,
            "baseline_fingerprint": base.workspace_fingerprint,
            "changed_count": len(changes),
            "changes": changes,
            "memory_entries_pinned_to_changes": stale_entries,
            "criteria_touched": criteria,
            "unlinked_requirements": unlinked,
            # -- Block #9: the reasoning layer over the same observations ------
            "classification_counts": analysis["counts"],
            "stale_facts": analysis["stale_facts"],
            "dependent_conclusions": analysis["dependent_conclusions"],
            "unclassified_paths": analysis["unclassified_paths"],
            "analysis_notes": analysis["notes"],
            #: The checks this change set implies. Derived from the operator's own
            #: declared expectations and the scanner's own topology; no model is
            #: asked, and a check with no evidence is never reported as passed.
            "verification_plan": self.verification_plan(impact=analysis["report"].to_dict()),
            #: The traceability chain for the most recent graph attempt, so the same
            #: response answers "what changed" and "what did that change break".
            "evidence_graph": self.evidence_graph(),
            "claims": {
                "proved": [
                    "which files changed, by the scanner's own fingerprints",
                    "which structural facts those files carried, and which of them are "
                    "no longer current",
                    "which topology edges cite those facts",
                    "which declared acceptance expectations name those paths",
                    "which memory entries are pinned to those paths",
                    "which memory conclusions cite evidence that is no longer current",
                ],
                "not_proved": [
                    "that a dependent check will fail; only that it names a changed path",
                    "full requirement-to-file traceability",
                    "any content-level diff: file contents are never returned",
                    "that an affected test covers the change: the relation is by path",
                    "that an UNKNOWN or SKIPPED check passed",
                ],
            },
            "note": (
                "no dangerous action is started by this report, and no assumption "
                "is promoted to a confirmed fact"
            ),
        }

    # ------------------------------------------------------------------
    # the change reasoning layer (Block #9)
    # ------------------------------------------------------------------
    def change_analysis(
        self,
        *,
        base: UnderstandingSnapshot | None = None,
        current: UnderstandingSnapshot | None = None,
        baseline: object = "",
        read_only: bool = False,
    ) -> dict[str, Any]:
        """Run the Change Impact Engine over two snapshots.

        ``OperatingSurface.change_impact`` calls this with the snapshots it already
        took, so a report costs one scan and not two. Called with no snapshots it
        resolves the baseline exactly as ``change_impact`` does.
        """

        from app.analysis.impact import ChangeImpactEngine

        if base is None:
            base = self._resolve_baseline(baseline, persist=not read_only)
        if current is None:
            current = self._snapshotter.create_snapshot(
                self._workspace, project_id=self._require_project()
            )
        memory = self._memory_store.load()
        if memory.project_id != self._require_project():
            memory = replace(memory, project_id=self._require_project())
        engine = ChangeImpactEngine(
            baseline=base,
            current=current,
            acceptance_specs=getattr(self._service, "acceptance_specs", {}) or {},
            memory=memory,
        )
        report = engine.report(freshness=self._freshness(current))
        staled = self.stale_facts(
            base=base, current=current, record_history=not read_only
        )
        findings_by_path = {finding.path: finding.to_dict() for finding in report.findings}
        return {
            "baseline": base,
            "current": current,
            "report": report,
            "findings_by_path": findings_by_path,
            "stale_facts": [item.to_dict() for item in report.stale_facts],
            "dependent_conclusions": [
                item.to_dict() for item in report.dependent_conclusions
            ],
            "unclassified_paths": list(report.unclassified_paths),
            "counts": report.counts,
            "notes": list(report.notes),
            "stale_facts_invalidated": staled,
        }

    def stale_facts(
        self,
        *,
        base: UnderstandingSnapshot | None = None,
        current: UnderstandingSnapshot | None = None,
        baseline: object = "",
        invalidate: bool = False,
        record_history: bool = True,
    ) -> dict[str, Any]:
        """Facts that were current at baseline and are not now, and their history.

        **History retention is measured, not claimed.** The first call records the
        stale-fact ids in the run store under this surface's own baseline record, so
        a fact that became stale is visible afterwards even if the file is restored
        and the fact becomes current again. ``history_retained`` reports the ids the
        store actually holds.

        ``invalidate=True`` additionally re-evaluates every *file-pinned* Project
        Memory entry through the existing ``ProjectMemory.refresh``, which moves a
        changed, deleted or renamed source to ``STALE`` and appends a
        ``STALE_ENTRY`` item rather than deleting anything. It is off by default
        because a read must not write; the change-impact route never turns it on.
        """

        if base is None:
            base = self._resolve_baseline(baseline, persist=not record_history)
        if current is None:
            current = self._snapshotter.create_snapshot(
                self._workspace, project_id=self._require_project()
            )
        from app.analysis.impact import ChangeImpactEngine

        memory = self._memory_store.load()
        engine = ChangeImpactEngine(
            baseline=base,
            current=current,
            acceptance_specs=getattr(self._service, "acceptance_specs", {}) or {},
            memory=memory,
        )
        index = self._freshness(current)
        report = engine.report(freshness=index)

        ids = [item.fact_id for item in report.stale_facts]
        if record_history:
            retained = self._remember_stale_facts(ids)
        else:
            # A read reports what is already recorded and writes nothing. This is
            # the difference the GET paths depend on: answering with history must
            # not be the same act as extending it.
            retained = self._recorded_stale_fact_ids()

        payload: dict[str, Any] = {
            "stale_facts": [item.to_dict() for item in report.stale_facts],
            "stale_fact_ids": ids,
            "history_retained": retained,
            "dependent_conclusions": [
                item.to_dict() for item in report.dependent_conclusions
            ],
            "reasons": sorted({item.reason for item in report.stale_facts}),
            "reason_vocabulary": [
                "source_content_changed",
                "source_deleted",
                "source_renamed",
                "source_deleted_with_ambiguous_move_candidates",
                "fact_not_extracted_from_current_content",
            ],
            "invalidation_performed": False,
            "invalidated_memory_ids": [],
            "note": (
                "a fact is stale when the same fact id is absent from the current "
                "scan of the same source path; nothing is deleted and nothing is "
                "promoted"
            ),
        }

        if invalidate:
            updated, result = memory.refresh(index)
            updated, contradiction_ids = updated.detect_contradictions()
            changed = bool(result.stale_memory_ids) or bool(result.renamed) or bool(
                result.deleted
            )
            if changed or contradiction_ids:
                self._memory_store.save(updated)
            payload["invalidation_performed"] = True
            payload["invalidated_memory_ids"] = list(result.stale_memory_ids)
            payload["refresh"] = result.to_dict()
            payload["memory_history_kept"] = True
        return payload

    def _recorded_stale_fact_ids(self) -> list[str]:
        """The stale-fact ids already recorded for this surface, read-only."""

        existing = self._run_store.read_state(self.BASELINE_ID)
        if existing is None or not existing.project_state:
            return []
        previous = existing.project_state.get("stale_fact_history")
        if isinstance(previous, Mapping):
            raw = previous.get("fact_ids")
        elif isinstance(previous, (list, tuple)):
            raw = previous
        else:
            return []
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(item) for item in raw]

    def _remember_stale_facts(self, fact_ids: Sequence[str]) -> list[str]:
        """Merge stale fact ids into the baseline record's own ``project_state``.

        One store, one record, additive keys. Nothing here overwrites a step-graph
        or recovery entry: the baseline record is this surface's own.
        """

        from app.runtime.run_store import RunStateSnapshot

        existing = self._run_store.read_state(self.BASELINE_ID)
        project_state: dict[str, Any] = (
            dict(existing.project_state)
            if existing is not None and existing.project_state
            else {}
        )
        previous = project_state.get("stale_fact_history")
        seen: list[str] = []
        if isinstance(previous, Mapping):
            raw = previous.get("fact_ids")
            if isinstance(raw, (list, tuple)):
                seen = [str(item) for item in raw]
        elif isinstance(previous, (list, tuple)):
            seen = [str(item) for item in previous]
        merged = list(dict.fromkeys(seen + [str(item) for item in fact_ids]))
        project_state["stale_fact_history"] = {
            "fact_ids": merged,
            "updated_at": self._now_iso(),
        }
        try:
            self._run_store.write_state(
                RunStateSnapshot(
                    run_id=self.BASELINE_ID,
                    task_id=existing.task_id if existing else "",
                    state=existing.state if existing else "",
                    status=existing.status if existing else "",
                    attempt_number=existing.attempt_number if existing else 0,
                    completed_attempt_indexes=(
                        existing.completed_attempt_indexes if existing else ()
                    ),
                    project_state=project_state,
                    updated_at=self._now_iso(),
                )
            )
        except Exception:  # noqa: BLE001 - history is an addition, never a gate
            return seen
        return merged

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _resolve_baseline(
        self, baseline: object = "", *, persist: bool = True
    ) -> UnderstandingSnapshot:
        """The baseline snapshot, resolved exactly as ``change_impact`` resolves it."""

        if baseline:
            wanted = self.validate_run_id(baseline)
            base = self._load_baseline(wanted)
            if base is None:
                raise Refused(
                    f"no baseline snapshot is recorded under {wanted!r}",
                    code="unknown_baseline",
                )
            return base
        if self._baseline is not None:
            return self._baseline.snapshot
        base = self._load_baseline(self.BASELINE_ID)
        if base is None:
            base = self._snapshotter.create_snapshot(
                self._workspace, project_id=self._require_project()
            )
            if persist:
                self._persist_baseline(base)
            # Read-only callers still get a usable in-memory baseline; they just do
            # not create durable state as a side effect of being asked a question.
            self._baseline = _Baseline(base, seeded=True)
        else:
            self._baseline = _Baseline(base)
        return base

    def verification_plan(self, *, impact: object = None) -> dict[str, Any]:
        """Plan the checks this change set implies, with an explicit state each.

        Built from the operator's own declared expectations, the scanner's own
        topology, and the recorded step results. No model is consulted, no
        dependency is guessed, and ``PASSED`` is the only state counted as a pass.

        When no change report is supplied, this method **computes one**, so the
        plan a route returns is always driven by the real change set. An earlier
        revision planned against ``None``, and a plan with no ``driven_by`` on any
        check is indistinguishable from a plan for an unchanged project.
        """

        if impact is None:
            report = self.change_analysis(read_only=True)
            impact = report["report"]
        impact = self._as_impact_payload(impact)
        planner = self._verification_planner()
        return planner.plan(impact).to_dict()

    @staticmethod
    def _as_impact_payload(impact: object) -> Mapping[str, Any]:
        """Normalize a change report into the mapping the planner reads.

        MEASURED DEFECT, and the reason this is a named function rather than an
        inline ``isinstance(impact, Mapping)``: ``ChangeImpactReport`` is a
        dataclass, **not** a ``Mapping``. An earlier revision converted anything
        that was not a Mapping through ``to_dict`` and then planned against the
        result -- so passing a report object worked by accident, while passing a
        plain object with no ``to_dict`` produced a plan for an *empty* change set.
        A plan with no ``driven_by`` on any check is indistinguishable from a plan
        for an unchanged project, which is exactly the false negative this raises
        instead.
        """

        if isinstance(impact, Mapping):
            return impact
        converter = getattr(impact, "to_dict", None)
        if callable(converter):
            payload = converter()
            if isinstance(payload, Mapping):
                return payload
        raise OperatingSurfaceError(
            "the change report handed to the verification planner must be a mapping "
            f"or expose to_dict(); it was {type(impact).__name__}, so no plan can be "
            "derived from it and none is invented",
            code="impact_payload_invalid",
        )

    def _verification_planner(self) -> Any:
        from app.analysis.verification import VerificationPlanner

        _graph_run_id, snapshot = self._latest_graph_attempt()
        baseline = self._load_baseline(self.BASELINE_ID)
        return VerificationPlanner(
            acceptance_specs=getattr(self._service, "acceptance_specs", {}) or {},
            declarations=getattr(self._service, "declarations", {}) or {},
            step_records=tuple(getattr(snapshot, "steps", ()) or ()) if snapshot else (),
            declared_plan=(
                tuple(getattr(snapshot, "declared_plan", ()) or ()) if snapshot else ()
            ),
            graph_status=(
                getattr(getattr(snapshot, "status", None), "value", "") if snapshot else ""
            ),
            baseline_present=baseline is not None,
        )

    def _latest_graph_attempt(self) -> tuple[str, Any]:
        """The most recent recorded step-graph attempt, or ``("", None)``.

        Read through the runner's own ``recorded``, so this surface reads the same
        record the runner would resume from. Never resumes anything.
        """

        runner = self.build_step_graph_runner()
        if runner is None:
            return "", None
        for run_id in self._run_store.list_run_ids_by_recency():
            try:
                record = runner.recorded(run_id)
            except Exception:  # noqa: BLE001 - an unreadable record is simply skipped
                continue
            if record is not None:
                return run_id, record
        return "", None

    def evidence_graph(self, *, graph_run_id: object = "") -> dict[str, Any]:
        """The traceability chain, from the goal to a computed final status.

        Assembled from the surface's own reads. Every transition cites concrete
        identifiers, and a transition with no evidence is emitted as ``MISSING``
        with a machine code naming which link is absent -- never dropped, never
        invented. When a mandatory link is missing the graph **refuses to emit
        ``ACCEPTED``** and returns ``ACCEPTED_REFUSED_INCOMPLETE_TRACE`` instead,
        with the reason.
        """

        from app.analysis.evidence import EvidenceGraphBuilder

        if graph_run_id:
            wanted = self.validate_run_id(graph_run_id)
        else:
            wanted, _ = self._latest_graph_attempt()

        runner = self.build_step_graph_runner()
        snapshot = None
        if wanted and runner is not None:
            try:
                snapshot = runner.recorded(wanted)
            except Exception:  # noqa: BLE001 - a damaged record is reported as absent
                snapshot = None
        if snapshot is None and wanted and runner is None:
            # Report the id but never fabricate a record for it.
            snapshot = None

        baseline = self._load_baseline(self.BASELINE_ID)
        current = self._snapshotter.create_snapshot(
            self._workspace, project_id=self._require_project()
        )
        baseline_fingerprints: dict[str, str | None] = {}
        if baseline is not None:
            baseline_fingerprints = {
                entry.relative_path: entry.fingerprint for entry in baseline.files
            }
        current_fingerprints = {
            entry.relative_path: entry.fingerprint for entry in current.files
        }

        memory = self._memory_store.load()
        goal_records = [
            record
            for record in memory.records
            if record.kind is MemoryItemKind.HYPOTHESIS
            and str(record.details.get("role", "")) == "goal"
        ]

        approvals: dict[str, str] = {}
        if snapshot is not None and runner is not None:
            graph = None
            try:
                from app.workflows.step_graph import graph_from_declared_plan

                graph = graph_from_declared_plan(
                    snapshot.graph_id, snapshot.declared_plan
                )
            except Exception:  # noqa: BLE001 - the fingerprint is an addition
                graph = None
            if graph is not None:
                for record in snapshot.steps:
                    try:
                        approvals[str(record.step_id)] = runner.intent_fingerprint(
                            graph, record.step_id
                        )
                    except Exception:  # noqa: BLE001
                        continue
                    # The fingerprint actually dispatched is the one the record kept
                    # in ``action_fingerprint``... no: that is the DECLARED plan's
                    # fingerprint, a different value from the intent fingerprint. The
                    # intent fingerprint is only comparable for a step the runner can
                    # still recompose, which is why a step whose plan cannot be
                    # recomposed yields no comparison rather than a false match.
            for record in snapshot.steps:
                if record.action_fingerprint:
                    approvals[f"{record.step_id}#declared_action"] = record.action_fingerprint

        operation_states = self._operation_states(snapshot)

        builder = EvidenceGraphBuilder(
            project_id=self._require_project(),
            goal_records=goal_records,
            declarations=getattr(self._service, "declarations", {}) or {},
            acceptance_specs=getattr(self._service, "acceptance_specs", {}) or {},
            step_records=tuple(getattr(snapshot, "steps", ()) or ()) if snapshot else (),
            graph_run_id=wanted,
            declared_plan=(
                tuple(getattr(snapshot, "declared_plan", ()) or ()) if snapshot else ()
            ),
            approval_fingerprints=approvals,
            current_fingerprints=current_fingerprints,
            baseline_fingerprints=baseline_fingerprints,
            operation_states=operation_states,
            graph_status=(
                getattr(getattr(snapshot, "status", None), "value", "")
                if snapshot is not None
                else ""
            ),
            stopped_reason=(
                str(getattr(snapshot, "stopped_reason", "")) if snapshot else ""
            ),
            agent_claims=self._agent_claims(wanted),
            baseline_present=baseline is not None,
        )
        graph_payload = builder.build().to_dict()
        graph_payload["graph_run_id"] = wanted
        graph_payload["attempt_recorded"] = snapshot is not None
        graph_payload["baseline_snapshot_id"] = (
            getattr(baseline, "snapshot_id", "") if baseline is not None else ""
        )
        graph_payload["graph_status_reported"] = (
            getattr(getattr(snapshot, "status", None), "value", "")
            if snapshot is not None
            else ""
        )
        return self._acceptance_gate(graph_payload)

    def _operation_states(self, snapshot: Any) -> dict[str, str]:
        """The durable ledger state of each step's own operation identity.

        Read through the existing ``RunIdempotencyGuard.recorded_state``. An absent
        record is reported as ``no_record``; it is never treated as a success.
        """

        runner = self.build_step_graph_runner()
        if runner is None or snapshot is None:
            return {}
        # A method, not a property: see the same note in ``_repeat_guarantees``.
        guard = runner.ledger_guard()
        states: dict[str, str] = {}
        try:
            from app.workflows.step_graph import graph_from_declared_plan

            graph = graph_from_declared_plan(snapshot.graph_id, snapshot.declared_plan)
        except Exception:  # noqa: BLE001
            return states
        for definition in graph.steps:
            try:
                identity = runner.operation_identity(
                    graph, definition, snapshot.graph_run_id
                )
                state = guard.recorded_state(identity)
            except Exception:  # noqa: BLE001
                continue
            states[definition.step_id] = (
                getattr(state, "value", str(state)) if state is not None else "no_record"
            )
        return states

    @staticmethod
    def _agent_claims(graph_run_id: str) -> list[dict[str, Any]]:
        """Claims an agent made, kept in a compartment the verdict never reads.

        This surface does not read a model's output: the Block #8 composition makes
        no generative dispatch on the execution path. The compartment therefore
        reports that it is empty *and why*, rather than being omitted -- an absent
        section would leave a reader unable to tell "no claim was made" from
        "claims are not collected".
        """

        return []

    # ------------------------------------------------------------------
    # the bounded repair decision (Block #9)
    # ------------------------------------------------------------------
    def _repeat_guarantees(self, snapshot: Any) -> dict[str, Any]:
        """The measured repeat guarantee of each step's own operation identity.

        Read through the existing ``RunIdempotencyGuard.repeat_decision``. This adds
        no new durability and no new decision table: it reports which of the
        documented guarantees the ledger's own evidence supports.
        """

        runner = self.build_step_graph_runner()
        if runner is None or snapshot is None:
            return {}
        # ``ledger_guard`` IS A METHOD, NOT A PROPERTY. Reading it without calling it
        # made every ``repeat_decision`` call raise ``AttributeError``, which the
        # per-step ``except`` swallowed -- so this method silently returned an empty
        # map and the repair loop planned against NO measured guarantee at all. A
        # silent empty map is the worst possible outcome here: "the ledger says
        # nothing" and "the ledger could not be read" must not look alike.
        guard = runner.ledger_guard()
        try:
            from app.workflows.step_graph import graph_from_declared_plan

            graph = graph_from_declared_plan(snapshot.graph_id, snapshot.declared_plan)
        except Exception:  # noqa: BLE001 - an unreadable plan yields no guarantee
            return {}
        decisions: dict[str, Any] = {}
        for definition in graph.steps:
            try:
                identity = runner.operation_identity(
                    graph, definition, snapshot.graph_run_id
                )
                decisions[definition.step_id] = guard.repeat_decision(identity)
            except Exception:  # noqa: BLE001 - reported as a missing guarantee below
                continue
        return decisions

    def repair_plan(
        self,
        graph_run_id: object,
        *,
        attempt_cap: object = None,
        step_budget: object = None,
        seconds_budget: object = None,
        previous_progress_marker: object = "",
    ) -> dict[str, Any]:
        """A bounded repair decision for one recorded attempt. Dispatches nothing.

        Read-only. The budget, the attempt cap, the no-progress rule and the refusal
        path for an unknown side effect are all decided here from the durable ledger
        and the recorded attempt; the response names the single rule that stopped
        the loop. See ``app/analysis/repair.py`` for why step 6 of the loop (the
        repair itself) is deliberately not performed on this surface.
        """

        from app.analysis.repair import RepairLoop

        run_id = self.validate_run_id(graph_run_id)
        runner = self.build_step_graph_runner()
        if runner is None:
            raise Refused(
                "no step-graph runner is composed: host execution is disabled",
                code="step_graph_unavailable",
            )
        try:
            snapshot = runner.recorded(run_id)
        except Exception as exc:  # noqa: BLE001
            raise Refused(
                f"the recorded attempt cannot be read: {_safe_reason(exc)}",
                code="graph_state_refused",
            ) from exc
        if snapshot is None:
            return {
                "graph_run_id": run_id,
                "found": False,
                "outcome": "BLOCKED_NO_TARGET",
                "stop_rule": "no_target",
                "dispatch_performed": False,
                "note": "no attempt is recorded under this id",
            }

        ingredients = self._service.step_graph_composition() or {}
        loop = RepairLoop(
            graph_run_id=run_id,
            snapshot=snapshot,
            declarations=getattr(self._service, "declarations", {}) or {},
            repeat_decisions=self._repeat_guarantees(snapshot),
            approval_policy=ingredients.get("approval_policy"),
            approval_resolver=ingredients.get("approval_resolver"),
            attempt_cap=_bounded_int(attempt_cap, default=3, low=1, high=50),
            step_budget=_bounded_int(step_budget, default=8, low=1, high=200),
            seconds_budget=_bounded_float(
                seconds_budget, default=900.0, low=1.0, high=86400.0
            ),
            previous_progress_marker=previous_progress_marker or "",
        )
        payload = loop.plan().to_dict()
        payload["found"] = True
        payload["note"] = (
            "this route plans and decides; it starts no repair, and it never turns "
            "an unknown side-effect outcome into permission to repeat"
        )
        return payload

    @staticmethod
    def _acceptance_gate(payload: dict[str, Any]) -> dict[str, Any]:
        """Refuse ``ACCEPTED`` when a mandatory link is missing.

        The mandatory links are the ones without which "this was accepted" has no
        meaning: a goal, a requirement, an acceptance criterion, a step record, an
        approval decision, a performed command, and a declared verification result.
        A missing link anywhere in that set refuses the acceptance and names the
        codes.
        """

        mandatory = {
            "no_goal_recorded": "goal",
            "requirement_has_no_declared_criterion": "acceptance_criterion",
            "criterion_has_no_verification_expectation": "verification_expectation",
            "no_step_targets_this_requirement": "plan_step",
            "no_attempt_recorded_for_this_step": "step_record",
            "no_approval_decision_recorded": "approval_decision",
            "no_authorized_action_recorded": "authorized_action",
            "no_command_performed": "command",
            "no_declared_verification_for_this_step": "verification_result",
            "step_verification_not_run": "verification_result",
            "step_verification_unevaluable": "verification_result",
            "no_recorded_attempt": "attempt",
        }
        missing_codes = sorted(
            {
                str(link.get("missing_code", ""))
                for link in payload.get("missing_links", ())
                if str(link.get("missing_code", "")) in mandatory
            }
        )
        payload["mandatory_missing_codes"] = missing_codes
        payload["mandatory_missing_kinds"] = sorted(
            {mandatory[code] for code in missing_codes}
        )
        if missing_codes:
            payload["final_status"] = "ACCEPTED_REFUSED_INCOMPLETE_TRACE"
            payload["acceptance_refused"] = True
            payload["acceptance_refusal_reason"] = (
                "mandatory traceability is incomplete: "
                + ", ".join(missing_codes)
                + "; a task is not accepted because an agent said so, and it is not "
                "accepted while a mandatory link has no evidence"
            )
        else:
            payload["final_status"] = payload.get("verdict", "UNPROVEN")
            payload["acceptance_refused"] = False
            payload["acceptance_refusal_reason"] = ""
        payload["verdict_source"] = (
            "computed from the recorded verification observations and the "
            "requirements' own criteria; no agent claim is read"
        )
        return payload

    @staticmethod
    def _dependents(
        affected_facts: Sequence[Mapping[str, Any]],
        edges_by_fact: Mapping[str, list[Any]],
        node_by_id: Mapping[str, Any],
    ) -> tuple[list[dict[str, Any]], bool]:
        """Topology nodes reached from an affected fact, capped and honest.

        NOT DELETED, AND DELIBERATELY NOT CALLED BY ``change_impact`` ANY MORE.
        Block #9 moved this reasoning into ``app.analysis.impact`` so there is one
        implementation of "which dependents does this fact reach" instead of two
        that could drift. Measured: ``change_impact`` now builds its ``changes`` list
        through ``_surface_changes`` and reads ``dependent_nodes`` from the engine
        finding, so this method has no production caller. It is left in place, with
        this note, because it is the shape an external consumer of the surface may
        still be reading and because deleting it is a wider change than this task
        authorises; the engine's own ``_dependents`` is the live one.
        """

        seen: dict[str, dict[str, Any]] = {}
        for fact in affected_facts:
            for edge in edges_by_fact.get(str(fact.get("fact_id", "")), []):
                for node_id in (edge.source_node_id, edge.target_node_id):
                    if node_id in seen:
                        continue
                    node = node_by_id.get(node_id)
                    if node is None:
                        continue
                    seen[node_id] = {
                        "node_id": node_id,
                        "node_type": node.node_type.value,
                        "name": node.name,
                        "relative_path": node.relative_path,
                        "via_edge": edge.edge_type.value,
                    }
        ordered = [seen[key] for key in sorted(seen)]
        truncated = len(ordered) > MAX_DEPENDENT_NODES
        return ordered[:MAX_DEPENDENT_NODES], truncated

    def _criteria_touched(
        self, changed_paths: set[str]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Declared criteria whose expectation names a changed path.

        A criterion is named only on a literal path match between its declared
        expectation and a changed file. Everything else is reported as unlinked,
        because inventing an edge here is exactly the false traceability the block
        forbids.
        """

        specs = getattr(self._service, "acceptance_specs", {})
        touched: list[dict[str, Any]] = []
        linked_requirements: set[str] = set()
        all_requirements: set[str] = set()
        for declaration_id, spec in sorted(specs.items()):
            for criterion in spec.criteria:
                all_requirements.add(str(getattr(criterion, "requirement_id", "")))
                expectation = spec.expectations.get(criterion.criterion_id)
                if expectation is None:
                    continue
                if expectation.relative_path in changed_paths:
                    linked_requirements.add(str(getattr(criterion, "requirement_id", "")))
                    touched.append(
                        {
                            "declaration_id": declaration_id,
                            "criterion_id": criterion.criterion_id,
                            "requirement_id": getattr(criterion, "requirement_id", ""),
                            "description": criterion.description,
                            "expectation_path": expectation.relative_path,
                            "must_exist": bool(expectation.exists),
                            "action": "re-run verification for this criterion",
                        }
                    )
        unlinked = sorted(item for item in all_requirements - linked_requirements if item)
        return touched, unlinked

    def _persist_baseline(self, snapshot: UnderstandingSnapshot) -> None:
        """Store the baseline snapshot so a restart keeps a real reference point."""

        from app.runtime.run_store import RunStateSnapshot

        existing = self._run_store.read_state(self.BASELINE_ID)
        project_state: dict[str, Any] = (
            dict(existing.project_state)
            if existing is not None and existing.project_state
            else {}
        )
        project_state["understanding_baseline"] = snapshot.to_dict()
        self._run_store.write_state(
            RunStateSnapshot(
                run_id=self.BASELINE_ID,
                task_id=existing.task_id if existing else "",
                state=existing.state if existing else "",
                status=existing.status if existing else "",
                attempt_number=existing.attempt_number if existing else 0,
                completed_attempt_indexes=(
                    existing.completed_attempt_indexes if existing else ()
                ),
                project_state=project_state,
                updated_at=snapshot.created_at,
            )
        )

    def _load_baseline(self, baseline_id: str) -> UnderstandingSnapshot | None:
        """Read a persisted baseline snapshot, or ``None`` when there is none."""

        snapshot = self._run_store.read_state(baseline_id)
        if snapshot is None or not snapshot.project_state:
            return None
        payload = snapshot.project_state.get("understanding_baseline")
        if not isinstance(payload, Mapping):
            return None
        return _snapshot_from_dict(payload)


def _snapshot_from_dict(payload: Mapping[str, Any]) -> UnderstandingSnapshot | None:
    """Rebuild a snapshot from its own serialized form.

    Deliberately defensive: a damaged document yields ``None`` rather than a
    partly-invented snapshot, because a baseline the server cannot trust must not
    be used to decide what changed.
    """

    try:
        from app.understanding.models import (
            ManifestDescriptor,
            ProjectTopology,
            ScanWarning,
            StructuralFact,
            StructuralFactType,
            TopologyEdge,
            TopologyNode,
            NodeType,
            EdgeType,
        )
        from app.snapshots import SnapshotFile

        files = tuple(
            SnapshotFile(
                relative_path=str(item.get("relative_path", "")),
                exists=bool(item.get("exists", True)),
                fingerprint=item.get("fingerprint"),
            )
            for item in payload.get("files") or ()
            if isinstance(item, Mapping)
        )
        facts = tuple(
            StructuralFact.create(
                fact_type=StructuralFactType(str(item.get("fact_type", ""))),
                source_path=str(item.get("source_path", "")),
                line_start=int(item.get("line_start", 0) or 0),
                line_end=int(item.get("line_end", 0) or 0),
                extractor_id=str(item.get("extractor_id", "")),
                details=dict(item.get("details") or {}),
            )
            for item in payload.get("facts") or ()
            if isinstance(item, Mapping)
        )
        manifests = tuple(
            ManifestDescriptor(
                relative_path=str(item.get("relative_path", "")),
                manifest_type=str(item.get("manifest_type", "")),
            )
            for item in payload.get("manifests") or ()
            if isinstance(item, Mapping)
        )
        topology_payload = payload.get("topology") or {}
        nodes = tuple(
            TopologyNode(
                node_id=str(item.get("node_id", "")),
                node_type=NodeType(str(item.get("node_type", ""))),
                name=str(item.get("name", "")),
                relative_path=str(item.get("relative_path", "")),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in topology_payload.get("nodes") or ()
            if isinstance(item, Mapping)
        )
        edges = tuple(
            TopologyEdge(
                edge_id=str(item.get("edge_id", "")),
                edge_type=EdgeType(str(item.get("edge_type", ""))),
                source_node_id=str(item.get("source_node_id", "")),
                target_node_id=str(item.get("target_node_id", "")),
                fact_ids=tuple(str(fid) for fid in item.get("fact_ids") or ()),
                metadata=dict(item.get("metadata") or {}),
            )
            for item in topology_payload.get("edges") or ()
            if isinstance(item, Mapping)
        )
        warnings = tuple(
            ScanWarning(
                warning_type=str(item.get("warning_type", "")),
                message=str(item.get("message", "")),
                file_path=item.get("file_path"),
                line=item.get("line"),
            )
            for item in payload.get("warnings") or ()
            if isinstance(item, Mapping)
        )
        return UnderstandingSnapshot(
            snapshot_id=str(payload.get("snapshot_id", "")),
            project_id=str(payload.get("project_id", "")),
            workspace_fingerprint=str(payload.get("workspace_fingerprint", "")),
            files=files,
            manifests=manifests,
            topology=ProjectTopology(nodes=nodes, edges=edges),
            facts=facts,
            warnings=warnings,
            extractor_versions=dict(payload.get("extractor_versions") or {}),
            parent_snapshot_id=payload.get("parent_snapshot_id"),
            created_at=str(payload.get("created_at", "")),
        )
    except Exception:  # noqa: BLE001 - an untrustworthy baseline is no baseline
        return None


def _graph_from_request(
    graph_id: str, steps: Sequence[Mapping[str, Any]]
) -> Any:
    """Build a ``StepGraph`` from a request's step list.

    Construction only. The graph is *not* validated here: validation belongs to
    ``StepGraph.assert_valid`` against the operator's declaration registry, and it
    happens inside the runner before any scope is frozen. Duplicating that check
    here would create a second, weaker validator.

    The one thing this does normalize is the *encoding* of a step's parameters. The
    canonical persisted form is a sequence of ``[name, value]`` pairs, because
    order-insensitivity is part of the identity the approval is bound to. A JSON
    client naturally sends an object. Accepting both shapes is a codec concern and
    nothing more: the parameters are still passed through the same validator, so an
    unknown or widening parameter is refused exactly as before. A parameter object
    is converted in sorted-name order, which is the canonical order, so two
    encodings of the same parameters produce the same fingerprint.
    """

    from app.workflows.step_graph import StepDefinition, StepGraph, StepGraphError

    normalized: list[dict[str, Any]] = []
    for step in steps:
        payload = dict(step)
        action = payload.get("action")
        if isinstance(action, Mapping):
            action_payload = dict(action)
            parameters = action_payload.get("parameters")
            if isinstance(parameters, Mapping):
                action_payload["parameters"] = [
                    [str(name), str(value)]
                    for name, value in sorted(parameters.items(), key=lambda item: str(item[0]))
                ]
            payload["action"] = action_payload
        normalized.append(payload)
    try:
        definitions = [StepDefinition.from_dict(step) for step in normalized]
    except StepGraphError as exc:
        # The REQUEST DOCUMENT is malformed: an ``action`` that is missing, empty, or
        # names no usable declaration. ``StepDefinition.from_dict`` raises
        # ``StepGraphStateError`` for the missing-action case, but ``StepAction``'s own
        # validation raises the broader ``StepGraphError`` for an ``action`` whose
        # ``declaration_id`` is absent, empty, or not a string -- which is why guarding
        # on ``StepGraphStateError`` alone let that second case escape as a 500.
        #
        # This is the caller's shape error, so it becomes the surface's own request
        # error HERE, at the parse boundary, and nothing is executed.
        #
        # It is caught ONLY around the parse. Widening the route's ``errors_as_400`` to
        # ``StepGraphError`` was tried and rejected: the same class is raised on the
        # normal execution path, so a VALID graph stopped executing (``executed: false``,
        # no artifact). Narrow to the boundary, never to the whole ``start_graph`` call.
        raise OperatingSurfaceError(
            "a step in the request does not have the shape this route declares",
            code="invalid_request_schema",
        ) from exc
    return StepGraph(graph_id=str(graph_id) or "operator-plan", steps=tuple(definitions))


def build_operating_surface(service: Any) -> OperatingSurface:
    """The one place an ``OperatingSurface`` is constructed for the HTTP app."""

    return OperatingSurface(service)
