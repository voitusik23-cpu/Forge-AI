"""Offline tests for workspace-scoped ChangeSet and Artifact collection."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from app.artifacts import ArtifactType, FileChangeType
from app.agents.mock_agent import MockAgent
from app.agents.registry import AgentRegistry
from app.orchestrator.models import EventType, Task
from app.orchestrator.orchestrator import Orchestrator
from app.orchestrator.run import RunExecutor
from app.tools.approval import ApprovalPolicy, ApprovalState
from app.tools.contracts import ToolInvocation
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry
from app.tools.workspace import Workspace
from app.tools.write_project_file import WriteProjectFile


class _Approved:
    def resolve(self, request):
        return ApprovalState.APPROVED


class ChangeSetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def execute(self, writes):
        invocations = tuple(
            ToolInvocation(
                WriteProjectFile.TOOL_ID,
                {"relative_path": path, "content": content, "overwrite": True},
                f"write-{index}",
            )
            for index, (path, content) in enumerate(writes, start=1)
        )
        agents = AgentRegistry()
        agents.register(MockAgent(tool_invocations=invocations))
        registry = ToolRegistry()
        registry.register(WriteProjectFile())
        executor = RunExecutor(
            Orchestrator(agents, default_provider="mock"),
            tool_executor=ToolExecutor(
                registry,
                approval_policy=ApprovalPolicy(),
                approval_resolver=_Approved(),
            ),
        )
        return executor.execute(
            Task(id="changeset-test", description="write fixtures"),
            allowed_tool_ids=(WriteProjectFile.TOOL_ID,),
            workspace=self.workspace,
        )

    def test_created_file_has_null_before_and_correct_after_fingerprint(self):
        run = self.execute([("new.txt", "new payload")])
        change = run.change_sets[0].changes[0]
        self.assertEqual(change.change_type, FileChangeType.CREATED)
        self.assertIsNone(change.before_fingerprint)
        self.assertEqual(change.after_fingerprint, hashlib.sha256(b"new payload").hexdigest())

    def test_modified_file_records_before_and_after_fingerprints(self):
        (self.root / "existing.txt").write_text("before", encoding="utf-8")
        run = self.execute([("existing.txt", "after")])
        change = run.change_sets[0].changes[0]
        self.assertEqual(change.change_type, FileChangeType.MODIFIED)
        self.assertEqual(change.before_fingerprint, hashlib.sha256(b"before").hexdigest())
        self.assertEqual(change.after_fingerprint, hashlib.sha256(b"after").hexdigest())
        self.assertNotEqual(change.before_fingerprint, change.after_fingerprint)

    def test_unchanged_target_is_omitted(self):
        (self.root / "same.txt").write_text("same", encoding="utf-8")
        run = self.execute([("same.txt", "same")])
        self.assertEqual(run.change_sets, [])
        self.assertEqual(run.artifacts, [])
        self.assertFalse(any(event.type == EventType.CHANGESET_CREATED for event in run.events))

    def test_multiple_files_share_one_changeset(self):
        run = self.execute([("a.txt", "A"), ("b.txt", "B")])
        self.assertEqual(len(run.change_sets), 1)
        self.assertEqual(len(run.change_sets[0].changes), 2)
        self.assertEqual(
            [item.relative_path for item in run.change_sets[0].changes],
            ["a.txt", "b.txt"],
        )

    def test_changeset_is_linked_to_artifact_and_safe_event(self):
        payload = "secret-like test fixture"
        run = self.execute([("safe.txt", payload)])
        changeset = run.change_sets[0]
        artifact = run.artifacts[0]
        event = next(event for event in run.events if event.type == EventType.CHANGESET_CREATED)
        self.assertEqual(artifact.run_id, run.id)
        self.assertEqual(artifact.changeset_id, changeset.changeset_id)
        self.assertEqual(artifact.artifact_type, ArtifactType.CHANGESET)
        self.assertEqual(event.data["artifact_id"], artifact.artifact_id)
        self.assertEqual(event.data["number_of_changes"], 1)
        self.assertNotIn(payload, repr(changeset))
        self.assertNotIn(payload, repr(event.data))

    def test_workspace_escape_never_creates_change_set(self):
        run = self.execute([("../outside.txt", "outside")])
        self.assertEqual(run.change_sets, [])
        self.assertFalse((self.root.parent / "outside.txt").exists())


if __name__ == "__main__":
    unittest.main()
