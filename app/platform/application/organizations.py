"""Discovery use cases: AS-1 and AS-2 (Stage 1 / Step 4, design section 5).

The first vertical slice of Application Services. Read-only, and deliberately narrow:

* **AS-1** lists the organizations the authenticated subject may choose between;
* **AS-2** lists the projects available in one selected organization.

What this module does **not** do, and what keeps the slice honest: it creates no project,
starts no run, and touches no `RunRecord`, `UsageRecord`, `APIKey`, or provider account.
The system scope and system-owned credentials are unreachable from here -- the
composition holds a tenant-login pool, and no call in this module names a system
repository (design section 8.2 item 3).

Transaction boundaries, stated precisely because the design's one-line summary is
coarser than the code (the design document records this in section 7.6):

* a **pre-tenant** read transaction resolves the subject and the candidate set. This is
  unavoidable: naming the subject's *candidate* organizations requires a scope that is
  not bound to an organization, which is exactly what ``pre_tenant`` is for;
* the **tenant** transaction then re-decides membership and organization status for the
  bound tenant and performs the authorized read. The decision is made against data read
  **inside the transaction that uses it**, so a membership revoked between the two reads
  cannot be used.

Ordering is load-bearing (M2-8): the subject is resolved, and refuses, **before** any
tenant-scoped Unit of Work is opened. A test asserts that by counting scope opens.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Sequence

from app.platform.application.authorization import (
    bind_organization,
    requires_subject,
    resolve_candidate_organizations,
    resolve_subject,
    select_candidate,
)
from app.platform.application.context import AuthorizedContext
from app.platform.application.errors import translate_persistence_error
from app.platform.models import Organization
from app.platform.principal import AuthenticatedPrincipal

__all__ = [
    "OrganizationSummary",
    "ProjectSummary",
    "DiscoveryService",
]


@dataclass(frozen=True)
class OrganizationSummary:
    """One organization the caller may choose between.

    Carries no member count and no identifier of anyone else: a discovery response is
    about the caller's own tenancy and must not become a way to observe other people
    (design section 8.2 item 4).

    ``role`` is the caller's **own** role in that organization, echoed back to the person
    who holds it. It grants nothing here.
    """

    organization_id: str
    name: str
    slug: str
    role: str

    def to_dict(self) -> dict[str, str]:
        return {
            "organization_id": self.organization_id,
            "name": self.name,
            "slug": self.slug,
            "role": self.role,
        }


@dataclass(frozen=True)
class ProjectSummary:
    """One project in the bound organization."""

    project_id: str
    name: str
    slug: str
    status: str

    def to_dict(self) -> dict[str, str]:
        return {
            "project_id": self.project_id,
            "name": self.name,
            "slug": self.slug,
            "status": self.status,
        }


class DiscoveryService:
    """The AS-1 and AS-2 use cases.

    The service holds a :class:`~app.platform.persistence.database.PlatformDatabase` and
    owns the transaction boundaries; the authorization decision itself lives in
    :mod:`app.platform.application.authorization`, so the rule has one home and this
    module has none of it.

    Every method takes the principal as its first argument and returns *summaries*. No
    method accepts a ``user_id``, a role, a workspace, or a path: a client cannot name a
    subject or an authority, only an organization it wants to look inside (M2-2).
    """

    def __init__(self, discovery_database, tenant_database=None) -> None:
        """Two handles, because the two scopes are served by two logins.

        Discovery runs on the **pre-tenant** scope, which is the system role with the
        system scope declared; the authorized read runs on the **tenant** scope, which is
        the application role. Each Platform login is a member of exactly one of those
        roles -- that separation is a security invariant, so it is stated in the
        constructor rather than hidden behind one handle that could not honour it.

        ``tenant_database`` defaults to ``discovery_database`` for the case where a
        single login is a member of both roles. That configuration is *not* one this
        layer recommends; it is accepted only so a caller is not forced to construct two
        objects when its deployment genuinely has one. The default is safe in the sense
        that matters: the tenant scope still steps down with ``SET LOCAL ROLE``, and
        ``PlatformDatabase`` refuses a pool whose effective role is not subject to
        row-level security.
        """

        self._discovery = discovery_database
        self._tenant = tenant_database if tenant_database is not None else discovery_database

    @property
    def discovery_database(self):
        return self._discovery

    @property
    def tenant_database(self):
        return self._tenant

    # -- shared steps ------------------------------------------------------- #
    async def _resolve(self, principal: Optional[AuthenticatedPrincipal]):
        """Steps 1-3 in one pre-tenant transaction: ``(subject, candidates, roles)``.

        Raises before any tenant scope exists, which is what makes M2-8 hold.
        """

        verified = requires_subject(principal)
        async with self._discovery.pre_tenant(user_id=verified.subject_id) as unit:
            subject = await resolve_subject(unit, verified)
            candidates = await resolve_candidate_organizations(unit, subject)
            memberships = await unit.memberships.list_for_user(subject.subject_id)
        roles = {
            str(membership.organization_id): membership.role.value
            for membership in memberships
        }
        return subject, candidates, roles

    @contextlib.asynccontextmanager
    async def _authorized(
        self, principal: Optional[AuthenticatedPrincipal], organization_id: Optional[str]
    ) -> AsyncIterator[tuple[AuthorizedContext, object]]:
        """Steps 1-6, with the tenant transaction open for the caller's body.

        Yields ``(context, tenant_unit)`` so the authorized read happens in the same
        transaction that decided it.
        """

        subject, candidates, _ = await self._resolve(principal)
        selected = select_candidate(candidates, organization_id)
        async with self._tenant.tenant(
            str(selected.id), user_id=subject.subject_id
        ) as tenant_unit:
            context = await bind_organization(
                tenant_unit, subject, str(selected.id)
            )
            yield context, tenant_unit

    # -- AS-1 --------------------------------------------------------------- #
    async def list_organizations(
        self, principal: Optional[AuthenticatedPrincipal]
    ) -> Sequence[OrganizationSummary]:
        """AS-1: the organizations available to the caller.

        No client input at all. An empty result is not an error: it says the caller
        belongs to no active organization, which is a fact about the caller and
        discloses nothing about anyone else.
        """

        try:
            _, candidates, roles = await self._resolve(principal)
        except Exception as exc:  # noqa: BLE001 - re-raised as an application error
            raise translate_persistence_error(exc) from exc

        return tuple(
            self._summarize(organization, roles) for organization in candidates
        )

    @staticmethod
    def _summarize(
        organization: Organization, roles: dict[str, str]
    ) -> OrganizationSummary:
        return OrganizationSummary(
            organization_id=str(organization.id),
            name=organization.name,
            slug=organization.slug,
            role=roles.get(str(organization.id), ""),
        )

    # -- AS-2 --------------------------------------------------------------- #
    async def list_projects(
        self,
        principal: Optional[AuthenticatedPrincipal],
        organization_id: Optional[str],
    ) -> Sequence[ProjectSummary]:
        """AS-2: the projects in the organization the caller selected.

        ``organization_id`` is a **claim**. It is checked against the caller's own
        candidate set, and the server never substitutes a different one. A claim outside
        the set is answered ``not_found`` rather than ``not_permitted``, so the caller
        cannot tell another tenant's identifier from a non-existent one (design section
        6.3).
        """

        try:
            async with self._authorized(principal, organization_id) as (_, unit):
                projects = await unit.projects.list_for_current_tenant()
        except Exception as exc:  # noqa: BLE001
            raise translate_persistence_error(exc) from exc

        return tuple(
            ProjectSummary(
                project_id=str(project.id),
                name=project.name,
                slug=project.slug,
                status=project.status.value,
            )
            for project in projects
        )

    # -- for the next slice ------------------------------------------------- #
    @contextlib.asynccontextmanager
    async def authorized_context(
        self, principal: Optional[AuthenticatedPrincipal], organization_id: Optional[str]
    ) -> AsyncIterator[AuthorizedContext]:
        """The authorized context alone, so AS-3 reuses this decision.

        Exposed rather than kept private so the next slice does not re-implement the
        rule, and so a test can assert what was bound without reaching into a private
        helper.
        """

        try:
            async with self._authorized(principal, organization_id) as (context, _):
                yield context
        except Exception as exc:  # noqa: BLE001
            raise translate_persistence_error(exc) from exc
