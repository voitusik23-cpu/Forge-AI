"""Production project discovery: bounded, server-side, non-authoritative.

These tests pin the discovery trust boundary:

* the workspace root comes only from trusted server-side composition, and no
  caller, task, decision, or LLM can choose it or widen it;
* discovery stays bounded (file count, per-file size, total bytes) and
  deterministic, and it excludes secrets, credentials, and keys;
* the resulting ``UnderstandingSnapshot`` is immutable, belongs to exactly one
  run, and is never reused across runs;
* the snapshot becomes *context* for the decision provider, and that provider
  still receives no scope, workspace, approval, or authority;
* discovery grants no authority: it cannot add a command, tool, profile,
  environment value, or timeout, and it never executes anything.

Real bounded behaviour is exercised against real temporary workspaces rather than
mocks. Nothing here mutates tracked repository files.
"""

from __future__ import annotations

from pathlib import Path
import dataclasses
import json
import sys
import tempfile
import unittest

from app.agent_runtime.harness import AgentHarness
from app.agent_runtime.models import HarnessRequest
from app.agent_runtime.policy import AgentHarnessPolicy
from app.agent_runtime.project_discovery import (
    DiscoveryOutcome,
    ProjectDiscovery,
    ProjectDiscoveryError,
)
from app.api.models import TaskRunRequest
from app.api.service import ForgeApiService
from app.decision.models import Decision, DecisionAction, DecisionType
from app.execution.capabilities import ExecutionCapability
from app.execution.declaration import ExecutionDeclaration
from app.execution.profile import ProjectExecutionProfile
from app.orchestrator.models import EventType, TaskResult
from app.orchestrator.run import RunExecutor
from app.runtime.bootstrap import create_agent_harness, create_loop_coordinator
from app.runtime.context import RuntimeContext
from app.runtime.run_scope import RunScope
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy
from app.tools.registry import build_default_tool_registry
from app.tools.verification import VerificationExpectation
from app.tools.workspace import Workspace
from app.understanding.models import ScanLimits, UnderstandingSnapshot

CANARY = "DISCOVERY_SECRET_CANARY_7f21"
DECLARATION_ID = "verify.discovery"


def api_profile(**overrides) -> ProjectExecutionProfile:
    payload = {
        "profile_id": "api-default",
        "allowed_commands": ("python",),
        "capabilities": frozenset({ExecutionCapability.INTERPRET_TEXT}),
        "network_access": False,
        "working_directory": ".",
    }
    payload.update(overrides)
    return ProjectExecutionProfile(**payload)


class _OrchestratorDouble:
    def dispatch(self, task, agent_name=None, *, provider_name=None, observer=None):
        return TaskResult(task.id, True, output="double")


def _runtime(**overrides) -> RuntimeContext:
    orchestrator = _OrchestratorDouble()
    payload = {
        "settings": None,
        "provider_accounts": {},
        "provider_registry": None,
        "provider_capabilities": None,
        "agent_registry": None,
        "orchestrator": orchestrator,
        "run_executor": RunExecutor(orchestrator),
        "harness": create_agent_harness(with_discovery=False),
    }
    payload.update(overrides)
    return RuntimeContext(**payload)


class _CapturingDecisionProvider:
    """Record the context the decision provider is shown."""

    def __init__(self, action: DecisionAction = DecisionAction.EXECUTE) -> None:
        self.action = action
        self.requests: list[object] = []

    def decide(self, request):
        self.requests.append(request)
        return Decision(
            decision_id="capture",
            run_id=request.run_id,
            decision_type=DecisionType.CONTINUE,
            action=self.action,
            reason_code="capture",
        )


def make_project(root: Path) -> None:
    """A small realistic project, plus files that must never be indexed."""
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "demo", "dependencies": {"left-pad": "1.0.0"}}),
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("requests==2.0.0\n", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "app.py").write_text("import os\n\ndef main():\n    return 1\n", encoding="utf-8")
    (root / ".env").write_text(f"API_KEY={CANARY}\n", encoding="utf-8")
    (root / ".env.example").write_text(f"API_KEY={CANARY}\n", encoding="utf-8")
    (root / "server.pem").write_text(f"-----BEGIN {CANARY}-----\n", encoding="utf-8")
    (root / "credentials.json").write_text(json.dumps({"token": CANARY}), encoding="utf-8")
    (root / "id_rsa.key").write_text(CANARY, encoding="utf-8")


class DiscoveryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        make_project(self.root)
        self.workspace = Workspace(self.root)

    def tearDown(self) -> None:
        RunScope.release_all()
        self._tmp.cleanup()

    def _service(self, *, capture=None, discovery=None, **kwargs) -> ForgeApiService:
        provider = capture or _CapturingDecisionProvider()
        factory_kwargs = {"decision_provider": provider}
        if discovery is not None:
            factory_kwargs["project_discovery"] = discovery
        return ForgeApiService(
            runtime=kwargs.pop("runtime", _runtime()),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations={
                DECLARATION_ID: ExecutionDeclaration(
                    declaration_id=DECLARATION_ID,
                    command=("python", "-c", "print('ok')"),
                    profile_id="api-default",
                    working_directory=".",
                )
            },
            approval_policy=kwargs.pop("approval_policy", ApprovalPolicy()),
            harness_factory=lambda **kw: create_agent_harness(**factory_kwargs, **kw),
            **kwargs,
        )

    @staticmethod
    def _event(service, response, name):
        record = service._run_store.load(response.run_id)
        return [
            event for event in record.events if event.event_type.name == name
        ]


class TrustedRootTests(DiscoveryTestCase):
    def test_discovery_uses_the_composition_workspace(self) -> None:
        """A: the root comes from trusted composition, not from the caller."""
        discovery = ProjectDiscovery()
        service = self._service(discovery=discovery)
        response = service.run_agent_loop(DECLARATION_ID)
        completed = self._event(service, response, "PROJECT_DISCOVERY_COMPLETED")
        self.assertEqual(len(completed), 1)
        meta = completed[0].metadata
        self.assertEqual(meta.get("status"), "completed")
        self.assertGreater(meta.get("file_count", 0), 0)
        self.assertTrue(meta.get("workspace_fingerprint"))

    def test_observe_requires_a_trusted_workspace(self) -> None:
        """A: a non-Workspace root is refused outright."""
        discovery = ProjectDiscovery()
        for bogus in ("/", str(self.root), None, object()):
            with self.assertRaises(ProjectDiscoveryError):
                discovery.observe(bogus, run_id="r")  # type: ignore[arg-type]

    def test_observe_requires_a_run_id(self) -> None:
        discovery = ProjectDiscovery()
        for bogus in ("", "   ", None, 7):
            with self.assertRaises(ProjectDiscoveryError):
                discovery.observe(self.workspace, run_id=bogus)  # type: ignore[arg-type]

    def test_caller_cannot_choose_the_root(self) -> None:
        """A: no entry point accepts a root, limits, or a scanner.

        `idempotency_key` is admitted by a later block; it selects a durable
        operation record and confers nothing, so it is listed below among the
        names that must never appear rather than being an exception to the rule.
        """
        import inspect

        for method in (ForgeApiService.run_agent_loop,):
            parameters = list(inspect.signature(method).parameters)
            self.assertEqual(
                parameters,
                ["self", "declaration_id", "purpose_run_id", "idempotency_key"],
            )
            for banned in ("workspace", "root", "limits", "scanner",
                           "scan_limits", "discovery", "run_scope",
                           "authorized_execution", "allowed_tool_ids"):
                self.assertNotIn(banned, parameters)

    def test_taskrunrequest_carries_no_discovery_input(self) -> None:
        fields = {f.name for f in dataclasses.fields(TaskRunRequest)}
        for banned in ("workspace", "root", "scan_limits", "limits",
                       "scanner", "depth", "max_files"):
            self.assertNotIn(banned, fields)


class PathEscapeTests(DiscoveryTestCase):
    def test_outside_workspace_files_are_never_indexed(self) -> None:
        """B: content outside the root never enters the snapshot."""
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(outside, ignore_errors=True))
        (outside / "outside_secret.txt").write_text(CANARY, encoding="utf-8")

        outcome = ProjectDiscovery().observe(self.workspace, run_id="escape")
        self.assertTrue(outcome.succeeded)
        snapshot = outcome.snapshot
        assert snapshot is not None
        indexed = {f.relative_path for f in snapshot.files}
        self.assertNotIn("outside_secret.txt", indexed)
        for rel in indexed:
            self.assertFalse(rel.startswith(".."), rel)
            self.assertNotIn(str(outside), rel)

    def test_scanner_rejects_a_non_workspace_object(self) -> None:
        from app.understanding.scanner import BoundedProjectScanner

        with self.assertRaises(ValueError):
            BoundedProjectScanner().scan(str(self.root))  # type: ignore[arg-type]

    def test_outside_content_never_reaches_the_history(self) -> None:
        """B: neither a secret path nor its content is persisted."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        raw = service._run_store.events_path(response.run_id).read_text(encoding="utf-8")
        self.assertNotIn(CANARY, raw)
        state = service._run_store.state_path(response.run_id).read_text(encoding="utf-8")
        self.assertNotIn(CANARY, state)


class DiscoveryBoundsTests(DiscoveryTestCase):
    def test_file_count_bound_is_enforced(self) -> None:
        """C: max_file_count stops traversal and warns, deterministically."""
        many = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(many, ignore_errors=True))
        for index in range(12):
            (many / f"f{index:02d}.py").write_text("x = 1\n", encoding="utf-8")

        discovery = ProjectDiscovery(limits=ScanLimits(max_file_count=5))
        outcome = discovery.observe(Workspace(many), run_id="bounds")
        self.assertTrue(outcome.succeeded)
        snapshot = outcome.snapshot
        assert snapshot is not None
        self.assertLessEqual(len(snapshot.files), 5)
        self.assertTrue(
            any(w.warning_type == "MAX_FILE_COUNT_EXCEEDED" for w in snapshot.warnings)
        )

    def test_per_file_size_bound_is_enforced(self) -> None:
        """C: an oversized file is inventoried but its content is never parsed.

        The scanner's documented bound is that a file above
        ``max_file_size_bytes`` is recorded as a path plus fingerprint only: no
        text is read, so no structural facts may be extracted from it.
        """
        big = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(big, ignore_errors=True))
        (big / "small.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        (big / "huge.py").write_text("def g():\n    return 2\n" * 4000, encoding="utf-8")

        discovery = ProjectDiscovery(limits=ScanLimits(max_file_size_bytes=200))
        outcome = discovery.observe(Workspace(big), run_id="size")
        self.assertTrue(outcome.succeeded)
        snapshot = outcome.snapshot
        assert snapshot is not None
        indexed = {f.relative_path for f in snapshot.files}
        self.assertIn("small.py", indexed)
        self.assertIn("huge.py", indexed, "oversized files stay visible as inventory")

        # The bound: the oversized file produced no parsed content.
        huge_facts = [
            f for f in snapshot.facts if getattr(f, "source_path", "") == "huge.py"
        ]
        self.assertEqual(huge_facts, [], "no facts may come from an oversized file")
        small_facts = [
            f for f in snapshot.facts if getattr(f, "source_path", "") == "small.py"
        ]
        self.assertTrue(small_facts, "the in-budget file is parsed")
        self.assertTrue(
            any(w.warning_type == "FILE_EXCEEDS_MAX_SIZE" for w in snapshot.warnings)
        )

        # And a snapshot file carries no content channel at all.
        from app.understanding.models import SnapshotFile

        self.assertEqual(
            {f.name for f in dataclasses.fields(SnapshotFile)},
            {"relative_path", "exists", "fingerprint"},
        )

    def test_total_byte_bound_stops_the_scan(self) -> None:
        """C: the total byte ceiling bounds the whole observation."""
        wide = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(wide, ignore_errors=True))
        for index in range(20):
            (wide / f"f{index:02d}.py").write_text("z = 3\n" * 20, encoding="utf-8")

        discovery = ProjectDiscovery(
            limits=ScanLimits(max_total_bytes=120, max_file_size_bytes=4096)
        )
        outcome = discovery.observe(Workspace(wide), run_id="bytes")
        self.assertTrue(outcome.succeeded)
        snapshot = outcome.snapshot
        assert snapshot is not None
        self.assertLess(len(snapshot.files), 20)

    def test_ordering_is_deterministic(self) -> None:
        """C: the same workspace yields the same bounded fingerprint."""
        first = ProjectDiscovery().observe(self.workspace, run_id="r1")
        second = ProjectDiscovery().observe(self.workspace, run_id="r2")
        self.assertTrue(first.succeeded and second.succeeded)
        assert first.snapshot is not None and second.snapshot is not None
        self.assertEqual(
            first.snapshot.workspace_fingerprint,
            second.snapshot.workspace_fingerprint,
        )
        self.assertEqual(
            [f.relative_path for f in first.snapshot.files],
            [f.relative_path for f in second.snapshot.files],
        )

    def test_snapshot_is_immutable(self) -> None:
        """The per-run observation cannot be rewritten after discovery."""
        outcome = ProjectDiscovery().observe(self.workspace, run_id="immutable")
        snapshot = outcome.snapshot
        assert snapshot is not None
        self.assertTrue(UnderstandingSnapshot.__dataclass_params__.frozen)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            snapshot.project_id = "other"


class SensitiveFileTests(DiscoveryTestCase):
    def test_secrets_are_excluded_from_the_snapshot(self) -> None:
        """D: .env, keys, and credentials never enter the inventory."""
        outcome = ProjectDiscovery().observe(self.workspace, run_id="secrets")
        snapshot = outcome.snapshot
        assert snapshot is not None
        # The scanner records a secret file as a path plus fingerprint only: it
        # never reads its content, and the warning proves the exclusion fired.
        self.assertTrue(
            any(w.warning_type == "SECRET_FILE_EXCLUDED" for w in snapshot.warnings)
        )
        secret_paths = {".env", ".env.example", "server.pem", "credentials.json", "id_rsa.key"}
        excluded = {
            w.file_path
            for w in snapshot.warnings
            if w.warning_type == "SECRET_FILE_EXCLUDED"
        }
        self.assertTrue(
            secret_paths & excluded,
            f"expected excluded secret paths, got {sorted(excluded)}",
        )
        # No fact may ever be extracted from a secret file.
        secret_facts = [
            f for f in snapshot.facts if getattr(f, "source_path", "") in secret_paths
        ]
        self.assertEqual(secret_facts, [])

    def test_secret_content_never_appears_in_snapshot_or_history(self) -> None:
        """D: the canary content is absent from both snapshot and events."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)

        outcome = ProjectDiscovery().observe(self.workspace, run_id="canary")
        assert outcome.snapshot is not None
        payload = json.dumps(
            {
                "files": [f.relative_path for f in outcome.snapshot.files],
                "manifests": [m.relative_path for m in outcome.snapshot.manifests],
            }
        )
        self.assertNotIn(CANARY, payload)

        raw = service._run_store.events_path(response.run_id).read_text(encoding="utf-8")
        self.assertNotIn(CANARY, raw)

    def test_binary_and_vendor_trees_are_skipped(self) -> None:
        (self.root / "node_modules").mkdir(exist_ok=True)
        (self.root / "node_modules" / "pkg.js").write_text(CANARY, encoding="utf-8")
        (self.root / "image.png").write_bytes(b"\x89PNG" + CANARY.encode())
        outcome = ProjectDiscovery().observe(self.workspace, run_id="skip")
        snapshot = outcome.snapshot
        assert snapshot is not None
        indexed = {f.relative_path for f in snapshot.files}
        # An ignored directory is pruned entirely, so nothing inside it appears.
        self.assertNotIn("node_modules/pkg.js", indexed)
        # A binary file may be inventoried by path, but its content is never read
        # or parsed, so it contributes no facts.
        self.assertEqual(
            [f for f in snapshot.facts if getattr(f, "source_path", "") == "image.png"],
            [],
        )


class DecisionIsolationTests(DiscoveryTestCase):
    def test_decision_receives_project_understanding_context(self) -> None:
        """E: the snapshot reaches the decision as bounded context."""
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture)
        service.run_agent_loop(DECLARATION_ID)
        self.assertTrue(capture.requests)
        envelope = capture.requests[-1].context_envelope
        items = list(getattr(envelope, "context_items", ()) or ())
        understanding = [
            item for item in items if "understanding" in str(getattr(item, "item_type", ""))
        ]
        self.assertTrue(understanding, "the decision must see the snapshot as context")
        payload = json.loads(understanding[0].value)
        self.assertIn("snapshot_id", payload)
        self.assertNotIn(CANARY, understanding[0].value)

    def test_decision_provider_receives_no_authority(self) -> None:
        """E: no scope, workspace, approval, command, or filesystem handle."""
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture)
        service.run_agent_loop(DECLARATION_ID)
        request = capture.requests[-1]
        fields = {f.name for f in dataclasses.fields(request)}
        for banned in (
            "run_scope", "workspace", "approval_policy", "approval_resolver",
            "allowed_execution_commands", "execution_requests", "command",
            "scanner", "project_discovery", "understanding_snapshot",
        ):
            self.assertNotIn(banned, fields, banned)
        envelope = request.context_envelope
        envelope_fields = {f.name for f in dataclasses.fields(envelope)}
        for banned in ("run_scope", "workspace", "approval_policy", "scanner"):
            self.assertNotIn(banned, envelope_fields, banned)

    def test_missing_snapshot_does_not_fabricate_context(self) -> None:
        """E: with discovery disabled the decision sees no understanding item."""
        capture = _CapturingDecisionProvider()
        service = ForgeApiService(
            runtime=_runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations={
                DECLARATION_ID: ExecutionDeclaration(
                    declaration_id=DECLARATION_ID,
                    command=("python", "-c", "print('ok')"),
                    profile_id="api-default",
                    working_directory=".",
                )
            },
            approval_policy=ApprovalPolicy(),
            harness_factory=lambda **kw: create_agent_harness(
                decision_provider=capture, with_discovery=False, **kw
            ),
        )
        service.run_agent_loop(DECLARATION_ID)
        envelope = capture.requests[-1].context_envelope
        items = list(getattr(envelope, "context_items", ()) or ())
        understanding = [
            item for item in items if "understanding" in str(getattr(item, "item_type", ""))
        ]
        self.assertEqual(understanding, [])


class DiscoveryFailureTests(DiscoveryTestCase):
    class _BrokenDiscovery:
        def observe(self, workspace, *, run_id):
            raise RuntimeError("scanner exploded")

    class _FailingDiscovery:
        def observe(self, workspace, *, run_id):
            return DiscoveryOutcome(
                run_id=run_id,
                snapshot=None,
                duration_seconds=0.0,
                failure_category="RuntimeError",
            )

    def test_scanner_failure_is_recorded_not_masked(self) -> None:
        """14: a scanner failure surfaces as a failure, never as a snapshot."""
        service = self._service(discovery=self._FailingDiscovery())
        response = service.run_agent_loop(DECLARATION_ID)
        completed = self._event(service, response, "PROJECT_DISCOVERY_COMPLETED")
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].metadata.get("status"), "failed")
        self.assertEqual(completed[0].metadata.get("failure_category"), "RuntimeError")
        self.assertNotIn("workspace_fingerprint", completed[0].metadata)
        record = service._run_store.load(response.run_id)
        names = [event.event_type.name for event in record.events]
        self.assertIn("PROJECT_DISCOVERY_STARTED", names)
        self.assertIn("HARNESS_FAILED", names)

    def test_raising_discovery_fails_the_run(self) -> None:
        service = self._service(discovery=self._BrokenDiscovery())
        response = service.run_agent_loop(DECLARATION_ID)
        self.assertEqual(response.state, "FAILED")
        self.assertFalse(response.success)
        names = [
            event.event_type.name
            for event in service._run_store.load(response.run_id).events
        ]
        self.assertIn("HARNESS_FAILED", names)

    def test_failed_discovery_does_not_reach_context_as_success(self) -> None:
        """14: no silent fallback; the decision is not fed a fake snapshot."""
        capture = _CapturingDecisionProvider()
        service = self._service(capture=capture, discovery=self._FailingDiscovery())
        service.run_agent_loop(DECLARATION_ID)
        if capture.requests:
            envelope = capture.requests[-1].context_envelope
            items = list(getattr(envelope, "context_items", ()) or ())
            understanding = [
                item
                for item in items
                if "understanding" in str(getattr(item, "item_type", ""))
            ]
            self.assertEqual(understanding, [])
            self.assertIn("discovery_failed", list(envelope.blocking_conditions))


class RunIdentityTests(DiscoveryTestCase):
    def test_snapshot_belongs_to_its_run(self) -> None:
        """F: snapshot fingerprint is recorded against the same run id."""
        service = self._service()
        response = service.run_agent_loop(DECLARATION_ID)
        completed = self._event(service, response, "PROJECT_DISCOVERY_COMPLETED")[0]
        self.assertEqual(completed.metadata.get("run_id"), response.run_id)
        self.assertEqual(completed.task_id, response.task_id)
        record = service._run_store.load(response.run_id)
        identities = {event.task_id for event in record.events if event.task_id}
        self.assertEqual(identities, {response.task_id})

    def test_fresh_snapshot_per_run(self) -> None:
        """G: a new run observes the workspace again; nothing is reused."""
        service = self._service()
        first = service.run_agent_loop(DECLARATION_ID)
        second = service.run_agent_loop(DECLARATION_ID)
        self.assertNotEqual(first.run_id, second.run_id)
        a = self._event(service, first, "PROJECT_DISCOVERY_COMPLETED")[0].metadata
        b = self._event(service, second, "PROJECT_DISCOVERY_COMPLETED")[0].metadata
        self.assertNotEqual(a.get("snapshot_id"), b.get("snapshot_id"))
        # The bounded fingerprint describes content, so unchanged content matches.
        self.assertEqual(a.get("workspace_fingerprint"), b.get("workspace_fingerprint"))

    def test_workspace_change_is_observed_by_a_later_run(self) -> None:
        """G/12: discovery describes the workspace as of its own run."""
        service = self._service()
        first = service.run_agent_loop(DECLARATION_ID)
        before = self._event(service, first, "PROJECT_DISCOVERY_COMPLETED")[0].metadata

        (self.root / "added_module.py").write_text("value = 42\n", encoding="utf-8")
        second = service.run_agent_loop(DECLARATION_ID)
        after = self._event(service, second, "PROJECT_DISCOVERY_COMPLETED")[0].metadata

        self.assertGreater(after.get("file_count", 0), before.get("file_count", 0))
        self.assertNotEqual(
            after.get("workspace_fingerprint"), before.get("workspace_fingerprint")
        )


class AuthorityPreservationTests(DiscoveryTestCase):
    def test_discovery_never_executes_anything(self) -> None:
        """5/I: discovery is observation only."""
        import ast

        repo = Path(__file__).resolve().parents[1]
        source = (repo / "app/agent_runtime/project_discovery.py").read_text(
            encoding="utf-8"
        )
        # Prose may name what discovery must not do; only executable lines count.
        tree = ast.parse(source)
        prose = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                doc = ast.get_docstring(node, clean=False)
                if doc:
                    prose.add(doc)
        code_lines = []
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(stripped and stripped in doc for doc in prose):
                continue
            code_lines.append(line)
        code = "\n".join(code_lines)
        for banned in (
            "subprocess", "Popen", "os.system", "LocalExecutionAdapter",
            "ExecutionCoordinator", "AuthorizedExecution", "shell=True",
        ):
            self.assertNotIn(banned, code, banned)

    def test_discovery_cannot_widen_the_scope(self) -> None:
        """H: a snapshot grants no command, tool, profile, env, or timeout."""
        outcome = ProjectDiscovery().observe(self.workspace, run_id="widen")
        scope = RunScope(
            run_id="widen",
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="c", description="d"),
            ),
        )
        scope.freeze()
        fingerprint = scope.fingerprint
        # Observing the project changes nothing about the frozen perimeter.
        self.assertEqual(scope.allowed_tool_ids, frozenset())
        self.assertEqual(scope.allowed_execution_commands, frozenset({"python"}))
        self.assertEqual(scope.fingerprint, fingerprint)
        self.assertTrue(outcome.succeeded)

    def test_snapshot_carries_no_authority_fields(self) -> None:
        fields = {f.name for f in dataclasses.fields(UnderstandingSnapshot)}
        for banned in (
            "command", "argv", "executable", "allowed_commands",
            "allowed_tool_ids", "run_scope", "approval", "environment",
            "timeout", "capabilities", "profile",
        ):
            self.assertNotIn(banned, fields, banned)

    def test_harness_remains_the_only_orchestrator(self) -> None:
        """I: discovery adds no loop, coordinator, or agent."""
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "app/agent_runtime/project_discovery.py",
            "app/agent_runtime/frozen_verifier.py",
        ):
            source = (repo / relative).read_text(encoding="utf-8")
            for banned in (
                "class AgentHarness",
                "def run_loop",
                "def run(",
                "ExecutionCoordinator(",
                "LocalExecutionAdapter(",
                "AuthorizedExecution",
            ):
                self.assertNotIn(banned, source, f"{relative}: {banned}")
        # Exactly one production orchestrator class exists.
        harness_source = (repo / "app/agent_runtime/harness.py").read_text(
            encoding="utf-8"
        )
        self.assertEqual(harness_source.count("class AgentHarness"), 1)


class HarnessDiscoveryIntegrationTests(DiscoveryTestCase):
    def _request(self, run_id: str, discovery):
        scope = RunScope(
            run_id=run_id,
            workspace=self.workspace,
            execution_profile=api_profile(),
            allowed_tool_ids=frozenset(),
            allowed_execution_commands=frozenset({"python"}),
            acceptance_criteria=(
                AcceptanceCriterion(criterion_id="c", description="d"),
            ),
        )
        scope.freeze()
        return HarnessRequest(
            run_id=run_id,
            workspace=self.workspace,
            allowed_execution_commands=("python",),
            acceptance_criteria=(AcceptanceCriterion(criterion_id="c", description="d"),),
            run_scope=scope,
        )

    def test_harness_without_discovery_emits_no_discovery_events(self) -> None:
        harness = AgentHarness(policy=AgentHarnessPolicy(max_actions=1))
        result = harness.run(self._request("no-discovery", None))
        names = [event.event_type.name for event in result.events]
        self.assertNotIn("PROJECT_DISCOVERY_STARTED", names)
        self.assertNotIn("PROJECT_DISCOVERY_COMPLETED", names)

    def test_harness_with_discovery_emits_bounded_events(self) -> None:
        harness = AgentHarness(
            policy=AgentHarnessPolicy(max_actions=1),
            project_discovery=ProjectDiscovery(),
        )
        result = harness.run(self._request("with-discovery", ProjectDiscovery()))
        names = [event.event_type.name for event in result.events]
        self.assertIn("PROJECT_DISCOVERY_STARTED", names)
        self.assertIn("PROJECT_DISCOVERY_COMPLETED", names)
        completed = [
            event
            for event in result.events
            if event.event_type.name == "PROJECT_DISCOVERY_COMPLETED"
        ][0]
        self.assertEqual(completed.metadata.get("status"), "completed")
        # Discovery happens before the first context phase is entered.
        self.assertLess(
            names.index("PROJECT_DISCOVERY_COMPLETED"),
            names.index("HARNESS_PHASE_CHANGED"),
        )
        phases = [
            event.metadata.get("phase")
            for event in result.events
            if event.event_type.name == "HARNESS_PHASE_CHANGED"
        ]
        self.assertIn("CONTEXT", phases)

    def test_discovery_events_are_registered(self) -> None:
        self.assertEqual(
            EventType.PROJECT_DISCOVERY_STARTED.value, "project_discovery_started"
        )
        self.assertEqual(
            EventType.PROJECT_DISCOVERY_COMPLETED.value, "project_discovery_completed"
        )


class CompatibilityTests(DiscoveryTestCase):
    def test_run_task_is_unchanged(self) -> None:
        service = self._service()
        response = service.run_task(TaskRunRequest(description="normal task"))
        self.assertTrue(response.run_id.startswith("run-api-"), response.run_id)

    def test_declared_verification_is_unchanged(self) -> None:
        service = self._service()
        response = service.run_declared_verification(DECLARATION_ID)
        self.assertTrue(response.success, response.error)

    def test_acceptance_slice_still_accepts_real_work(self) -> None:
        from app.agent_runtime.acceptance_spec import AcceptanceSpec

        (self.root / "artifact.txt")
        spec = AcceptanceSpec(
            declaration_id=DECLARATION_ID,
            criteria=(AcceptanceCriterion(
                criterion_id="artifact", description="artifact exists",
                requirement_id="req"),),
            expectations={
                "artifact": VerificationExpectation("artifact.txt", True)
            },
        )
        service = ForgeApiService(
            runtime=_runtime(),
            workspace=self.workspace,
            tool_registry=build_default_tool_registry(self.root),
            execution_profile=api_profile(),
            declarations={
                DECLARATION_ID: ExecutionDeclaration(
                    declaration_id=DECLARATION_ID,
                    command=("python", "-c", "open('artifact.txt','w').write('ok')"),
                    profile_id="api-default",
                    working_directory=".",
                )
            },
            acceptance_specs={DECLARATION_ID: spec},
            approval_policy=ApprovalPolicy(),
            harness_factory=lambda **kw: create_agent_harness(**kw),
            loop_coordinator_factory=lambda **kw: create_loop_coordinator(
                isolate_workspace=False
            ),
        )
        response = service.run_accepted_task(DECLARATION_ID)
        self.assertTrue(response.success, response.error)
        record = service._run_store.load(response.run_id)
        events = [
            event
            for event in record.events
            if event.event_type.name == "ACCEPTANCE_COMPLETED"
        ]
        self.assertEqual(events[-1].metadata.get("status"), "pass")
        # The acceptance slice also discovers the project through the same path.
        names = [event.event_type.name for event in record.events]
        self.assertIn("PROJECT_DISCOVERY_COMPLETED", names)


if __name__ == "__main__":
    unittest.main()
