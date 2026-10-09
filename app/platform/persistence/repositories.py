"""PostgreSQL repository implementations.

Every class here follows the same three rules, and they are the reason this layer
cannot quietly serve the wrong tenant:

1. **The tenant comes from the session, never from a parameter.** A tenant-scoped
   write reads ``session.organization_id``; a method has no argument that could
   carry another tenant's identifier. The row-level security policies are the
   second enforcement, not the only one.
2. **No transaction management.** A statement goes through :class:`Session`, which
   refuses it unless a Unit of Work opened a transaction. There is no
   ``commit``/``rollback``/``begin`` in this module at all.
3. **No ``update(**fields)``.** Each mutable transition is its own named method, so
   an immutable field has nowhere to be written from.

Identifiers are validated by the mapper before they reach SQL, so a malformed
identifier is a named :class:`InvalidIdentifierError` rather than a cast failure
from the driver.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional, Sequence

from app.platform.enums import (
    APIKeyStatus,
    MembershipRole,
    MembershipStatus,
    OrganizationStatus,
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
from app.platform.persistence.database import Session
from app.platform.persistence.errors import (
    ConcurrentModificationError,
    EntityNotFound,
    PermissionDeniedError,
    PersistenceError,
    TransactionError,
)
from app.platform.persistence.mapper import (
    api_key_from_row,
    as_uuid,
    membership_from_row,
    optional_text,
    organization_from_row,
    project_from_row,
    provider_account_from_row,
    require_mapping,
    run_record_from_row,
    scopes_to_array,
    usage_record_from_row,
    user_from_row,
)

__all__ = [
    "PostgresUserRepository",
    "PostgresOrganizationRepository",
    "PostgresMembershipRepository",
    "PostgresSystemMembershipRepository",
    "PostgresSystemUserRepository",
    "PostgresProjectRepository",
    "PostgresProviderAccountRepository",
    "PostgresSystemProviderAccountRepository",
    "PostgresAPIKeyRepository",
    "PostgresRunRecordRepository",
    "PostgresUsageRecordRepository",
]

USER_COLUMNS = "id, email, display_name, status, created_at, updated_at"
ORGANIZATION_COLUMNS = "id, name, slug, is_personal, status, created_at, updated_at"
MEMBERSHIP_COLUMNS = (
    "id, organization_id, user_id, role, status, created_at, updated_at"
)
PROJECT_COLUMNS = (
    "id, organization_id, name, slug, status, workspace_ref, created_at, updated_at"
)
PROVIDER_ACCOUNT_COLUMNS = (
    "id, organization_id, provider_name, secret_ref, status, metadata, "
    "created_at, updated_at"
)
API_KEY_COLUMNS = (
    "id, organization_id, created_by_user_id, name, key_prefix, scopes, status, "
    "expires_at, revoked_at, last_used_at, created_at, updated_at"
)
RUN_RECORD_COLUMNS = (
    "id, organization_id, project_id, core_run_id, initiated_by_user_id, task_id, "
    "status, started_at, finished_at, attempt_count, failure_classification, "
    "created_at, updated_at"
)
USAGE_RECORD_COLUMNS = (
    "id, organization_id, run_record_id, core_run_id, attempt_number, "
    "provider_name, model_name, input_tokens, output_tokens, cached_tokens, "
    "duration_seconds, success, fallback, tool_call_count, completed_at, "
    "error_type, created_at"
)


def metadata_json(metadata: Optional[dict]) -> str:
    """Serialize provider metadata, failing as a persistence error.

    ``json.dumps`` raises ``TypeError`` for an unserializable value and ``ValueError``
    for a circular one. Letting either escape would break the rule that a repository
    raises only the persistence hierarchy, and a caller catching
    :class:`PersistenceError` would miss it.
    """

    payload = require_mapping(metadata or {})
    try:
        return json.dumps(payload)
    except (TypeError, ValueError) as exc:
        raise PersistenceError(
            f"provider metadata is not JSON-serializable: {exc}"
        ) from exc


class _TenantBound:
    """Shared base: a repository bound to the tenant of its session.

    The tenant is read from the session when a statement runs, not captured at
    construction, so a repository used after its transaction ended fails on the
    session's own guard rather than acting on a stale scope.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        return self._session

    def _tenant(self) -> str:
        organization_id = self._session.organization_id
        if not organization_id:
            raise TransactionError(
                "this repository is tenant-scoped but the current transaction has "
                "no tenant context; use PlatformDatabase.tenant(...)"
            )
        return organization_id

    def _require_matching_tenant(self, organization_id: Optional[str]) -> None:
        """Refuse a record that names a different tenant.

        The tenant a write lands in comes from the session, so a record naming
        another tenant is a disagreement between the caller and its own transaction.
        Silently writing it into the session's tenant would be confused-deputy
        behaviour: the caller believes it addressed one tenant and another was
        written. The write is refused instead, and the caller is told which two
        identifiers disagree.
        """

        if organization_id is None:
            raise TransactionError(
                "this record carries no organization_id, and this layer does not "
                "infer one: set organization_id to the tenant of the transaction "
                "explicitly, so the write is never an implicit redirect"
            )
        if str(organization_id) != str(self._tenant()):
            raise TransactionError(
                f"record organization_id {organization_id!r} is not the tenant of "
                f"this transaction ({self._tenant()!r}); refusing to write it into a "
                "different tenant"
            )


# =========================================================================== #
# users
# =========================================================================== #
class PostgresUserRepository:
    """Identity records. Retrieval only; this is not authentication."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def create(self, user: User) -> User:
        user_id = as_uuid(user.id, "User.id")
        # `updated_at` exists in the table but not in the domain contract, so it
        # stays NULL until an update sets it. Writing a domain field that does not
        # exist is how a mapper silently drifts from its model; this one is caught
        # by the tests rather than by a reader.
        row = await self._session.fetchrow(
            "INSERT INTO users (id, email, display_name, status, created_at) "
            "VALUES ($1, $2, $3, $4, $5) "
            f"RETURNING {USER_COLUMNS}",
            user_id, user.email, user.display_name, user.status.value,
            user.created_at,
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise EntityNotFound("User", str(user_id))
        return user_from_row(row)

    async def get(self, user_id: str) -> Optional[User]:
        row = await self._session.fetchrow(
            f"SELECT {USER_COLUMNS} FROM users WHERE id = $1",
            as_uuid(user_id, "User.id"),
        )
        return None if row is None else user_from_row(row)

    async def find_by_email(self, email: str) -> Optional[User]:
        """Resolve a user by email, within what this scope may see.

        Under the system scope the discovery policy returns only the declared
        subject's own row, so this is "resolve the subject I was asked about" and not
        a way to look anybody up. Under the tenant scope the row-level policy decides,
        which means a same-tenant user may be resolved and a foreign one may not.
        """

        row = await self._session.fetchrow(
            f"SELECT {USER_COLUMNS} FROM users WHERE email = $1", email
        )
        return None if row is None else user_from_row(row)

    # THERE IS DELIBERATELY NO set_status AND NO set_email ON THIS REPOSITORY.
    #
    # `status` is a server decision -- a suspended account must not be able to
    # un-suspend itself -- and `email` is an identity attribute. The tenant role's
    # UPDATE privilege is column-limited to `display_name` in migration 0015, so both
    # are denied at the privilege layer as well as having no policy. The server scope
    # owns them; see `PostgresSystemUserRepository`.

    async def set_display_name(self, user_id: str, display_name: str) -> User:
        """Set the subject's own display name. The only self-service field."""

        row = await self._session.fetchrow(
            "UPDATE users SET display_name = $1, updated_at = now() WHERE id = $2 "
            f"RETURNING {USER_COLUMNS}",
            display_name, as_uuid(user_id, "User.id"),
        )
        if row is None:
            raise EntityNotFound("User", user_id)
        return user_from_row(row)

    # THERE IS DELIBERATELY NO list_all.
    #
    # It existed as a "server scope" directory read. The discovery policies are now
    # bounded by the declared subject, so under the system scope it could only ever
    # return that one subject's row -- and under the tenant scope it would return
    # whatever the tenant policy allows, which is a different question. A method whose
    # name claims a directory and whose behaviour is "one row, or this tenant's rows"
    # is a trap, so it is gone. Enumerating identities is not a persistence
    # capability at this stage.


class PostgresSystemUserRepository:
    """Account administration on the server-only scope.

    The counterpart of the tenant repository, and deliberately not the same class:
    the two hold **disjoint** write capabilities on ``users``.

    * the tenant scope may set ``display_name`` on the subject's own row;
    * this scope may set ``status`` and ``email`` on any row, because account
      administration is server-side by design.

    Neither can do the other's job, and that is enforced by column-level privileges
    with a policy attached to each, not by convention.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def _update(self, user_id: str, assignments: str, *args: object) -> User:
        row = await self._session.fetchrow(
            f"UPDATE users SET {assignments}, updated_at = now() "
            f"WHERE id = ${len(args) + 1} RETURNING {USER_COLUMNS}",
            *args,
            as_uuid(user_id, "User.id"),
        )
        if row is None:
            raise EntityNotFound("User", user_id)
        return user_from_row(row)

    async def set_status(self, user_id: str, status: UserStatus) -> User:
        """Suspend or reactivate an account. A server decision."""

        return await self._update(user_id, "status = $1", status.value)

    async def set_email(self, user_id: str, email: str) -> User:
        """Change the identity attribute. A server decision."""

        return await self._update(user_id, "email = $1", email)

    async def set_display_name(self, user_id: str, display_name: str) -> User:
        """Refused: this scope does not hold the column.

        The method exists so the refusal is this layer's ``PermissionDeniedError``
        naming the boundary, rather than a raw driver privilege error the caller has
        to interpret. Migration 0015 grants this scope ``status`` and ``email`` only.
        """

        raise PermissionDeniedError(
            "the server scope may change status and email, not display_name; the "
            "subject owns its display name and sets it through the tenant scope"
        )


# =========================================================================== #
# organizations
# =========================================================================== #
class PostgresOrganizationRepository:
    """Tenant records."""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def create(self, organization: Organization) -> Organization:
        organization_id = as_uuid(organization.id, "Organization.id")
        # `updated_at` exists in the table but not in the domain contract, so it
        # stays NULL until an update sets it.
        row = await self._session.fetchrow(
            "INSERT INTO organizations (id, name, slug, is_personal, status, "
            "created_at) VALUES ($1, $2, $3, $4, $5, $6) "
            f"RETURNING {ORGANIZATION_COLUMNS}",
            organization_id, organization.name,
            # The domain uses '' for "no slug"; the column uses NULL. The mapping
            # is explicit here rather than relying on the two agreeing.
            optional_text(organization.slug),
            organization.is_personal, organization.status.value,
            organization.created_at,
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise EntityNotFound("Organization", str(organization_id))
        return organization_from_row(row)

    async def get(self, organization_id: str) -> Optional[Organization]:
        row = await self._session.fetchrow(
            f"SELECT {ORGANIZATION_COLUMNS} FROM organizations WHERE id = $1",
            as_uuid(organization_id, "Organization.id"),
        )
        return None if row is None else organization_from_row(row)

    async def list_for_user(self, user_id: str) -> Sequence[Organization]:
        """The tenants a subject can reach, through their memberships.

        The pre-tenant discovery read. It lists candidates; choosing among them is an
        application decision. The organization policy resolves tenancy through the
        **declared subject's** membership, so the session is asked to agree with the
        argument before any SQL runs.
        """

        self._session.require_subject(user_id)
        rows = await self._session.fetch(
            f"SELECT o.{ORGANIZATION_COLUMNS.replace(', ', ', o.')} "
            "FROM organizations o "
            "JOIN memberships m ON m.organization_id = o.id "
            "WHERE m.user_id = $1 AND m.status = 'active' "
            "ORDER BY o.created_at",
            as_uuid(user_id, "User.id"),
        )
        return [organization_from_row(r) for r in rows]

    # THERE IS DELIBERATELY NO set_status AND NO set_name ON THIS REPOSITORY.
    #
    # The schema grants the system role SELECT and INSERT on `organizations` and
    # nothing else, and no UPDATE policy exists for either role. Both methods
    # existed here and neither could ever succeed: under the system role the
    # statement was refused outright, and under the tenant role row-level security
    # filtered it to zero rows, so the caller was told the tenant did not exist. An
    # authorization refusal that the caller reads as absence is the worst of the
    # available outcomes, and the honest fix is to stop advertising the capability.
    #
    # Renaming or suspending a tenant needs a grant, an UPDATE policy, and a
    # server-only scope before it can be offered.


# =========================================================================== #
# memberships
# =========================================================================== #
class PostgresMembershipRepository(_TenantBound):
    """The only path from a subject to tenant authority.

    Tenant-scoped, because a membership cannot be created without deciding which
    tenant it belongs to and that decision must come from the session. Creating the
    first membership of a brand-new tenant is a bootstrap step, and it is served by
    :class:`PostgresSystemMembershipRepository` on the server-only scope instead of
    by a parameter here.
    """

    async def create(self, membership: Membership) -> Membership:
        """Insert a membership into the tenant this transaction is scoped to.

        The tenant comes from the session, never from ``membership``. There is no
        organization parameter, so there is none to pass a foreign tenant in.

        ``UNIQUE (organization_id, user_id)`` is the authority on duplicates. No
        pre-check is performed: a check-then-insert would still lose a race, and the
        database is the only place that cannot.
        """

        return await self._insert(self._tenant(), membership)

    async def _insert(self, organization_id: str, membership: Membership) -> Membership:
        membership_id = as_uuid(membership.id, "Membership.id")
        row = await self._session.fetchrow(
            "INSERT INTO memberships (id, organization_id, user_id, role, status, "
            "created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            f"RETURNING {MEMBERSHIP_COLUMNS}",
            membership_id,
            as_uuid(organization_id, "organization_id"),
            as_uuid(membership.user_id, "Membership.user_id"),
            membership.role.value, membership.status.value,
            membership.created_at, membership.updated_at,
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise EntityNotFound("Membership", str(membership_id))
        return membership_from_row(row)

    async def get(self, membership_id: str) -> Optional[Membership]:
        row = await self._session.fetchrow(
            f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships WHERE id = $1",
            as_uuid(membership_id, "Membership.id"),
        )
        return None if row is None else membership_from_row(row)

    async def find_for_user(self, user_id: str) -> Optional[Membership]:
        """The current tenant's membership for a user, if any."""

        row = await self._session.fetchrow(
            f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships "
            "WHERE user_id = $1 AND organization_id = $2",
            as_uuid(user_id, "User.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else membership_from_row(row)

    async def list_for_user(self, user_id: str) -> Sequence[Membership]:
        """Every membership of a user. Pre-tenant discovery.

        The discovery policy returns only memberships of the subject the transaction
        declared, so this read cannot be pointed at somebody else: the session
        refuses the disagreement rather than silently returning nothing, and the
        policy would refuse it even if the session allowed it.
        """

        self._session.require_subject(user_id)
        rows = await self._session.fetch(
            f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships WHERE user_id = $1 "
            "ORDER BY created_at",
            as_uuid(user_id, "User.id"),
        )
        return [membership_from_row(r) for r in rows]

    async def list_for_current_tenant(self) -> Sequence[Membership]:
        rows = await self._session.fetch(
            f"SELECT {MEMBERSHIP_COLUMNS} FROM memberships "
            "WHERE organization_id = $1 ORDER BY created_at",
            as_uuid(self._tenant(), "organization_id"),
        )
        return [membership_from_row(r) for r in rows]

    async def set_status(
        self, membership_id: str, status: MembershipStatus
    ) -> Membership:
        row = await self._session.fetchrow(
            "UPDATE memberships SET status = $1, updated_at = now() WHERE id = $2 "
            f"RETURNING {MEMBERSHIP_COLUMNS}",
            status.value, as_uuid(membership_id, "Membership.id"),
        )
        if row is None:
            raise EntityNotFound("Membership", membership_id)
        return membership_from_row(row)

    async def set_role(self, membership_id: str, role: MembershipRole) -> Membership:
        """Record a role. A role is not a permission; nothing here grants one."""

        row = await self._session.fetchrow(
            "UPDATE memberships SET role = $1, updated_at = now() WHERE id = $2 "
            f"RETURNING {MEMBERSHIP_COLUMNS}",
            role.value, as_uuid(membership_id, "Membership.id"),
        )
        if row is None:
            raise EntityNotFound("Membership", membership_id)
        return membership_from_row(row)


# =========================================================================== #
# projects
# =========================================================================== #
class PostgresSystemMembershipRepository:
    """Membership writes on the server-only scope.

    Registering an organization and its first ``OWNER`` cannot be tenant-scoped: the
    tenant has no members yet, so there is no tenant context to derive. This class
    takes the organization explicitly for exactly that reason, and is reachable only
    from the server scope with the system role -- the system role is the only role
    with ``INSERT`` on ``memberships``.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def create(self, organization_id: str, membership: Membership) -> Membership:
        if str(membership.organization_id) != str(organization_id):
            raise TransactionError(
                f"membership.organization_id {membership.organization_id!r} does not "
                f"match the organization argument {organization_id!r}"
            )
        membership_id = as_uuid(membership.id, "Membership.id")
        row = await self._session.fetchrow(
            "INSERT INTO memberships (id, organization_id, user_id, role, status, "
            "created_at, updated_at) VALUES ($1, $2, $3, $4, $5, $6, $7) "
            f"RETURNING {MEMBERSHIP_COLUMNS}",
            membership_id,
            as_uuid(organization_id, "organization_id"),
            as_uuid(membership.user_id, "Membership.user_id"),
            membership.role.value, membership.status.value,
            membership.created_at, membership.updated_at,
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise EntityNotFound("Membership", str(membership_id))
        return membership_from_row(row)


class PostgresProjectRepository(_TenantBound):
    """Tenant-owned work containers. Never takes an organization identifier."""

    async def create(
        self, project_id: str, name: str, *, slug: str = "",
        workspace_ref: str = "",
    ) -> Project:
        """Insert a project into the tenant of this transaction.

        ``workspace_ref`` is stored exactly as given and interpreted nowhere. It is
        a server-derived opaque reference, not a filesystem path, and this layer
        performs no workspace authorization: that belongs above persistence.

        Tenant-scoped slug uniqueness is left to the partial unique index, so a
        duplicate in this tenant is a unique violation and the same slug in another
        tenant is allowed.
        """

        row = await self._session.fetchrow(
            "INSERT INTO projects (id, organization_id, name, slug, status, "
            "workspace_ref, created_at) VALUES ($1, $2, $3, $4, 'active', $5, now()) "
            f"RETURNING {PROJECT_COLUMNS}",
            as_uuid(project_id, "Project.id"),
            as_uuid(self._tenant(), "organization_id"),
            name, optional_text(slug), optional_text(workspace_ref),
        )
        if row is None:  # pragma: no cover - RETURNING always yields a row
            raise EntityNotFound("Project", project_id)
        return project_from_row(row)

    async def get(self, project_id: str) -> Optional[Project]:
        row = await self._session.fetchrow(
            f"SELECT {PROJECT_COLUMNS} FROM projects WHERE id = $1 AND "
            "organization_id = $2",
            as_uuid(project_id, "Project.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else project_from_row(row)

    async def list_for_current_tenant(self) -> Sequence[Project]:
        rows = await self._session.fetch(
            f"SELECT {PROJECT_COLUMNS} FROM projects WHERE organization_id = $1 "
            "ORDER BY created_at",
            as_uuid(self._tenant(), "organization_id"),
        )
        return [project_from_row(r) for r in rows]

    async def find_by_slug(self, slug: str) -> Optional[Project]:
        """Find a project by slug, applying the same ``''`` <-> NULL rule as writes.

        Without ``optional_text`` here, a project created without a slug round-trips
        as ``slug == ''`` but is not findable by that value, so an existence check
        would report a project missing that ``get`` returns.
        """

        # ``IS NOT DISTINCT FROM``, because ``slug = NULL`` is never true in SQL, so a
        # plain equality test against a normalized-to-NULL slug finds nothing and the
        # lookup disagrees with the writer and with ``get``. The operator is an
        # equality test with defined NULL semantics; it is not a range predicate and
        # does not weaken the index.
        row = await self._session.fetchrow(
            f"SELECT {PROJECT_COLUMNS} FROM projects WHERE organization_id = $1 "
            "AND slug IS NOT DISTINCT FROM $2",
            as_uuid(self._tenant(), "organization_id"), optional_text(slug),
        )
        return None if row is None else project_from_row(row)

    async def _set(self, project_id: str, column: str, value: Any) -> Project:
        row = await self._session.fetchrow(
            f"UPDATE projects SET {column} = $1, updated_at = now() WHERE id = $2 "
            f"AND organization_id = $3 RETURNING {PROJECT_COLUMNS}",
            value, as_uuid(project_id, "Project.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("Project", project_id)
        return project_from_row(row)

    async def set_name(self, project_id: str, name: str) -> Project:
        return await self._set(project_id, "name", name)

    async def set_slug(self, project_id: str, slug: str) -> Project:
        return await self._set(project_id, "slug", optional_text(slug))

    async def set_workspace_ref(self, project_id: str, workspace_ref: str) -> Project:
        return await self._set(
            project_id, "workspace_ref", optional_text(workspace_ref)
        )

    async def set_status(self, project_id: str, status: ProjectStatus) -> Project:
        return await self._set(project_id, "status", status.value)


# =========================================================================== #
# provider accounts
# =========================================================================== #
class PostgresProviderAccountRepository(_TenantBound):
    """Tenant-owned provider credentials (BYOK).

    System-owned credentials are unreachable from this class by construction: every
    statement is bounded by the current tenant identifier, and the row-level
    security policy additionally requires ``organization_id IS NOT NULL``. There is
    no method that could ask for a null tenant, and ``organization_id IS NULL`` is
    never used as a lookup here.
    """

    async def create(
        self, account_id: str, provider_name: str, secret_ref: str, *,
        metadata: Optional[dict] = None,
    ) -> ProviderAccount:
        row = await self._session.fetchrow(
            "INSERT INTO provider_accounts (id, organization_id, provider_name, "
            "secret_ref, status, metadata, created_at) "
            "VALUES ($1, $2, $3, $4, 'active', $5, now()) "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            as_uuid(account_id, "ProviderAccount.id"),
            as_uuid(self._tenant(), "organization_id"),
            provider_name, secret_ref,
            metadata_json(metadata),
        )
        if row is None:  # pragma: no cover
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)

    async def get(self, account_id: str) -> Optional[ProviderAccount]:
        row = await self._session.fetchrow(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts "
            "WHERE id = $1 AND organization_id = $2",
            as_uuid(account_id, "ProviderAccount.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else provider_account_from_row(row)

    async def list_for_current_tenant(self) -> Sequence[ProviderAccount]:
        rows = await self._session.fetch(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts "
            "WHERE organization_id = $1 ORDER BY provider_name",
            as_uuid(self._tenant(), "organization_id"),
        )
        return [provider_account_from_row(r) for r in rows]

    async def find_by_provider_name(
        self, provider_name: str
    ) -> Optional[ProviderAccount]:
        row = await self._session.fetchrow(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts "
            "WHERE organization_id = $1 AND provider_name = $2",
            as_uuid(self._tenant(), "organization_id"), provider_name,
        )
        return None if row is None else provider_account_from_row(row)

    async def set_secret_ref(self, account_id: str, secret_ref: str) -> ProviderAccount:
        """Replace the opaque credential reference."""

        row = await self._session.fetchrow(
            "UPDATE provider_accounts SET secret_ref = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id = $3 "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            secret_ref, as_uuid(account_id, "ProviderAccount.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)

    async def set_metadata(self, account_id: str, metadata: dict) -> ProviderAccount:
        """Replace provider metadata. Descriptive only; never money or authority."""

        row = await self._session.fetchrow(
            "UPDATE provider_accounts SET metadata = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id = $3 "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            metadata_json(metadata),
            as_uuid(account_id, "ProviderAccount.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)

    async def set_status(
        self, account_id: str, status: ProviderAccountStatus
    ) -> ProviderAccount:
        """Enable or disable. There is no method that could change ownership mode."""

        row = await self._session.fetchrow(
            "UPDATE provider_accounts SET status = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id = $3 "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            status.value, as_uuid(account_id, "ProviderAccount.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)


class PostgresSystemProviderAccountRepository:
    """System-owned (Forge-managed) provider credentials.

    Every statement is bounded to ``organization_id IS NULL``. That is the one
    place in the Platform persistence layer where a null tenant is a legitimate
    filter, and it is legitimate only because this class is reachable solely from
    the server scope with the system role, whose policy requires both a null
    organization and an explicitly declared system scope.

    This is not a route to tenant credentials: the system role has no privilege on
    any operational table beyond this one, and the policy refuses a non-null
    organization.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    async def create(
        self, account_id: str, provider_name: str, secret_ref: str, *,
        metadata: Optional[dict] = None,
    ) -> ProviderAccount:
        row = await self._session.fetchrow(
            "INSERT INTO provider_accounts (id, organization_id, provider_name, "
            "secret_ref, status, metadata, created_at) "
            "VALUES ($1, NULL, $2, $3, 'active', $4, now()) "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            as_uuid(account_id, "ProviderAccount.id"),
            provider_name, secret_ref,
            metadata_json(metadata),
        )
        if row is None:  # pragma: no cover
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)

    async def get(self, account_id: str) -> Optional[ProviderAccount]:
        row = await self._session.fetchrow(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts "
            "WHERE id = $1 AND organization_id IS NULL",
            as_uuid(account_id, "ProviderAccount.id"),
        )
        return None if row is None else provider_account_from_row(row)

    async def list_system_owned(self) -> Sequence[ProviderAccount]:
        rows = await self._session.fetch(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts "
            "WHERE organization_id IS NULL ORDER BY provider_name"
        )
        return [provider_account_from_row(r) for r in rows]

    async def find_by_provider_name(
        self, provider_name: str
    ) -> Optional[ProviderAccount]:
        row = await self._session.fetchrow(
            f"SELECT {PROVIDER_ACCOUNT_COLUMNS} FROM provider_accounts "
            "WHERE organization_id IS NULL AND provider_name = $1", provider_name
        )
        return None if row is None else provider_account_from_row(row)

    async def set_secret_ref(self, account_id: str, secret_ref: str) -> ProviderAccount:
        row = await self._session.fetchrow(
            "UPDATE provider_accounts SET secret_ref = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id IS NULL "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            secret_ref, as_uuid(account_id, "ProviderAccount.id"),
        )
        if row is None:
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)

    async def set_status(
        self, account_id: str, status: ProviderAccountStatus
    ) -> ProviderAccount:
        row = await self._session.fetchrow(
            "UPDATE provider_accounts SET status = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id IS NULL "
            f"RETURNING {PROVIDER_ACCOUNT_COLUMNS}",
            status.value, as_uuid(account_id, "ProviderAccount.id"),
        )
        if row is None:
            raise EntityNotFound("ProviderAccount", account_id)
        return provider_account_from_row(row)


# =========================================================================== #
# api keys
# =========================================================================== #
class PostgresAPIKeyRepository(_TenantBound):
    """API key records.

    The schema has no token column and no hash column, so no method here accepts
    one. Which representation a key has, and whether an organization-scoped service
    principal or a creator-derived model applies, are open decisions (O-1); this
    class only stores the fields the contract defines.

    The creator must be a member of the key's own organization: the provenance
    foreign key makes any other value a violation rather than a stored
    inconsistency.
    """

    async def create(
        self, key_id: str, created_by_user_id: str, name: str, *,
        key_prefix: str = "", scopes: Sequence[str] = (),
        expires_at: Optional[datetime] = None,
    ) -> APIKey:
        row = await self._session.fetchrow(
            "INSERT INTO api_keys (id, organization_id, created_by_user_id, name, "
            "key_prefix, scopes, status, expires_at, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, now()) "
            f"RETURNING {API_KEY_COLUMNS}",
            as_uuid(key_id, "APIKey.id"),
            as_uuid(self._tenant(), "organization_id"),
            as_uuid(created_by_user_id, "created_by_user_id"),
            name, optional_text(key_prefix), scopes_to_array(scopes), expires_at,
        )
        if row is None:  # pragma: no cover
            raise EntityNotFound("APIKey", key_id)
        return api_key_from_row(row)

    async def get(self, key_id: str) -> Optional[APIKey]:
        row = await self._session.fetchrow(
            f"SELECT {API_KEY_COLUMNS} FROM api_keys WHERE id = $1 AND "
            "organization_id = $2",
            as_uuid(key_id, "APIKey.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else api_key_from_row(row)

    async def list_for_current_tenant(self) -> Sequence[APIKey]:
        rows = await self._session.fetch(
            f"SELECT {API_KEY_COLUMNS} FROM api_keys WHERE organization_id = $1 "
            "ORDER BY created_at",
            as_uuid(self._tenant(), "organization_id"),
        )
        return [api_key_from_row(r) for r in rows]

    async def list_for_creator(self, user_id: str) -> Sequence[APIKey]:
        rows = await self._session.fetch(
            f"SELECT {API_KEY_COLUMNS} FROM api_keys WHERE organization_id = $1 "
            "AND created_by_user_id = $2 ORDER BY created_at",
            as_uuid(self._tenant(), "organization_id"),
            as_uuid(user_id, "created_by_user_id"),
        )
        return [api_key_from_row(r) for r in rows]

    async def revoke(self, key_id: str, revoked_at: datetime) -> APIKey:
        """Revoke a key and record when, in one statement.

        The domain contract refuses ``revoked`` without ``revoked_at`` and the
        reverse, so the two are set together; there is no method that could set one
        alone.
        """

        row = await self._session.fetchrow(
            "UPDATE api_keys SET status = 'revoked', revoked_at = $1, "
            "updated_at = now() WHERE id = $2 AND organization_id = $3 "
            f"RETURNING {API_KEY_COLUMNS}",
            revoked_at, as_uuid(key_id, "APIKey.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("APIKey", key_id)
        return api_key_from_row(row)

    async def set_expiry(self, key_id: str, expires_at: Optional[datetime]) -> APIKey:
        row = await self._session.fetchrow(
            "UPDATE api_keys SET expires_at = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id = $3 "
            f"RETURNING {API_KEY_COLUMNS}",
            expires_at, as_uuid(key_id, "APIKey.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("APIKey", key_id)
        return api_key_from_row(row)

    async def record_use(self, key_id: str, used_at: datetime) -> APIKey:
        """Record last use. A timestamp, not an authorization decision."""

        row = await self._session.fetchrow(
            "UPDATE api_keys SET last_used_at = $1, updated_at = now() "
            "WHERE id = $2 AND organization_id = $3 "
            f"RETURNING {API_KEY_COLUMNS}",
            used_at, as_uuid(key_id, "APIKey.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("APIKey", key_id)
        return api_key_from_row(row)


# =========================================================================== #
# run records
# =========================================================================== #
class PostgresRunRecordRepository(_TenantBound):
    """The outer lifecycle of a run.

    ``id`` is the Platform identity and ``core_run_id`` the Core correlation
    identity. They are different values, the schema refuses to let them collapse,
    and no method here accepts one where the other belongs.

    ``core_run_id`` is deliberately **not** unique (K-8): retry and resume
    semantics are not decided, so the lookup returns a row and a separate listing
    returns every match.
    """

    async def create_queued(
        self, run_id: str, project_id: str, *, task_id: str = "",
    ) -> RunRecord:
        """Insert a queued run.

        A queued run carries no ``core_run_id`` and no ``started_at``, because the
        schema forbids both, and no initiator, because queued is the one state in
        which an absent initiator is accepted.
        """

        row = await self._session.fetchrow(
            "INSERT INTO run_records (id, organization_id, project_id, task_id, "
            "status, attempt_count, failure_classification, created_at) "
            "VALUES ($1, $2, $3, $4, 'queued', 0, '', now()) "
            f"RETURNING {RUN_RECORD_COLUMNS}",
            as_uuid(run_id, "RunRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
            as_uuid(project_id, "RunRecord.project_id"),
            task_id,
        )
        if row is None:  # pragma: no cover
            raise EntityNotFound("RunRecord", run_id)
        return run_record_from_row(row)

    async def get(self, run_id: str) -> Optional[RunRecord]:
        row = await self._session.fetchrow(
            f"SELECT {RUN_RECORD_COLUMNS} FROM run_records WHERE id = $1 AND "
            "organization_id = $2",
            as_uuid(run_id, "RunRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else run_record_from_row(row)

    async def find_by_core_run_id(self, core_run_id: str) -> Optional[RunRecord]:
        row = await self._session.fetchrow(
            f"SELECT {RUN_RECORD_COLUMNS} FROM run_records WHERE core_run_id = $1 "
            "AND organization_id = $2 ORDER BY created_at LIMIT 1",
            core_run_id, as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else run_record_from_row(row)

    async def list_by_core_run_id(self, core_run_id: str) -> Sequence[RunRecord]:
        rows = await self._session.fetch(
            f"SELECT {RUN_RECORD_COLUMNS} FROM run_records WHERE core_run_id = $1 "
            "AND organization_id = $2 ORDER BY created_at",
            core_run_id, as_uuid(self._tenant(), "organization_id"),
        )
        return [run_record_from_row(r) for r in rows]

    async def list_by_task_id(self, task_id: str) -> Sequence[RunRecord]:
        rows = await self._session.fetch(
            f"SELECT {RUN_RECORD_COLUMNS} FROM run_records WHERE task_id = $1 AND "
            "organization_id = $2 ORDER BY created_at",
            task_id, as_uuid(self._tenant(), "organization_id"),
        )
        return [run_record_from_row(r) for r in rows]

    async def list_for_current_tenant(self) -> Sequence[RunRecord]:
        rows = await self._session.fetch(
            f"SELECT {RUN_RECORD_COLUMNS} FROM run_records WHERE organization_id = "
            "$1 ORDER BY created_at DESC",
            as_uuid(self._tenant(), "organization_id"),
        )
        return [run_record_from_row(r) for r in rows]

    async def claim(
        self, run_id: str, *, core_run_id: str, initiated_by_user_id: str,
        started_at: datetime,
    ) -> RunRecord:
        """Move a queued run to ``running`` and record its Core identity.

        This is the transition that crosses the Core boundary, so it sets
        ``core_run_id``, ``started_at``, and the initiator together: the schema
        requires all three on any non-queued state.

        **The ``status = 'queued'`` predicate is part of the UPDATE, not a separate
        read.** Two workers racing to claim the same run therefore cannot both
        succeed: the second statement matches no row and this raises
        :class:`EntityNotFound`, which the caller can treat as "already claimed".
        A check-then-update would let both pass the check.
        """

        row = await self._session.fetchrow(
            "UPDATE run_records SET status = 'running', core_run_id = $1, "
            "started_at = $2, initiated_by_user_id = $3, updated_at = now() "
            "WHERE id = $4 AND organization_id = $5 AND status = 'queued' "
            f"RETURNING {RUN_RECORD_COLUMNS}",
            core_run_id, started_at,
            as_uuid(initiated_by_user_id, "initiated_by_user_id"),
            as_uuid(run_id, "RunRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("RunRecord (claimable)", run_id)
        return run_record_from_row(row)

    #: Statuses a lifecycle mutation may not move a run away from. O-7 has not fixed
    #: which statuses are terminal, so this is deliberately the conservative set: a run
    #: that has finished is finished, and no ordinary transition may reopen it.
    TERMINAL_STATUSES = (
        RunRecordStatus.SUCCEEDED,
        RunRecordStatus.FAILED,
        RunRecordStatus.CANCELLED,
        RunRecordStatus.TIMED_OUT,
        RunRecordStatus.INTERRUPTED,
    )

    async def finish(
        self, run_id: str, *, status: RunRecordStatus, finished_at: datetime,
        failure_classification: str = "",
        expected_status: Optional[RunRecordStatus] = None,
    ) -> RunRecord:
        """Move a run to a terminal status, compare-and-set.

        **The guard is part of the UPDATE**, so two workers racing to finish one run
        cannot both succeed: the second statement matches no row and this raises
        :class:`~app.platform.persistence.errors.ConcurrentModificationError`. A
        read-then-update would let both pass the read.

        Two conditions are always applied, on top of the caller's expectation:

        * ``status <> 'queued'``, because a queued run has never been claimed and the
          schema requires a Core identity and an initiator before any other status;
        * ``status NOT IN (<terminal>)``, because a finished run must not be finished
          again by a later worker holding a stale view.

        ``expected_status`` narrows the guard further when the caller knows which
        state it observed. It is optional so existing callers keep working, and it
        does not fix O-7: this is a concurrency guard, not a lifecycle model.
        """

        if status not in self.TERMINAL_STATUSES:
            raise TransactionError(
                f"{status.value!r} is not a terminal status; use set_status for a "
                "transition that does not finish the run"
            )
        terminal = [s.value for s in self.TERMINAL_STATUSES]
        row = await self._session.fetchrow(
            "UPDATE run_records SET status = $1, finished_at = $2, "
            "failure_classification = $3, updated_at = now() "
            "WHERE id = $4 AND organization_id = $5 "
            "AND status <> 'queued' AND NOT (status = ANY($6::text[])) "
            "AND ($7::text IS NULL OR status = $7) "
            f"RETURNING {RUN_RECORD_COLUMNS}",
            status.value, finished_at, failure_classification,
            as_uuid(run_id, "RunRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
            terminal,
            None if expected_status is None else expected_status.value,
        )
        if row is None:
            raise ConcurrentModificationError(
                "RunRecord", run_id,
                f"finish to {status.value!r} matched no row: the run is absent, "
                "already finished, still queued, or in a state other than the "
                "expected one",
            )
        return run_record_from_row(row)

    async def set_status(
        self, run_id: str, status: RunRecordStatus, *,
        failure_classification: str = "",
        expected_status: Optional[RunRecordStatus] = None,
    ) -> RunRecord:
        """Change status without finishing, compare-and-set.

        The ``NOT (status = ANY(<terminal>))`` condition is what stops a stale worker
        from resurrecting a finished run: ``running`` is reachable only from a state
        that has not finished. ``expected_status`` narrows the guard when the caller
        knows the state it observed.

        The status vocabulary is whatever the domain enum holds; no new lifecycle
        state is introduced here, and **O-7 remains open**.
        """

        terminal = [s.value for s in self.TERMINAL_STATUSES]
        row = await self._session.fetchrow(
            "UPDATE run_records SET status = $1, failure_classification = $2, "
            "updated_at = now() WHERE id = $3 AND organization_id = $4 "
            "AND NOT (status = ANY($5::text[])) "
            "AND ($6::text IS NULL OR status = $6) "
            f"RETURNING {RUN_RECORD_COLUMNS}",
            status.value, failure_classification,
            as_uuid(run_id, "RunRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
            terminal,
            None if expected_status is None else expected_status.value,
        )
        if row is None:
            raise ConcurrentModificationError(
                "RunRecord", run_id,
                f"transition to {status.value!r} matched no row: the run is absent, "
                "already finished, or in a state other than the expected one",
            )
        return run_record_from_row(row)

    async def increment_attempt_count(self, run_id: str) -> RunRecord:
        """Record one more attempt in a single statement.

        ``attempt_count = attempt_count + 1`` is evaluated by the database, so
        concurrent increments cannot lose an update the way read-modify-write
        would.
        """

        row = await self._session.fetchrow(
            "UPDATE run_records SET attempt_count = attempt_count + 1, "
            "updated_at = now() WHERE id = $1 AND organization_id = $2 "
            f"RETURNING {RUN_RECORD_COLUMNS}",
            as_uuid(run_id, "RunRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        if row is None:
            raise EntityNotFound("RunRecord", run_id)
        return run_record_from_row(row)


# =========================================================================== #
# usage records
# =========================================================================== #
class PostgresUsageRecordRepository(_TenantBound):
    """Physical usage observations. APPEND-ONLY.

    There is no update method and no delete method, and there never should be: the
    table's privileges withhold ``UPDATE`` and ``DELETE`` and no policy grants
    them, so an attempt would be refused by the database even if a method existed.

    No field carries money. There is no cost, price, charge, balance, or currency
    here, and none may be added: this is a measurement, not an invoice.
    """

    async def append(self, usage: UsageRecord) -> UsageRecord:
        """Insert one observation.

        A duplicate ``(run_record_id, attempt_number)`` is a
        :class:`~app.platform.persistence.errors.UniqueViolationError`, not a
        silent overwrite. The database is the authority on that, not a pre-check.
        """

        self._require_matching_tenant(usage.organization_id)
        await self._session.execute(
            "INSERT INTO usage_records (id, organization_id, run_record_id, "
            "core_run_id, attempt_number, provider_name, model_name, input_tokens, "
            "output_tokens, cached_tokens, duration_seconds, success, fallback, "
            "tool_call_count, completed_at, error_type, created_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, "
            "$15, $16, $17)",
            as_uuid(usage.id, "UsageRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
            as_uuid(usage.run_record_id, "UsageRecord.run_record_id"),
            usage.core_run_id,
            usage.attempt_number,
            usage.provider_name,
            usage.model_name,
            usage.input_tokens,
            usage.output_tokens,
            usage.cached_tokens,
            usage.duration_seconds,
            usage.success,
            usage.fallback,
            usage.tool_call_count,
            usage.completed_at,
            usage.error_type,
            usage.created_at,
        )
        created = await self.get(usage.id)
        if created is None:
            raise EntityNotFound("UsageRecord", usage.id)
        return created

    async def get(self, usage_id: str) -> Optional[UsageRecord]:
        row = await self._session.fetchrow(
            f"SELECT {USAGE_RECORD_COLUMNS} FROM usage_records WHERE id = $1 AND "
            "organization_id = $2",
            as_uuid(usage_id, "UsageRecord.id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return None if row is None else usage_record_from_row(row)

    async def list_for_run(self, run_record_id: str) -> Sequence[UsageRecord]:
        rows = await self._session.fetch(
            f"SELECT {USAGE_RECORD_COLUMNS} FROM usage_records WHERE "
            "run_record_id = $1 AND organization_id = $2 ORDER BY attempt_number",
            as_uuid(run_record_id, "UsageRecord.run_record_id"),
            as_uuid(self._tenant(), "organization_id"),
        )
        return [usage_record_from_row(r) for r in rows]

    async def list_for_current_tenant(self) -> Sequence[UsageRecord]:
        rows = await self._session.fetch(
            f"SELECT {USAGE_RECORD_COLUMNS} FROM usage_records WHERE "
            "organization_id = $1 ORDER BY created_at",
            as_uuid(self._tenant(), "organization_id"),
        )
        return [usage_record_from_row(r) for r in rows]
