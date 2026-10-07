"""End-to-End Product Execution Validation for Forge AI (Stage 4).

Validates the full execution pipeline:
User Task -> Planner -> Capability Requirements -> Capability Fabric ->
Dispatcher -> Execution Boundary & RunScope -> Tool Execution -> Artifact ->
Project State (Understanding Snapshot) -> Usage / Run Accounting / Trace.
"""

import tempfile
import unittest
from pathlib import Path
from typing import Optional

from app.agents.base import AgentExecutionError
from app.agents.providers.model_registry import ModelRegistry
from app.agents.providers.models import (
    CostTier,
    ModelCapability,
    ProviderInfo,
    ProviderModelInfo,
    ProviderProtocol,
)
from app.agents.registry import AgentRegistry
from app.artifacts import Artifact, ArtifactType, ChangeSet, FileChangeType
from app.context.assembler import ContextAssembler
from app.fabric import (
    CapabilityFabric,
    CapabilityRequirement,
    FabricRequest,
    FabricResolution,
    RunAccountingRecord,
    UsageAccountingAdapter,
)
from app.orchestrator.dispatcher import DispatchPolicy, Dispatcher
from app.orchestrator.models import (
    EventType,
    Run,
    RunState,
    Task,
    TaskCategory,
    TaskResult,
)
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.planning.planner import Planner
from app.tools.approval import ApprovalRequest, ApprovalResolution, ApprovalState
from app.tools.contracts import ToolDefinition, ToolInvocation, ToolResult, ToolStatus
from app.tools.executor import ToolExecutor
from app.tools.read_project_file import ReadProjectFile
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile
from app.understanding.snapshotter import UnderstandingSnapshotter
from app.usage import Usage


class CodeAuthoringAgent:
    """Mock agent simulating code and test authoring by invoking WriteProjectFile."""

    def __init__(
        self,
        name: str,
        provider_name: str,
        model_name: str = "mock-code-v1",
        usage: Optional[Usage] = None,
    ) -> None:
        self.name = name
        self.provider_name = provider_name
        self.model_name = model_name
        self.usage = usage or Usage(
            input_tokens=1200,
            output_tokens=800,
            cached_tokens=250,
            estimated_cost=0.004,
        )
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1

        if task.context and "forge_tool_results" in task.context:
            tool_results = task.context["forge_tool_results"]
            all_ok = all(r.get("status") == "completed" for r in tool_results)
            return TaskResult(
                task_id=task.id,
                success=all_ok,
                output="Files created successfully." if all_ok else "Tool execution failed.",
                provider=self.provider_name,
                agent=self.name,
                model_name=self.model_name,
                usage=self.usage,
            )

        # Return tool invocations to write hello.py and test_hello.py
        hello_code = (
            "def add(a: int, b: int) -> int:\n"
            "    \"\"\"Return sum of two numbers.\"\"\"\n"
            "    return a + b\n"
        )
        test_code = (
            "from src.hello import add\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n"
        )

        invocations = [
            ToolInvocation(
                tool_id=WriteProjectFile.TOOL_ID,
                input={
                    "relative_path": "src/hello.py",
                    "content": hello_code,
                    "overwrite": True,
                },
            ),
            ToolInvocation(
                tool_id=WriteProjectFile.TOOL_ID,
                input={
                    "relative_path": "tests/test_hello.py",
                    "content": test_code,
                    "overwrite": True,
                },
            ),
        ]

        return TaskResult(
            task_id=task.id,
            success=True,
            output="Generated src/hello.py and tests/test_hello.py",
            provider=self.provider_name,
            agent=self.name,
            model_name=self.model_name,
            usage=self.usage,
            tool_invocations=invocations,
        )


class FailingAgent:
    """Mock agent that fails on execution to trigger fallback."""

    def __init__(self, name: str, provider_name: str, model_name: str = "failing-model") -> None:
        self.name = name
        self.provider_name = provider_name
        self.model_name = model_name
        self.calls = 0

    def run(self, task: Task) -> TaskResult:
        self.calls += 1
        raise AgentExecutionError(f"Provider '{self.provider_name}' encountered rate limit error 429")


class AutoApprovingResolver:
    """Approval resolver that approves authorized tool requests for end-to-end testing."""

    def resolve(self, request: ApprovalRequest) -> ApprovalResolution:
        return ApprovalResolution(
            decision=ApprovalState.APPROVED,
            approved_fingerprint=request.intent_fingerprint or "",
            approval_id=f"appr-{request.invocation_id}",
        )


class EndToEndForgeExecutionTests(unittest.TestCase):
    """Stage 4 End-to-End Validation Suite."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace_root = Path(self.temp_dir.name)
        self.workspace = Workspace(self.workspace_root)

        # 1. Model Registry
        self.model_registry = ModelRegistry()
        self.model_registry.register_provider(
            ProviderInfo(
                provider_id="mock",
                display_name="Mock Provider",
                protocol=ProviderProtocol.MOCK,
            )
        )
        self.model_registry.register_provider(
            ProviderInfo(
                provider_id="deepseek",
                display_name="DeepSeek",
                protocol=ProviderProtocol.OPENAI_CHAT,
            )
        )
        self.model_registry.register_model(
            ProviderModelInfo(
                provider_id="mock",
                model_id="mock-code-v1",
                context_window=64_000,
                capabilities=frozenset([ModelCapability.CODE, ModelCapability.FAST]),
                cost_tier=CostTier.FREE,
                input_cost_per_1m=0.0,
                output_cost_per_1m=0.0,
            )
        )
        self.model_registry.register_model(
            ProviderModelInfo(
                provider_id="deepseek",
                model_id="deepseek-coder",
                context_window=64_000,
                capabilities=frozenset([ModelCapability.CODE, ModelCapability.REASONING]),
                cost_tier=CostTier.CHEAP,
                input_cost_per_1m=0.14,
                output_cost_per_1m=0.28,
            )
        )

        # 2. Tool Registry
        self.tool_registry = ToolRegistry()
        self.write_tool = WriteProjectFile()
        self.read_tool = ReadProjectFile(self.workspace_root, ["src/hello.py", "tests/test_hello.py"])
        self.tool_registry.register(self.write_tool)
        self.tool_registry.register(self.read_tool)

        # 3. Capability Fabric
        self.fabric = CapabilityFabric(
            model_registry=self.model_registry,
            tool_registry=self.tool_registry,
            workspace=self.workspace,
        )

        # 4. Agent Registry
        self.agent_registry = AgentRegistry()
        self.coder_agent = CodeAuthoringAgent(
            name="mock",
            provider_name="mock",
            model_name="mock-code-v1",
            usage=Usage(input_tokens=1200, output_tokens=800, cached_tokens=250, estimated_cost=0.004),
        )
        self.agent_registry.register(self.coder_agent)

        # 5. Orchestrator & Tool Executor
        self.orchestrator = Orchestrator(
            registry=self.agent_registry,
            default_provider="mock",
            fallback_chain=("mock",),
            model_registry=self.model_registry,
            fabric=self.fabric,
            allow_paid_providers=True,
        )
        self.approval_resolver = AutoApprovingResolver()
        self.tool_executor = ToolExecutor(
            self.tool_registry,
            approval_resolver=self.approval_resolver,
        )
        self.context_assembler = ContextAssembler()
        self.run_executor = RunExecutor(
            orchestrator=self.orchestrator,
            context_assembler=self.context_assembler,
            tool_executor=self.tool_executor,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_e2e_01_successful_real_task_execution(self) -> None:
        """E2E-1: Full Pipeline from User Goal -> Plan -> Task -> Fabric -> Execution -> Artifact -> State -> Accounting."""
        # 1. User Goal & Planner
        user_goal = "Create a small Python library with arithmetic functions and tests"
        planner = Planner()
        plan = planner.create_plan(user_goal)
        self.assertTrue(len(plan.tasks) > 0)
        impl_planned = next(t for t in plan.tasks if t.category == TaskCategory.CODE)
        self.assertEqual(impl_planned.title, "Implementation")

        # 2. Formulate Orchestrator Task with Capability Requirements
        run_id = "run-e2e-stage4-001"
        task = Task(
            id=f"{plan.plan_id}-{impl_planned.task_id}",
            description=impl_planned.description,
            category=impl_planned.category,
            context={"run_id": run_id, "plan_id": plan.plan_id, "project_id": "forge-sample"},
            parameters={
                "required_capabilities": [
                    CapabilityRequirement.model(ModelCapability.CODE),
                    CapabilityRequirement.tool(WriteProjectFile.TOOL_ID),
                ],
            },
        )

        # 3. Capability Fabric Resolution
        fabric_req = FabricRequest(
            subject_agent="orchestrator",
            intent_description=task.description,
            required_capabilities=(
                CapabilityRequirement.model(ModelCapability.CODE),
                CapabilityRequirement.tool(WriteProjectFile.TOOL_ID),
            ),
            allow_paid_providers=True,
        )
        resolution = self.fabric.resolve(fabric_req)
        self.assertTrue(resolution.can_proceed)
        self.assertIn("mock", resolution.candidate_providers)
        self.assertIn(WriteProjectFile.TOOL_ID, resolution.selected_tools)

        # 4. Execute through RunExecutor
        run = self.run_executor.execute(
            task,
            run_id=run_id,
            workspace=self.workspace,
            allowed_tool_ids=[WriteProjectFile.TOOL_ID, ReadProjectFile.TOOL_ID],
        )

        # 5. Verify Run Lifecycle
        self.assertEqual(run.id, run_id)
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertIsNotNone(run.result)
        self.assertTrue(run.result.success)
        self.assertEqual(run.result.provider, "mock")
        self.assertEqual(run.result.model_name, "mock-code-v1")

        # 6. Verify Real Disk Artifacts in Workspace
        hello_file = self.workspace_root / "src" / "hello.py"
        test_file = self.workspace_root / "tests" / "test_hello.py"
        self.assertTrue(hello_file.exists())
        self.assertTrue(test_file.exists())
        self.assertIn("def add", hello_file.read_text(encoding="utf-8"))
        self.assertIn("test_add", test_file.read_text(encoding="utf-8"))

        # 7. Verify ChangeSets and Artifacts Attached to Run
        self.assertTrue(len(run.change_sets) > 0)
        self.assertTrue(len(run.artifacts) > 0)
        cs = run.change_sets[0]
        self.assertEqual(cs.run_id, run_id)
        changed_paths = {c.relative_path for c in cs.changes}
        self.assertEqual(changed_paths, {"src/hello.py", "tests/test_hello.py"})
        for c in cs.changes:
            self.assertEqual(c.change_type, FileChangeType.CREATED)
            self.assertIsNotNone(c.after_fingerprint)

        # 8. Verify Project State Update (Understanding Snapshot)
        snapshotter = UnderstandingSnapshotter()
        snapshot = snapshotter.create_snapshot(self.workspace, project_id="forge-sample")
        indexed_files = {f.relative_path for f in snapshot.files}
        self.assertIn("src/hello.py", indexed_files)
        self.assertIn("tests/test_hello.py", indexed_files)
        # Check AST symbol extraction
        from app.understanding.models import StructuralFactType
        symbol_facts = [f for f in snapshot.facts if f.fact_type == StructuralFactType.SYMBOL_DECLARED]
        self.assertTrue(any(f.details.get("symbol_name") == "add" for f in symbol_facts))

        # 9. Verify Run Accounting (2 rounds: tool invocation + followup completion)
        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            task_id=task.id,
            project_id="forge-sample",
            associated_artifact_ids=tuple(a.artifact_id for a in run.artifacts),
            total_duration_seconds=1.2,
        )
        self.assertEqual(accounting.run_id, run_id)
        self.assertEqual(accounting.task_id, task.id)
        self.assertEqual(accounting.project_id, "forge-sample")
        self.assertEqual(accounting.total_attempts, 2)
        self.assertEqual(accounting.total_input_tokens, 2400)
        self.assertEqual(accounting.total_output_tokens, 1600)
        self.assertEqual(accounting.total_cached_tokens, 500)
        self.assertEqual(accounting.total_tokens, 4000)
        self.assertEqual(accounting.total_cost, 0.008)
        self.assertIsNotNone(accounting.successful_attempt)

        # 10. Verify RunTrace Events
        event_types = [e.type for e in run.events]
        self.assertIn(EventType.RUN_STARTED, event_types)
        self.assertIn(EventType.CONTEXT_ASSEMBLED, event_types)
        self.assertIn(EventType.PROVIDER_SELECTED, event_types)
        self.assertIn(EventType.PROVIDER_ATTEMPT, event_types)
        self.assertIn(EventType.TOOL_INVOCATION_REQUESTED, event_types)
        self.assertIn(EventType.TOOL_EXECUTION_COMPLETED, event_types)
        self.assertIn(EventType.PROVIDER_RESULT, event_types)
        self.assertIn(EventType.RUN_COMPLETED, event_types)

    def test_e2e_02_fallback_execution_scenario(self) -> None:
        """E2E-2: Provider A fails -> Provider B succeeds under single run_id."""
        failing_deepseek = FailingAgent(
            name="deepseek",
            provider_name="deepseek",
            model_name="deepseek-coder",
        )
        self.agent_registry.register(failing_deepseek)

        # Orchestrator configured with fallback chain: deepseek -> mock
        dispatcher = Dispatcher(
            agent_registry=self.agent_registry,
            default_provider="mock",
            fallback_chain=("deepseek", "mock"),
            model_registry=self.model_registry,
            fabric=self.fabric,
            allow_paid_providers=True,
        )
        orchestrator = Orchestrator(
            registry=self.agent_registry,
            default_provider="mock",
            fallback_chain=("deepseek", "mock"),
            model_registry=self.model_registry,
            fabric=self.fabric,
            allow_paid_providers=True,
        )
        orchestrator._dispatcher = dispatcher

        run_executor = RunExecutor(
            orchestrator=orchestrator,
            context_assembler=self.context_assembler,
            tool_executor=self.tool_executor,
        )

        run_id = "run-e2e-fallback-002"
        task = Task(
            id="task-fallback-1",
            description="Author hello module with fallback",
            category=TaskCategory.CODE,
            context={"run_id": run_id},
            parameters={"model": "deepseek:deepseek-coder"},
        )

        run = run_executor.execute(
            task,
            run_id=run_id,
            workspace=self.workspace,
            allowed_tool_ids=[WriteProjectFile.TOOL_ID],
        )

        self.assertEqual(run.id, run_id)
        self.assertEqual(run.state, RunState.COMPLETED)
        self.assertTrue(run.result.success)
        # Succeeded on fallback (mock)
        self.assertEqual(run.result.provider, "mock")

        # Check Accounting has 4 attempts (2 attempts in round 0 + 2 attempts in round 1 followup)
        accounting = self.fabric.get_run_accounting(run_id=run_id, task_id=task.id)
        self.assertEqual(accounting.total_attempts, 4)
        # Attempt 1: deepseek (failed)
        self.assertEqual(accounting.attempt_records[0].provider_name, "deepseek")
        self.assertFalse(accounting.attempt_records[0].success)
        self.assertFalse(accounting.attempt_records[0].is_fallback)
        # Attempt 2: mock (succeeded)
        self.assertEqual(accounting.attempt_records[1].provider_name, "mock")
        self.assertTrue(accounting.attempt_records[1].success)
        self.assertTrue(accounting.attempt_records[1].is_fallback)

        # Trace captured FALLBACK events
        fallback_events = [e for e in run.events if e.type == EventType.FALLBACK]
        self.assertEqual(len(fallback_events), 2)
        self.assertEqual(fallback_events[0].data.get("source"), "deepseek")
        self.assertEqual(fallback_events[0].data.get("target"), "mock")

        # Artifact created once by successful attempt
        hello_file = self.workspace_root / "src" / "hello.py"
        self.assertTrue(hello_file.exists())

    def test_e2e_03_artifact_result_and_project_state_linkage(self) -> None:
        """E2E-3: Verify explicit chain run -> task -> result -> artifact -> project state."""
        run_id = "run-e2e-linkage-003"
        task = Task(
            id="task-linkage-1",
            description="Create code files",
            category=TaskCategory.CODE,
            context={"run_id": run_id},
        )
        run = self.run_executor.execute(
            task,
            run_id=run_id,
            workspace=self.workspace,
            allowed_tool_ids=[WriteProjectFile.TOOL_ID],
        )
        self.assertEqual(run.state, RunState.COMPLETED)

        # Artifact link to run_id
        artifact = run.artifacts[0]
        self.assertEqual(artifact.run_id, run_id)
        self.assertEqual(artifact.artifact_type, ArtifactType.CHANGESET)

        # ChangeSet link to run_id
        cs = run.change_sets[0]
        self.assertEqual(cs.run_id, run_id)
        self.assertEqual(cs.changeset_id, artifact.changeset_id)

        # Project state snapshot
        snapshotter = UnderstandingSnapshotter()
        snapshot = snapshotter.create_snapshot(self.workspace)
        hello_snap = next(f for f in snapshot.files if f.relative_path == "src/hello.py")
        hello_change = next(c for c in cs.changes if c.relative_path == "src/hello.py")
        # Snapshot fingerprint matches ChangeSet after_fingerprint
        self.assertEqual(hello_snap.fingerprint, hello_change.after_fingerprint)

    def test_e2e_04_run_accounting_detailed_record(self) -> None:
        """E2E-4: Verify all fields in RunAccountingRecord."""
        run_id = "run-e2e-accounting-004"
        task = Task(
            id="task-accounting-1",
            description="Accounting test",
            category=TaskCategory.CODE,
            context={"run_id": run_id},
        )
        run = self.run_executor.execute(
            task,
            run_id=run_id,
            workspace=self.workspace,
            allowed_tool_ids=[WriteProjectFile.TOOL_ID],
        )

        accounting = self.fabric.get_run_accounting(
            run_id=run_id,
            task_id=task.id,
            project_id="forge-accounting",
            associated_artifact_ids=tuple(a.artifact_id for a in run.artifacts),
            total_duration_seconds=2.3,
        )

        self.assertEqual(accounting.run_id, run_id)
        self.assertEqual(accounting.task_id, task.id)
        self.assertEqual(accounting.project_id, "forge-accounting")
        self.assertEqual(accounting.total_attempts, 2)
        self.assertEqual(accounting.total_requests, 2)
        self.assertEqual(accounting.total_input_tokens, 2400)
        self.assertEqual(accounting.total_output_tokens, 1600)
        self.assertEqual(accounting.total_cached_tokens, 500)
        self.assertEqual(accounting.total_tokens, 4000)
        self.assertEqual(accounting.total_cost, 0.008)
        self.assertEqual(accounting.total_duration_seconds, 2.3)
        self.assertEqual(len(accounting.associated_artifact_ids), len(run.artifacts))

        # Check dictionary serialization
        acc_dict = accounting.to_dict()
        self.assertEqual(acc_dict["run_id"], run_id)
        self.assertEqual(acc_dict["total_tokens"], 4000)
        self.assertEqual(acc_dict["total_cost"], 0.008)

    def test_e2e_05_security_boundary_and_authority_ceiling(self) -> None:
        """E2E-5: Verify security boundaries (unauthorized tools denied, path traversal blocked)."""
        run_id = "run-e2e-security-005"
        task = Task(
            id="task-security-1",
            description="Security boundary test",
            category=TaskCategory.CODE,
            context={"run_id": run_id},
        )

        # 1. Disallow WriteProjectFile in allowed_tool_ids
        run = self.run_executor.execute(
            task,
            run_id=run_id,
            workspace=self.workspace,
            allowed_tool_ids=[ReadProjectFile.TOOL_ID],  # Write NOT allowed
        )

        # Tool execution was denied by ToolExecutor permission context
        denied_results = [
            tr for tr in run.result.tool_results if tr.status == ToolStatus.DENIED
        ]
        self.assertTrue(len(denied_results) > 0)
        # Verify no files were created in workspace
        self.assertFalse((self.workspace_root / "src" / "hello.py").exists())

        # 2. Verify Path Traversal is blocked by Workspace
        with self.assertRaises(Exception):
            self.workspace.resolve_target("../../etc/passwd")


if __name__ == "__main__":
    unittest.main()
