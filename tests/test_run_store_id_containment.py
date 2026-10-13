"""A run id must never name a directory outside the run store root.

``RunStore.run_dir`` joins the id straight onto the root, and the id allow-list
``_SAFE_RUN_ID`` accepted ``".."`` -- a name that *is* the parent directory.  The
comment above the pattern claimed it "prevents a crafted id from escaping the
storage root"; it did not.

Measured before this fix: with a record in ``state.json`` one level above the store
root, ``RunStore(root=<root>).load("..")`` returned that outside record, and
``GET /api/runs/%2E%2E`` reached it through the HTTP route, which applies no
allow-list of its own (its sibling route in ``app/api/routes.py`` does).

RED BEFORE THE FIX: tests 01, 02, 03 and 05 fail (test 04 fails on the missing
validator).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.runtime.run_store import RunStore


class RunIdContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory(prefix="forge_store_")
        self.base = Path(self._temp_dir.name).resolve()
        self.root = self.base / "runs"
        self.root.mkdir()
        # A record that lives OUTSIDE the store root, one level up.
        (self.base / "state.json").write_text(
            json.dumps({"run_id": "outside", "status": "OUTSIDE-THE-STORE-ROOT"}),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._temp_dir.cleanup()

    def test_01_a_parent_directory_id_is_refused(self) -> None:
        store = RunStore(root=self.root)
        with self.assertRaises(ValueError):
            store.run_dir("..")

    def test_02_the_loader_cannot_read_outside_the_root(self) -> None:
        store = RunStore(root=self.root)
        try:
            record = store.load("..")
        except ValueError:
            return  # refused outright, which is the point
        self.assertIsNone(
            record, f"the store served a record from outside its root: {record!r}"
        )

    def test_03_a_current_directory_id_is_refused(self) -> None:
        store = RunStore(root=self.root)
        with self.assertRaises(ValueError):
            store.run_dir(".")

    def test_04_the_public_validator_agrees_with_the_pattern(self) -> None:
        from app.runtime.run_store import is_safe_run_id

        self.assertFalse(is_safe_run_id(".."))
        self.assertFalse(is_safe_run_id("."))
        self.assertFalse(is_safe_run_id(""))
        self.assertFalse(is_safe_run_id("a/b"))
        self.assertTrue(is_safe_run_id("mvp-run-A"))
        self.assertTrue(is_safe_run_id("step.graph.s1"))

    def test_05_an_ordinary_id_still_resolves_inside_the_root(self) -> None:
        store = RunStore(root=self.root)
        path = store.run_dir("mvp-run-A")
        self.assertEqual(path.parent, self.root.resolve())


class RunHistoryRouteRefusesTraversalTests(unittest.TestCase):
    def test_06_the_route_answers_a_structured_refusal_not_500(self) -> None:
        """The route must not turn an invalid id into an unhandled exception."""

        from fastapi.testclient import TestClient

        from app.api.server import create_api_app

        client = TestClient(create_api_app())
        response = client.get("/api/runs/%2E%2E")

        self.assertIn(response.status_code, (400, 404), response.text)


if __name__ == "__main__":
    unittest.main()
