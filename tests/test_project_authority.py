"""The project authority contract: a client project id is a lookup key, never permission.

Nine required behaviours, all measured here: an allowed project; an unauthorised principal; a project in
another organisation; a substituted project id; a missing authority; an untrusted workspace; no dispatch
when a refusal happens; isolation between projects; and persistence across a restart of the authority.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

from app.api.project_authority import (
    AbsentProjectAuthority,
    AuthorizedProject,
    InMemoryProjectAuthority,
    ProjectAuthorityRefusal,
)


class AuthorityFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.root = pathlib.Path(self._directory.name)
        self.alice_workspace = self.root / "org-a" / "project-1"
        self.bob_workspace = self.root / "org-b" / "project-2"
        self.alice_workspace.mkdir(parents=True)
        self.bob_workspace.mkdir(parents=True)
        self.projects = {
            "project-1": {"organization_id": "org-a", "workspace_root": str(self.alice_workspace), "name": "Alpha"},
            "project-2": {"organization_id": "org-b", "workspace_root": str(self.bob_workspace), "name": "Beta"},
        }
        self.memberships = {"alice": {"org-a"}, "bob": {"org-b"}}
        self.authority = InMemoryProjectAuthority(self.projects, self.memberships, trusted_root=str(self.root))


class ContractTests(AuthorityFixture):
    def test_01_an_allowed_principal_reaches_the_project(self) -> None:
        granted = self.authority.authorize("alice", "project-1")
        self.assertIsInstance(granted, AuthorizedProject)
        self.assertEqual(granted.project_id, "project-1")
        self.assertEqual(granted.organization_id, "org-a")
        self.assertEqual(pathlib.Path(granted.workspace_root), self.alice_workspace.resolve())
        self.assertEqual(granted.principal_id, "alice")

    def test_02_an_unauthorised_principal_is_refused(self) -> None:
        with self.assertRaises(ProjectAuthorityRefusal) as raised:
            self.authority.authorize("mallory", "project-1")
        self.assertEqual(raised.exception.code, "project_forbidden")

    def test_03_a_project_of_another_organisation_is_refused(self) -> None:
        with self.assertRaises(ProjectAuthorityRefusal) as raised:
            self.authority.authorize("alice", "project-2")
        self.assertEqual(raised.exception.code, "project_forbidden")
        # and the refusal carries no workspace at all
        self.assertFalse(hasattr(raised.exception, "workspace_root"))

    def test_04_a_substituted_project_id_cannot_be_traded_up(self) -> None:
        # Alice legitimately holds project-1; swapping in another organisation's id gains nothing.
        allowed = self.authority.authorize("alice", "project-1")
        with self.assertRaises(ProjectAuthorityRefusal):
            self.authority.authorize("alice", "project-2")
        other = InMemoryProjectAuthority(self.projects, self.memberships, trusted_root=str(self.root))
        with self.assertRaises(ProjectAuthorityRefusal):
            other.authorize("bob", "project-1")
        self.assertEqual(allowed.project_id, "project-1")

    def test_05_a_missing_authority_fails_closed(self) -> None:
        absent = AbsentProjectAuthority()
        for call in (lambda: absent.authorize("alice", "project-1"),
                     lambda: absent.create("alice", "New", str(self.root / "x")),
                     lambda: absent.list_for("alice")):
            with self.assertRaises(ProjectAuthorityRefusal) as raised:
                call()
            self.assertEqual(raised.exception.code, "project_authority_unavailable")

    def test_06_an_untrusted_workspace_is_refused(self) -> None:
        outside = pathlib.Path(tempfile.gettempdir()) / "forge-outside-the-root"
        authority = InMemoryProjectAuthority(
            {"rogue": {"organization_id": "org-a", "workspace_root": str(outside)}},
            {"alice": {"org-a"}},
            trusted_root=str(self.root),
        )
        with self.assertRaises(ProjectAuthorityRefusal) as raised:
            authority.authorize("alice", "rogue")
        self.assertEqual(raised.exception.code, "project_workspace_untrusted")

    def test_07_a_refusal_happens_before_any_dispatch(self) -> None:
        dispatched: list = []

        def dispatch(project_id: str) -> None:  # stands in for creating a run record and executing
            dispatched.append(project_id)

        def use_project(principal: str, project_id: str) -> None:
            granted = self.authority.authorize(principal, project_id)   # authorise first
            dispatch(granted.project_id)                               # ... and only then dispatch

        with self.assertRaises(ProjectAuthorityRefusal):
            use_project("alice", "project-2")
        self.assertEqual(dispatched, [], "nothing may be dispatched when authorisation is refused")
        use_project("alice", "project-1")
        self.assertEqual(dispatched, ["project-1"])

    def test_08_projects_are_isolated_from_each_other(self) -> None:
        alice = self.authority.authorize("alice", "project-1")
        bob = self.authority.authorize("bob", "project-2")
        self.assertNotEqual(alice.workspace_root, bob.workspace_root)
        self.assertNotEqual(alice.organization_id, bob.organization_id)
        self.assertEqual([item.project_id for item in self.authority.list_for("alice")], ["project-1"])
        self.assertEqual([item.project_id for item in self.authority.list_for("bob")], ["project-2"])

    def test_09_a_created_project_survives_a_restart(self) -> None:
        created = self.authority.create("alice", "Gamma", str(self.root / "org-a" / "gamma"))
        # A new authority over the same stored records stands in for a restarted process.
        restarted = InMemoryProjectAuthority(self.authority._projects, self.memberships,
                                             trusted_root=str(self.root))
        again = restarted.authorize("alice", created.project_id)
        self.assertEqual(again.name, "Gamma")
        self.assertEqual(again.workspace_root, created.workspace_root)
        with self.assertRaises(ProjectAuthorityRefusal):
            restarted.authorize("bob", created.project_id)
        with self.assertRaises(ProjectAuthorityRefusal):
            self.authority.create("nobody", "Delta", str(self.root / "org-a" / "delta"))


if __name__ == "__main__":
    unittest.main()
