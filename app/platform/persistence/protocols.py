"""Repository protocols.

These are the interfaces the application layer depends on. They are defined
without any database knowledge: no driver, no SQL, no connection, no session.
An application service can be written and tested against these and never learn
that PostgreSQL exists.

Two rules are encoded in the method signatures rather than in documentation:

1. **A tenant-scoped repository never takes an organization identifier.** The
   tenant comes from the Unit of Work that produced the repository. There is no
   parameter to pass a foreign tenant and therefore no parameter to forget to
   validate. A method that needs an organization for a structural reason takes an
   *entity* identifier (a project, a run) and the scope still comes from context.
2. **There is no generic ``update(**fields)``.** Each mutable transition has its
   own named method, so an immutable field has nowhere to be written from.

:class:`UsageRecordRepository` has no update and no delete method at all. That is
not a convention to be remembered: the append-only guarantee is expressed by the
absence of the operation, backed by the missing table privileges underneath.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol, Sequence, runtime_checkable

from app.platform.enums import (
    APIKeyStatus,
    MembershipRole,
    MembershipStatus,
    ProjectStatus,
    ProviderAccountStatus,
    RunRecordStatus,
    UserStatus,
)
from app.platform.models import (
    APIKey,
    Membership,
    Organization,
    Project,
    ProviderAccount,
    RunRecord,
    UsageRecord,
    User,
)

__all__ = [
    "UserRepository",
    "OrganizationRepository",
    "MembershipRepository",
    "SystemMembershipRepository",
    "ProjectRepository",
    "TenantProviderAccountRepository",
    "SystemProviderAccountRepository",
    "APIKeyRepository",
    "RunRecordRepository",
    "UsageRecordRepository",
]


@runtime_checkable
class UserRepository(Protocol):
    """Identity records.

    This is not an authentication mechanism. ``find_by_email`` retrieves a row;
    deciding whether a subject may act as that user is an application concern that
    this layer does not and must not implement.
    """

    async def create(self, user: User) -> User:
        """Insert an identity. Available on the server-only path."""

    async def get(self, user_id: str) -> Optional[User]:
        """Return a visible user, or ``None``. Never raises for absence."""

    async def find_by_email(self, email: str) -> Optional[User]:
        """Return a user by email for discovery, or ``None``.

        Retrieval only. It grants nothing.
        """

    async def set_status(self, user_id: str, status: UserStatus) -> User:
        """Change account status. Server-only."""

    async def set_display_name(self, user_id: str, display_name: str) -> User:
        """Change a display name."""


@runtime_checkable
class OrganizationRepository(Protocol):
    """Tenant records."""

    async def create(self, organization: Organization) -> Organization:
        """Insert a tenant. Server-only: creating a tenant cannot be tenant-scoped."""

    async def get(self, organization_id: str) -> Optional[Organization]:
        """Return a tenant visible in the current scope, or ``None``."""

    async def list_for_user(self, user_id: str) -> Sequence[Organization]:
        """Return the tenants a user can reach, via their memberships.

        The pre-tenant discovery read. It answers "which tenants may this subject
        choose between", and the choice itself is an application decision.
        """

    # THERE IS DELIBERATELY NO set_status AND NO set_name HERE.
    #
    # The schema grants the system role SELECT and INSERT on `organizations` and
    # nothing else, and no UPDATE policy exists for either role. A method that
    # promised an update would either fail outright or -- worse -- match zero rows and
    # report the record as missing, which is a silent wrong answer.
    #
    # Renaming or suspending a tenant is therefore not a persistence capability at
    # this stage. When it becomes one, it needs a grant, an UPDATE policy, and a
    # server-only scope, in that order.


@runtime_checkable
class MembershipRepository(Protocol):
    """The only path from a subject to tenant authority."""

    async def create(self, membership: Membership) -> Membership:
        """Insert a membership into the current tenant.

        The tenant comes from the session and is not a parameter, so there is none to
        pass a foreign tenant in. Creating the FIRST membership of a new tenant cannot
        be tenant-scoped and is served by
        :class:`SystemMembershipRepository` instead.
        """

    async def get(self, membership_id: str) -> Optional[Membership]:
        """Return a membership visible in the current scope, or ``None``."""

    async def find_for_user(self, user_id: str) -> Optional[Membership]:
        """Return the current tenant's membership for a user, or ``None``."""

    async def list_for_user(self, user_id: str) -> Sequence[Membership]:
        """Return every membership of a user. Pre-tenant discovery."""

    async def list_for_current_tenant(self) -> Sequence[Membership]:
        """Return the current tenant's memberships."""

    async def set_status(
        self, membership_id: str, status: MembershipStatus
    ) -> Membership:
        """Activate, deactivate, or revoke.

        Reactivation is a status change on the existing row, never a second row:
        ``UNIQUE (organization_id, user_id)`` forbids the duplicate, and the
        provenance keys mean the row is not deleted while history points at it.
        """

    async def set_role(self, membership_id: str, role: MembershipRole) -> Membership:
        """Change a role. Role is not a permission; this only records it."""


@runtime_checkable
class SystemMembershipRepository(Protocol):
    """Membership writes on the server-only scope.

    Registering an organization together with its first ``OWNER`` cannot be
    tenant-scoped, because the tenant has no members yet. This interface takes the
    organization explicitly for that reason, and the system role is the only role
    holding ``INSERT`` on ``memberships``.
    """

    async def create(self, organization_id: str, membership: Membership) -> Membership:
        """Insert a membership for an explicitly named organization."""


@runtime_checkable
class ProjectRepository(Protocol):
    """Tenant-owned work containers.

    There is no ``organization_id`` parameter anywhere: a project is created in,
    listed for, and fetched from the tenant of the surrounding Unit of Work.
    """

    async def create(
        self, project_id: str, name: str, *, slug: str = "",
        workspace_ref: str = "",
    ) -> Project:
        """Insert a project in the current tenant.

        ``workspace_ref`` is stored as given and interpreted nowhere. It is a
        server-derived opaque reference, not a filesystem path.
        """

    async def get(self, project_id: str) -> Optional[Project]:
        """Return a project of the current tenant, or ``None``."""

    async def list_for_current_tenant(self) -> Sequence[Project]:
        """Return the current tenant's projects."""

    async def find_by_slug(self, slug: str) -> Optional[Project]:
        """Return a project by its tenant-scoped slug, or ``None``."""

    async def set_name(self, project_id: str, name: str) -> Project:
        """Rename a project."""

    async def set_slug(self, project_id: str, slug: str) -> Project:
        """Change a tenant-scoped slug. Uniqueness is enforced by the database."""

    async def set_workspace_ref(self, project_id: str, workspace_ref: str) -> Project:
        """Set the opaque workspace reference."""

    async def set_status(self, project_id: str, status: ProjectStatus) -> Project:
        """Activate or archive."""


@runtime_checkable
class TenantProviderAccountRepository(Protocol):
    """Provider credentials owned by a tenant (BYOK).

    System-owned credentials are unreachable from here by construction: the
    repository only ever writes the current tenant identifier and the row-level
    security policy excludes ``organization_id IS NULL``. A null tenant is not a
    wildcard and there is no method that could ask for one.
    """

    async def create(
        self, account_id: str, provider_name: str, secret_ref: str, *,
        metadata: Optional[dict] = None,
    ) -> ProviderAccount:
        """Insert a tenant-owned provider account."""

    async def get(self, account_id: str) -> Optional[ProviderAccount]:
        """Return a provider account of the current tenant, or ``None``."""

    async def list_for_current_tenant(self) -> Sequence[ProviderAccount]:
        """Return the current tenant's provider accounts. Never system-owned."""

    async def find_by_provider_name(self, provider_name: str) -> Optional[ProviderAccount]:
        """Return this tenant's account for a provider, or ``None``."""

    async def set_secret_ref(self, account_id: str, secret_ref: str) -> ProviderAccount:
        """Replace the opaque credential reference."""

    async def set_metadata(self, account_id: str, metadata: dict) -> ProviderAccount:
        """Replace provider metadata. Descriptive only; never money or authority."""

    async def set_status(
        self, account_id: str, status: ProviderAccountStatus
    ) -> ProviderAccount:
        """Enable or disable."""


@runtime_checkable
class SystemProviderAccountRepository(Protocol):
    """System-owned (Forge-managed) provider credentials.

    A separate protocol, on a separate Unit of Work, because a system-owned
    credential belongs to no tenant. Every method here is bounded to rows with a
    null ``organization_id``, so this is not a way to reach tenant credentials.
    """

    async def create(
        self, account_id: str, provider_name: str, secret_ref: str, *,
        metadata: Optional[dict] = None,
    ) -> ProviderAccount:
        """Insert a system-owned provider account."""

    async def get(self, account_id: str) -> Optional[ProviderAccount]:
        """Return a system-owned account, or ``None``. Never a tenant account."""

    async def list_system_owned(self) -> Sequence[ProviderAccount]:
        """Return system-owned accounts. Never tenant accounts."""

    async def find_by_provider_name(self, provider_name: str) -> Optional[ProviderAccount]:
        """Return the system-owned account for a provider, or ``None``."""

    async def set_secret_ref(self, account_id: str, secret_ref: str) -> ProviderAccount:
        """Replace the opaque credential reference."""

    async def set_status(
        self, account_id: str, status: ProviderAccountStatus
    ) -> ProviderAccount:
        """Enable or disable."""


@runtime_checkable
class APIKeyRepository(Protocol):
    """API key records.

    This layer stores what the contract defines and nothing more. There is no
    token column and no hash column, so there is no method that accepts one. The
    authority question (O-1) is not decided here.
    """

    async def create(
        self, key_id: str, created_by_user_id: str, name: str, *,
        key_prefix: str = "", scopes: Sequence[str] = (),
        expires_at: Optional[datetime] = None,
    ) -> APIKey:
        """Insert a key for the current tenant.

        The creator must be a member of that tenant: the provenance key makes any
        other value a foreign key violation rather than a stored inconsistency.
        """

    async def get(self, key_id: str) -> Optional[APIKey]:
        """Return a key of the current tenant, or ``None``."""

    async def list_for_current_tenant(self) -> Sequence[APIKey]:
        """Return the current tenant's keys."""

    async def list_for_creator(self, user_id: str) -> Sequence[APIKey]:
        """Return keys created by a user within the current tenant."""

    async def revoke(self, key_id: str, revoked_at: datetime) -> APIKey:
        """Revoke a key, recording when.

        Revocation and ``revoked_at`` are one transition, because the domain
        contract refuses one without the other.
        """

    async def set_expiry(self, key_id: str, expires_at: Optional[datetime]) -> APIKey:
        """Set or clear an expiry."""

    async def record_use(self, key_id: str, used_at: datetime) -> APIKey:
        """Record last use.

        A usage timestamp, not an authorization decision: whether a key is
        *acceptable* is decided above this layer.
        """


@runtime_checkable
class RunRecordRepository(Protocol):
    """The outer lifecycle of a run.

    ``RunRecord.id`` is the Platform identity; ``core_run_id`` is the Core
    correlation identity. They are two different things, the schema refuses to let
    them collapse, and no method here accepts one where the other belongs.
    """

    async def create_queued(
        self, run_id: str, project_id: str, *, task_id: str = "",
    ) -> RunRecord:
        """Insert a queued run.

        A queued run has no ``core_run_id``, no ``started_at``, and no initiator:
        the schema forbids the first two outright, and a queued run is the one
        state in which an absent initiator is accepted.
        """

    async def get(self, run_id: str) -> Optional[RunRecord]:
        """Return a run of the current tenant, or ``None``."""

    async def find_by_core_run_id(self, core_run_id: str) -> Optional[RunRecord]:
        """Return a run by its Core correlation id, or ``None``.

        Not unique by contract (K-8), so this returns one row; a caller that needs
        every match uses :meth:`list_by_core_run_id`.
        """

    async def list_by_core_run_id(self, core_run_id: str) -> Sequence[RunRecord]:
        """Return every run carrying a Core correlation id."""

    async def list_by_task_id(self, task_id: str) -> Sequence[RunRecord]:
        """Return runs by task id. ``task_id`` is stored but not unique."""

    async def list_for_current_tenant(self) -> Sequence[RunRecord]:
        """Return the current tenant's runs."""

    async def claim(
        self, run_id: str, *, core_run_id: str, initiated_by_user_id: str,
        started_at: datetime,
    ) -> RunRecord:
        """Move a queued run to ``running`` and record its Core identity.

        This is the transition that crosses the Core boundary, so it sets
        ``core_run_id``, ``started_at``, and the initiator together: the schema
        requires all three on any non-queued state.

        Refuses a run that is not currently queued, so two workers racing to claim
        the same run cannot both succeed. The check is part of the UPDATE
        statement, not a separate read.
        """

    async def finish(
        self, run_id: str, *, status: RunRecordStatus, finished_at: datetime,
        failure_classification: str = "",
    ) -> RunRecord:
        """Move a run to a terminal status.

        Refuses a status the schema would reject, and refuses a
        ``failure_classification`` together with ``succeeded``, which the contract
        forbids.
        """

    async def set_status(
        self, run_id: str, status: RunRecordStatus, *,
        failure_classification: str = "",
    ) -> RunRecord:
        """Change status without finishing, for a non-terminal transition."""

    async def increment_attempt_count(self, run_id: str) -> RunRecord:
        """Record one more attempt.

        A single ``attempt_count = attempt_count + 1`` statement, so concurrent
        increments cannot lose an update the way a read-modify-write would.
        """


@runtime_checkable
class UsageRecordRepository(Protocol):
    """Physical usage observations. APPEND-ONLY.

    There is no update method and no delete method. That is the design, not an
    omission: a usage record is an observation, the table's privileges withhold
    ``UPDATE`` and ``DELETE``, and no policy grants them. A correction is a new
    record, and ``UNIQUE (run_record_id, attempt_number)`` stops a second record
    from silently overwriting the first.

    No field here carries money. There is no cost, price, charge, balance, or
    currency, and none may be added: this is a measurement, not an invoice.
    """

    async def append(self, usage: UsageRecord) -> UsageRecord:
        """Insert one observation.

        A duplicate ``(run_record_id, attempt_number)`` is a unique violation, not
        a silent overwrite.
        """

    async def get(self, usage_id: str) -> Optional[UsageRecord]:
        """Return an observation of the current tenant, or ``None``."""

    async def list_for_run(self, run_record_id: str) -> Sequence[UsageRecord]:
        """Return a run's observations, oldest attempt first."""

    async def list_for_current_tenant(self) -> Sequence[UsageRecord]:
        """Return the current tenant's observations."""
