"""Offline integration tests for the provider-neutral Engineering Run facade."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.agents.registry import AgentRegistry
from app.orchestrator.engineering import (
    EngineeringRunExecutor,
    EngineeringRunRequest,
    EngineeringRunStatus,
)
from app.orchestrator.models import EventType, Task, TaskResult
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.revision import RevisionLoopExecutor
from app.orchestrator.run import RunExecutor
from app.tools.acceptance import AcceptanceCriterion
from app.tools.approval import ApprovalPolicy, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.verification import VerificationExpectation, VerificationStatus
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class _SequenceAgent:
    name = "fixture"
    provider_name = "fixture"

    def __init__(self, contents, *, path="result.txt"):
        self.contents = list(contents)
        self.path = path
        self.calls = 0

    def run(self, task):
        if task.context.get("forge_tool_results"):
            return TaskResult(task.id, True, output="tool round complete")
        revision = None
        for item in task.context.get("forge_execution_context", {}).get("items", []):
            if item.get("kind") == "task_context":
                revision = json.loads(item["content"]).get("forge_revision")
                break
        index = revision["attempt_number"] if revision else 0
        content = self.contents[min(index, len(self.contents) - 1)]
        self.calls += 1
        return TaskResult(
            task.id,
            True,
            output="fixture proposal",
            tool_invocations=[ToolInvocation(
                WriteProjectFile.TOOL_ID,
                {"relative_path": self.path, "content": content, "overwrite": True},
                f"fixture-write-{self.calls}",
            )],
        )


class _Resolver:
    def __init__(self, decision=ApprovalState.APPROVED):
        self.decision = decision
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return self.decision


class EngineeringRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)
        self.good = "accepted fixture"
        self.bad = "rejected fixture"
        self.criterion = AcceptanceCriterion("file-content", "File has expected digest")
        self.expectations = {
            "file-content": VerificationExpectation(
                "result.txt", True, hashlib.sha256(self.good.encode()).hexdigest()
            )
        }

    def tearDown(self):
        self.temp.cleanup()

    def make_executor(self, contents, *, approval=ApprovalState.APPROVED, path="result.txt"):
        agent = _SequenceAgent(contents, path=path)
        agents = AgentRegistry()
        agents.register(agent)
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        resolver = _Resolver(approval)
        run_executor = RunExecutor(
            Orchestrator(agents, default_provider="fixture"),
            tool_executor=ToolExecutor(
                registry, approval_policy=ApprovalPolicy(), approval_resolver=resolver
            ),
        )
        return EngineeringRunExecutor(RevisionLoopExecutor(run_executor)), agent, resolver

    def request(self, *, max_attempts=1, allowed=(WriteProjectFile.TOOL_ID,)):
        return EngineeringRunRequest(
            task=Task(id="engineering-test", description="write expected file"),
            workspace=self.workspace,
            snapshot_paths=("result.txt",),
            verification_expectations=self.expectations,
            acceptance_criteria=(self.criterion,),
            max_revision_attempts=max_attempts,
            allowed_tool_ids=allowed,
        )

    def test_success_collects_complete_attempt_and_lifecycle_history(self):
        executor, _, _ = self.make_executor([self.good])
        result = executor.execute(self.request())

        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual(result.final_acceptance.status.value, "pass")
        self.assertEqual(len(result.attempts), 1)
        self.assertEqual(result.attempts[0].attempt_number, 0)
        self.assertEqual(len(result.snapshots), 2)
        self.assertEqual(len(result.changesets), 1)
        self.assertEqual(result.verification_results[0].status, VerificationStatus.PASS)
        event_types = [event.type for event in result.run.events]
        ordered = [
            EventType.ENGINEERING_RUN_STARTED,
            EventType.SNAPSHOT_CREATED,
            EventType.TOOL_EXECUTION_COMPLETED,
            EventType.CHANGESET_CREATED,
            EventType.SNAPSHOT_CREATED,
            EventType.VERIFICATION_COMPLETED,
            EventType.ACCEPTANCE_COMPLETED,
            EventType.ENGINEERING_RUN_COMPLETED,
        ]
        cursor = -1
        for event_type in ordered:
            cursor = event_types.index(event_type, cursor + 1)

    def test_revision_keeps_per_attempt_and_aggregate_histories(self):
        executor, _, _ = self.make_executor([self.bad, self.good])
        result = executor.execute(self.request())

        self.assertEqual(result.final_status, EngineeringRunStatus.SUCCESS)
        self.assertEqual([attempt.attempt_number for attempt in result.attempts], [0, 1])
        self.assertEqual([len(attempt.snapshots) for attempt in result.attempts], [2, 2])
        self.assertEqual([attempt.acceptance_result.status.value for attempt in result.attempts], ["fail", "pass"])
        self.assertEqual([attempt.verification_results[0].status for attempt in result.attempts], [
            VerificationStatus.FAIL, VerificationStatus.PASS
        ])
        self.assertEqual([changeset.attempt_number for changeset in result.changesets], [0, 1])
        self.assertEqual(len(result.acceptance_results), 2)
        self.assertEqual(len(result.snapshots), 4)

    def test_limit_status_is_reported_after_configured_revision(self):
        executor, agent, _ = self.make_executor([self.bad])
        result = executor.execute(self.request(max_attempts=1))

        self.assertEqual(result.final_status, EngineeringRunStatus.LIMIT_REACHED)
        self.assertEqual([attempt.attempt_number for attempt in result.attempts], [0, 1])
        self.assertEqual(agent.calls, 2)
        self.assertEqual(sum(event.type == EventType.REVISION_STARTED for event in result.run.events), 1)

    def test_pending_approval_has_terminal_waiting_status_without_mutation(self):
        executor, _, resolver = self.make_executor([self.good], approval=None)
        result = executor.execute(self.request())

        self.assertEqual(result.final_status, EngineeringRunStatus.WAITING_FOR_APPROVAL)
        self.assertEqual(len(resolver.requests), 1)
        self.assertFalse((self.root / "result.txt").exists())
        self.assertEqual(result.changesets, ())
        self.assertEqual(result.verification_results, ())

    def test_permission_denial_and_workspace_traversal_fail_safely(self):
        executor, _, _ = self.make_executor([self.good])
        denied = executor.execute(self.request(allowed=()))
        self.assertEqual(denied.final_status, EngineeringRunStatus.FAILED)
        self.assertEqual(denied.changesets, ())

        traversal_executor, _, _ = self.make_executor([self.good], path="../outside.txt")
        traversal = traversal_executor.execute(self.request())
        self.assertEqual(traversal.final_status, EngineeringRunStatus.FAILED)
        self.assertFalse((self.root.parent / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
