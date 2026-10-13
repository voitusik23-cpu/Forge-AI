"""The harness's tool perimeter must refuse an intent the run does not authorize.

The check that enforces this -- "declared tool intents may only narrow the frozen
tool perimeter" -- had **no test coverage at all**: the string it raises on appears
in no test file in this repository. A security check nobody exercises is a claim, not
a guarantee, so this pins it. The surrounding change also removed a neighbouring
no-op call (``validate_tool_set(getattr(request, "allowed_tool_ids", None))``, which
always received ``None``); these tests show the real barrier is what refuses a
widening, not a request field that does not exist.

INJECTION PROOF: commenting out the ``raise`` in ``_enforce_run_scope`` makes tests
01 and 03 red, which is how this file is shown to be able to fail.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.agent_runtime.harness import AgentHarness
from app.runtime.run_scope import RunScope, RunScopeError
from app.tools.workspace import Workspace
from run_scope_support import harness_request, make_scope


class _Intent:
    """A declared tool intent, reduced to the identity the perimeter compares."""

    def __init__(self, tool_id: str) -> None:
        self.tool_id = tool_id


class HarnessToolPerimeterTests(unittest.TestCase):
    def setUp(self) -> None:
        RunScope.release_all()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_perimeter_")
        self.workspace = Workspace(Path(self._temp_dir.name).resolve())
        self.run_id = f"perimeter-run-{self._testMethodName}"
        self.scope = make_scope(
            self.run_id,
            commands=("python",),
            workspace=self.workspace,
            tools=("allowed_tool",),
        )

    def tearDown(self) -> None:
        RunScope.release_all()
        self._temp_dir.cleanup()

    def _enforce(self, *tool_ids: str) -> None:
        request = harness_request(
            self.scope, tool_requests=tuple(_Intent(tool_id) for tool_id in tool_ids)
        )
        return AgentHarness()._enforce_run_scope(request)

    def test_01_an_intent_outside_the_frozen_perimeter_is_refused(self) -> None:
        with self.assertRaises(RunScopeError) as caught:
            self._enforce("forbidden_tool")

        self.assertIn("exceed the frozen run tool set", str(caught.exception))

    def test_02_an_intent_inside_the_perimeter_is_accepted(self) -> None:
        self.assertIsNone(self._enforce("allowed_tool"))

    def test_03_a_second_undeclared_intent_is_refused_even_beside_an_allowed_one(self) -> None:
        with self.assertRaises(RunScopeError):
            self._enforce("allowed_tool", "another_tool")

    def test_04_a_request_carrying_no_intents_is_not_refused(self) -> None:
        self.assertIsNone(self._enforce())


if __name__ == "__main__":
    unittest.main()
