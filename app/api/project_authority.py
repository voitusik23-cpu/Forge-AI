"""The Core<->Platform project authority: a client-supplied project id is never permission.

The Core owns execution and nothing else. Projects, membership and organisations belong to the Platform,
so the Core does **not** gain a project store: it asks an injected authority, and it uses only what the
authority returns.

Guarantees this module enforces, checked by `tests/test_project_authority.py`:

* a request's ``project_id`` is a **lookup key**, never a path and never evidence of authority;
* ``workspace_root`` is taken only from :class:`AuthorizedProject`, and it must resolve inside the trusted
  root the Core was configured with;
* refusals are typed and happen **before** any run record or command exists;
* the default is fail-closed: without an authority, every project-scoped operation is refused.

No Platform object, credential or DTO ever crosses this boundary.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Mapping, Protocol, runtime_checkable


class ProjectAuthorityRefusal(Exception):
    """A typed refusal. ``code`` is stable so callers and tests can assert on it."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> Mapping[str, object]:
        return {"refused": True, "code": self.code, "error": self.message}


@dataclass(frozen=True)
class AuthorizedProject:
    """What the authority decided. The Core derives its workspace from here and nowhere else."""

    project_id: str
    organization_id: str
    workspace_root: str
    principal_id: str
    name: str = ""


@runtime_checkable
class ProjectAuthority(Protocol):
    """The single seam between the execution core and the project system of record."""

    def authorize(self, principal: str, project_id: str) -> AuthorizedProject:
        """Return the project this principal may use, or raise :class:`ProjectAuthorityRefusal`."""

    def create(self, principal: str, name: str, workspace_root: str) -> AuthorizedProject:
        """Persist a project for this principal, or raise :class:`ProjectAuthorityRefusal`."""

    def list_for(self, principal: str) -> list:
        """Projects this principal may see."""


class AbsentProjectAuthority:
    """Fail-closed default: no authority composed means no project-scoped operation is permitted."""

    code = "project_authority_unavailable"

    def _refuse(self, action: str) -> None:
        raise ProjectAuthorityRefusal(
            self.code,
            f"no project authority is composed; {action} is refused",
        )

    def authorize(self, principal: str, project_id: str) -> AuthorizedProject:
        self._refuse(f"using project {project_id!r}")

    def create(self, principal: str, name: str, workspace_root: str) -> AuthorizedProject:
        self._refuse("creating a project")

    def list_for(self, principal: str) -> list:
        self._refuse("listing projects")


class InMemoryProjectAuthority:
    """A deterministic authority for tests and offline composition: real checks, no Platform.

    It is deliberately explicit about membership: a principal only reaches projects of an organisation it
    belongs to, and a project id from another organisation is refused exactly like an unknown one.
    """

    def __init__(self, projects: dict | None = None, memberships: dict | None = None,
                 trusted_root: str | None = None) -> None:
        self._projects: dict[str, dict] = dict(projects or {})
        self._memberships: dict[str, set] = {key: set(value) for key, value in (memberships or {}).items()}
        self._trusted_root = pathlib.Path(trusted_root).resolve() if trusted_root else None

    # -- helpers ---------------------------------------------------------
    def _workspace_within_trust(self, workspace_root: str) -> str:
        resolved = pathlib.Path(workspace_root).resolve()
        if self._trusted_root is not None:
            try:
                resolved.relative_to(self._trusted_root)
            except ValueError:
                raise ProjectAuthorityRefusal(
                    "project_workspace_untrusted",
                    f"workspace {resolved} is outside the trusted root {self._trusted_root}",
                ) from None
        return str(resolved)

    def _record(self, project_id: str) -> dict:
        record = self._projects.get(project_id)
        if record is None:
            raise ProjectAuthorityRefusal("project_unknown", f"no project {project_id!r}")
        return record

    # -- the protocol ----------------------------------------------------
    def authorize(self, principal: str, project_id: str) -> AuthorizedProject:
        record = self._record(project_id)
        if record["organization_id"] not in self._memberships.get(principal, set()):
            raise ProjectAuthorityRefusal(
                "project_forbidden",
                f"{principal!r} is not a member of the organisation owning {project_id!r}",
            )
        return AuthorizedProject(
            project_id=project_id,
            organization_id=record["organization_id"],
            workspace_root=self._workspace_within_trust(record["workspace_root"]),
            principal_id=principal,
            name=record.get("name", ""),
        )

    def create(self, principal: str, name: str, workspace_root: str) -> AuthorizedProject:
        clean = (name or "").strip()
        if not clean:
            raise ProjectAuthorityRefusal("project_name_required", "a project name is required")
        organization = next(iter(self._memberships.get(principal, set())), None)
        if organization is None:
            raise ProjectAuthorityRefusal(
                "principal_without_organization",
                f"{principal!r} belongs to no organisation and cannot create projects",
            )
        project_id = f"p{len(self._projects) + 1}-{abs(hash((principal, clean))) % 100000:05d}"
        self._projects[project_id] = {
            "organization_id": organization,
            "workspace_root": self._workspace_within_trust(workspace_root),
            "name": clean,
        }
        return self.authorize(principal, project_id)

    def list_for(self, principal: str) -> list:
        out = []
        for project_id, record in sorted(self._projects.items()):
            if record["organization_id"] in self._memberships.get(principal, set()):
                out.append(AuthorizedProject(
                    project_id=project_id,
                    organization_id=record["organization_id"],
                    workspace_root=record["workspace_root"],
                    principal_id=principal,
                    name=record.get("name", ""),
                ))
        return out


class AbsentPrincipalResolver:
    """Fail-closed default: the Core never invents a principal, and never trusts a client-supplied one."""

    code = "principal_unavailable"

    def resolve(self, request: object) -> str:
        raise ProjectAuthorityRefusal(
            self.code,
            "no trusted principal source is composed; project-scoped operations are refused",
        )


class InMemoryPrincipalResolver:
    """A deterministic resolver for tests and offline composition: one configured trusted principal."""

    def __init__(self, principal: str | None = None) -> None:
        self._principal = principal

    def resolve(self, request: object) -> str:
        if not self._principal:
            raise ProjectAuthorityRefusal("principal_unavailable", "no principal is configured")
        return self._principal
