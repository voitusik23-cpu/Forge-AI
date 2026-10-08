"""Platform domain contracts (Stage 1 / Step 1).

These are **pure stdlib domain contracts**. They contain no persistence, no
repository, no ORM, no migration, no authentication, no session, no authorization
engine, no credential resolution, no transport, and no billing. They exist so the
future Platform persistence layer has one agreed shape to be written against.

Architectural position (Stage 1 Architecture Contract sections 1 and 11):

* the Platform domain is a **sibling** of Core, not a part of it;
* **Core must never import this package** — nothing under ``app/`` outside
  ``app/platform/`` may depend on it;
* this package imports only the standard library, and never a database driver, an
  ORM, a migration tool, or a payment library;
* an identifier is a **reference, never proof of authority**. A ``User.id``, an
  ``Organization.id``, a ``Project.id``, a ``RunRecord.id``, or an ``APIKey.id``
  grants nothing. Authority is derived at the server from an authenticated
  subject through a membership to an organization to a project, and even then it
  bounds a request rather than authorizing an effect.

Tenant containment is an application-level invariant, not only a future database
feature (R-3): every tenant-owned record carries an explicit ``organization_id``.

Secret safety: no record here holds credential material. ``ProviderAccount``
stores an opaque ``secret_ref`` and refuses to reveal it in ``repr``;
``APIKey`` stores no token and no hash.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import re
from typing import Mapping, Optional
from uuid import uuid4

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

__all__ = [
    "User",
    "Organization",
    "Membership",
    "Project",
    "ProviderAccount",
    "APIKey",
    "RunRecord",
    "UsageRecord",
    "DomainContractError",
    "new_id",
    "utc_now",
]


class DomainContractError(ValueError):
    """A domain contract was constructed with a value it cannot represent.

    A contract violation is a programming error at the boundary that built the
    record, so it is raised rather than stored.
    """


# --------------------------------------------------------------------------- #
# identifiers and timestamps
# --------------------------------------------------------------------------- #

# Identifiers are opaque strings. The concrete generation strategy is not frozen
# by the Stage 1 contract (O-1, and the identity decisions it gates), so this
# module deliberately does not define its own UUID type: it uses the standard
# library's UUIDv4 as the current default and accepts any non-empty string.
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def new_id() -> str:
    """Return a fresh opaque identifier.

    The value is a UUIDv4 string. This is a convenient default, not a frozen
    format: every contract accepts any non-empty identifier string, so a future
    decision can change how identifiers are minted without changing the domain.
    """
    return str(uuid4())


def utc_now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime.

    Timestamps in this package are timezone-aware ``datetime`` values, never
    floats and never naive. A naive datetime is refused at construction because it
    is ambiguous, and an ambiguous timestamp is not a durable record.
    """
    return datetime.now(timezone.utc)


def _require_text(value: object, label: str, *, max_length: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DomainContractError(f"{label} must be a non-empty string")
    if len(value) > max_length:
        raise DomainContractError(f"{label} must be at most {max_length} characters")
    return value


def _require_identifier(value: object, label: str) -> str:
    text = _require_text(value, label, max_length=128)
    if not _ID_PATTERN.match(text):
        raise DomainContractError(f"{label} must be an opaque identifier")
    return text


def _require_optional_identifier(value: object, label: str) -> Optional[str]:
    if value is None:
        return None
    return _require_identifier(value, label)


def _require_aware(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise DomainContractError(f"{label} must be a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DomainContractError(f"{label} must be timezone-aware")
    return value


def _require_optional_aware(value: object, label: str) -> Optional[datetime]:
    if value is None:
        return None
    return _require_aware(value, label)


def _require_enum(value: object, label: str, enum_type: type) -> object:
    if not isinstance(value, enum_type):
        raise DomainContractError(f"{label} must be a {enum_type.__name__}")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise DomainContractError(f"{label} must be a boolean")
    return value


def _require_count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise DomainContractError(f"{label} must be an integer")
    if value < 0:
        raise DomainContractError(f"{label} must be non-negative")
    return value


# --------------------------------------------------------------------------- #
# identity entities
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class User:
    """An authenticatable natural person.

    A user is a global identity, not a tenant: it owns nothing. The fields here
    are the contract's minimum. In particular there is **no** password, hash,
    session, external identity reference, balance, wallet, or billing state: the
    authentication mechanism is an open decision (O-2), and ``User.id`` is a
    reference rather than authorization.
    """

    id: str
    email: str
    created_at: datetime
    status: UserStatus = UserStatus.ACTIVE
    display_name: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.id, "User.id")
        _require_text(self.email, "User.email", max_length=320)
        if not _EMAIL_PATTERN.match(self.email):
            raise DomainContractError("User.email must look like an email address")
        _require_aware(self.created_at, "User.created_at")
        _require_enum(self.status, "User.status", UserStatus)
        if self.display_name:
            _require_text(self.display_name, "User.display_name", max_length=200)

    @classmethod
    def create(cls, *, email: str, display_name: str = "") -> "User":
        """Build an active user with a fresh identifier and the current time."""
        return cls(
            id=new_id(),
            email=email,
            created_at=utc_now(),
            display_name=display_name,
        )


@dataclass(frozen=True)
class Organization:
    """A tenant: the boundary of isolation, ownership, and (future) billing.

    ``is_personal`` marks the organization created alongside a single user at
    registration. It is a domain attribute only: it changes no behavior here, it
    grants no authority, and its lifecycle is not invented by this contract. A
    personal organization is a full tenant, so a project and a run inside it are
    owned exactly as in any other organization.

    No wallet, balance, payment-customer state, provider cost, or execution
    authority belongs here: money is a later stage and execution authority lives
    in Core.
    """

    id: str
    name: str
    created_at: datetime
    is_personal: bool = False
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    slug: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.id, "Organization.id")
        _require_text(self.name, "Organization.name", max_length=200)
        _require_aware(self.created_at, "Organization.created_at")
        _require_bool(self.is_personal, "Organization.is_personal")
        _require_enum(self.status, "Organization.status", OrganizationStatus)
        if self.slug:
            _require_text(self.slug, "Organization.slug", max_length=200)

    @classmethod
    def create(cls, *, name: str, is_personal: bool = False, slug: str = "") -> "Organization":
        """Build an active organization with a fresh identifier."""
        return cls(
            id=new_id(),
            name=name,
            created_at=utc_now(),
            is_personal=is_personal,
            slug=slug,
        )


@dataclass(frozen=True)
class Membership:
    """The authorization link between one user and one organization.

    This record is the **only** path from an authentication subject to
    tenant-scoped authority. It is also the reason a suspended membership means
    "no active authorization" rather than "some authority": any future
    authorization step must require an ``ACTIVE`` membership, so a revoked or
    inactive membership authorizes nothing.

    A role states a human's standing in the tenant. It is **not** a filesystem
    path, a command, a tool, or a network permission, and it never becomes Core
    execution authority.
    """

    id: str
    organization_id: str
    user_id: str
    role: MembershipRole
    created_at: datetime
    status: MembershipStatus = MembershipStatus.ACTIVE
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "Membership.id")
        _require_identifier(self.organization_id, "Membership.organization_id")
        _require_identifier(self.user_id, "Membership.user_id")
        _require_enum(self.role, "Membership.role", MembershipRole)
        _require_enum(self.status, "Membership.status", MembershipStatus)
        _require_aware(self.created_at, "Membership.created_at")
        _require_optional_aware(self.updated_at, "Membership.updated_at")

    @property
    def is_active(self) -> bool:
        """Whether this membership currently authorizes anything.

        A convenience predicate over the recorded status. It is not a permission
        check and it does not consult any other record.
        """
        return self.status is MembershipStatus.ACTIVE

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        user_id: str,
        role: MembershipRole,
    ) -> "Membership":
        """Build an active membership with a fresh identifier."""
        return cls(
            id=new_id(),
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            created_at=utc_now(),
        )


# --------------------------------------------------------------------------- #
# tenant-scoped resources
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Project:
    """A unit of work inside one organization.

    ``organization_id`` is required: a project is owned by a tenant and never by
    a user directly, so a user leaving an organization does not move or orphan
    the organization's projects.

    ``workspace_ref`` is an **opaque, server-derived** reference to the resource
    root the project will execute against. It is deliberately not a filesystem
    path: the architecture forbids accepting a client-chosen path as authority,
    fixes no topology (O-5), and keeps Core cross-platform. This field is a
    reference the server will resolve, never a permission and never a path a
    caller may set to influence where a run happens.

    A project grants no execution authority. Ownership of a project is not
    authorization to execute anything.
    """

    id: str
    organization_id: str
    name: str
    created_at: datetime
    status: ProjectStatus = ProjectStatus.ACTIVE
    slug: str = ""
    workspace_ref: str = ""
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "Project.id")
        _require_identifier(self.organization_id, "Project.organization_id")
        _require_text(self.name, "Project.name", max_length=200)
        _require_aware(self.created_at, "Project.created_at")
        _require_enum(self.status, "Project.status", ProjectStatus)
        if self.slug:
            _require_text(self.slug, "Project.slug", max_length=200)
        if self.workspace_ref:
            _require_text(self.workspace_ref, "Project.workspace_ref", max_length=256)
        _require_optional_aware(self.updated_at, "Project.updated_at")

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        name: str,
        slug: str = "",
        workspace_ref: str = "",
    ) -> "Project":
        """Build an active project with a fresh identifier."""
        return cls(
            id=new_id(),
            organization_id=organization_id,
            name=name,
            created_at=utc_now(),
            slug=slug,
            workspace_ref=workspace_ref,
        )


@dataclass(frozen=True)
class ProviderAccount:
    """A tenant's reference to a provider credential.

    The record holds an **opaque** ``secret_ref`` and never credential material:
    no plaintext key, no ciphertext, no token, no hash, no parsed reference
    grammar, and no resolution logic. Resolving a reference is a later step
    (O-3, O-4) behind an abstract port; nothing here reads, decrypts, or
    validates the reference beyond requiring it to be a non-empty string.

    ``repr`` is overridden so the reference is not echoed through logs or
    tracebacks: the default dataclass repr would print the value, and a secret
    reference must be treated as sensitive even though it is not itself a secret.

    ``organization_id`` may be ``None`` for a **system-owned** account, which is
    how a Forge-managed provider reference is expected to be represented. BYOK
    versus managed is a Platform/Billing distinction; no wholesale price, cost,
    or margin belongs here.
    """

    id: str
    provider_name: str
    created_at: datetime
    secret_ref: str = field(repr=False)
    organization_id: Optional[str] = None
    status: ProviderAccountStatus = ProviderAccountStatus.ACTIVE
    updated_at: Optional[datetime] = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_identifier(self.id, "ProviderAccount.id")
        _require_optional_identifier(
            self.organization_id, "ProviderAccount.organization_id"
        )
        _require_text(self.provider_name, "ProviderAccount.provider_name", max_length=100)
        # The reference is opaque: only non-emptiness is checked.
        _require_text(self.secret_ref, "ProviderAccount.secret_ref", max_length=512)
        _require_aware(self.created_at, "ProviderAccount.created_at")
        _require_enum(self.status, "ProviderAccount.status", ProviderAccountStatus)
        _require_optional_aware(self.updated_at, "ProviderAccount.updated_at")
        if not isinstance(self.metadata, Mapping):
            raise DomainContractError("ProviderAccount.metadata must be a mapping")

    def __repr__(self) -> str:
        """Represent the record without disclosing the secret reference."""
        return (
            f"ProviderAccount(id={self.id!r}, "
            f"organization_id={self.organization_id!r}, "
            f"provider_name={self.provider_name!r}, "
            f"secret_ref=<opaque>, status={self.status.value!r})"
        )

    @classmethod
    def create(
        cls,
        *,
        provider_name: str,
        secret_ref: str,
        organization_id: Optional[str] = None,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> "ProviderAccount":
        """Build an active provider account with a fresh identifier."""
        return cls(
            id=new_id(),
            organization_id=organization_id,
            provider_name=provider_name,
            secret_ref=secret_ref,
            created_at=utc_now(),
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class APIKey:
    """A programmatic credential belonging to one organization.

    The contract deliberately stores **no token and no hash**: the plaintext key
    is shown once at creation and only a safe representation is persisted, but
    the safe representation's algorithm is an open decision (O-1), so this record
    has no field for it yet. ``key_prefix`` is optional identification metadata
    whose format is equally unfrozen; it must never be a usable credential.

    Authority semantics, stated here because they are invariants rather than
    implementation:

    * an API key is **never** filesystem or workspace authority;
    * an API key can **never** bypass membership, organization, or project
      authorization — it authenticates a caller, and authority is still derived
      from an authenticated subject through a membership;
    * revocation must be explicit and effective. The API key authority model
      remains open between an organization-scoped service principal and a
      creator-membership-derived credential (R-5), and the forbidden state is
      fixed: a key must not silently keep authority after the authorization
      subject it depends on stops being valid. ``created_by_user_id`` is recorded
      so that either model can be implemented without changing this contract.
    """

    id: str
    organization_id: str
    created_by_user_id: str
    name: str
    created_at: datetime
    status: APIKeyStatus = APIKeyStatus.ACTIVE
    key_prefix: str = ""
    scopes: tuple[str, ...] = ()
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "APIKey.id")
        _require_identifier(self.organization_id, "APIKey.organization_id")
        _require_identifier(self.created_by_user_id, "APIKey.created_by_user_id")
        _require_text(self.name, "APIKey.name", max_length=200)
        _require_aware(self.created_at, "APIKey.created_at")
        _require_enum(self.status, "APIKey.status", APIKeyStatus)
        if self.key_prefix:
            _require_text(self.key_prefix, "APIKey.key_prefix", max_length=64)
        if not isinstance(self.scopes, tuple):
            raise DomainContractError("APIKey.scopes must be a tuple of strings")
        for scope in self.scopes:
            _require_text(scope, "APIKey.scopes entry", max_length=100)
        _require_optional_aware(self.expires_at, "APIKey.expires_at")
        _require_optional_aware(self.revoked_at, "APIKey.revoked_at")
        _require_optional_aware(self.last_used_at, "APIKey.last_used_at")
        _require_optional_aware(self.updated_at, "APIKey.updated_at")
        if self.status is APIKeyStatus.REVOKED and self.revoked_at is None:
            raise DomainContractError("a revoked API key must record revoked_at")
        if self.revoked_at is not None and self.status is not APIKeyStatus.REVOKED:
            raise DomainContractError("revoked_at requires the REVOKED status")

    @property
    def is_usable(self) -> bool:
        """Whether the recorded key state is still presentable.

        This reads two fields and nothing else. It is not an authorization check:
        presenting a usable key still authorizes nothing until a membership
        resolves, and expiry handling against the current time is a decision for
        the authentication step.
        """
        return self.status is APIKeyStatus.ACTIVE

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        created_by_user_id: str,
        name: str,
        scopes: tuple[str, ...] = (),
        expires_at: Optional[datetime] = None,
    ) -> "APIKey":
        """Build an active API key record with a fresh identifier.

        No token is generated and no hash is computed: both are later steps.
        """
        return cls(
            id=new_id(),
            organization_id=organization_id,
            created_by_user_id=created_by_user_id,
            name=name,
            created_at=utc_now(),
            scopes=tuple(scopes),
            expires_at=expires_at,
        )


# --------------------------------------------------------------------------- #
# run lifecycle
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RunRecord:
    """The durable **external** lifecycle of one run, owned by a project.

    Two distinct identities live here and must never be conflated:

    * ``id`` is the **Platform** run identity — the primary key of this record.
    * ``core_run_id`` is the **Core** execution correlation identity, produced by
      Core and carried in ``PhysicalTelemetry.run_id``. It is unknown until Core
      actually starts the attempt, so it is optional while the record is queued
      and required once the run has started.

    This record is **not** Core authorization, **not** Core execution authority,
    **not** a replacement for the Core runtime run, and **not** a replacement for
    the Core ``RunStore``. It records the outer lifecycle Platform owns; it does
    not describe phases, decisions, tools, or workspace activity, and nothing here
    may be read by Core to decide what a run may do.

    The status member set is provisional and O-7 remains open (see
    :class:`~app.platform.enums.RunRecordStatus`). What is fixed is the
    *requirement* that the model can express queued, running, succeeded, failed,
    cancelled, timed out, and interrupted/orphaned — and that a lost worker is
    representable, so a record cannot be stuck in ``RUNNING`` by design.
    """

    id: str
    organization_id: str
    project_id: str
    created_at: datetime
    status: RunRecordStatus = RunRecordStatus.QUEUED
    core_run_id: Optional[str] = None
    initiated_by_user_id: Optional[str] = None
    task_id: str = ""
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    attempt_count: int = 0
    failure_classification: str = ""
    updated_at: Optional[datetime] = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, "RunRecord.id")
        _require_identifier(self.organization_id, "RunRecord.organization_id")
        _require_identifier(self.project_id, "RunRecord.project_id")
        _require_aware(self.created_at, "RunRecord.created_at")
        _require_enum(self.status, "RunRecord.status", RunRecordStatus)
        _require_optional_identifier(self.core_run_id, "RunRecord.core_run_id")
        _require_optional_identifier(
            self.initiated_by_user_id, "RunRecord.initiated_by_user_id"
        )
        _require_optional_aware(self.started_at, "RunRecord.started_at")
        _require_optional_aware(self.finished_at, "RunRecord.finished_at")
        _require_count(self.attempt_count, "RunRecord.attempt_count")
        if self.task_id:
            _require_text(self.task_id, "RunRecord.task_id", max_length=128)
        if self.failure_classification:
            _require_text(
                self.failure_classification,
                "RunRecord.failure_classification",
                max_length=100,
            )
        _require_optional_aware(self.updated_at, "RunRecord.updated_at")

        # Two identities, never one. A correlation id equal to the record id
        # would silently collapse them, so it is refused rather than stored.
        if self.core_run_id is not None and self.core_run_id == self.id:
            raise DomainContractError(
                "RunRecord.core_run_id must be distinct from RunRecord.id"
            )
        if self.status is RunRecordStatus.QUEUED:
            if self.core_run_id is not None or self.started_at is not None:
                raise DomainContractError(
                    "a queued run has no core_run_id and has not started"
                )
        else:
            # Every non-queued state means Core was reached, so both the
            # correlation identity and the start time must be present for the
            # record to be able to describe what happened.
            if self.core_run_id is None:
                raise DomainContractError(
                    f"a {self.status.value} run must record core_run_id"
                )
            if self.started_at is None:
                raise DomainContractError(
                    f"a {self.status.value} run must record started_at"
                )
        if self.finished_at is not None and self.started_at is None:
            raise DomainContractError("a finished run must record started_at")
        if self.failure_classification and self.status is RunRecordStatus.SUCCEEDED:
            raise DomainContractError("a succeeded run cannot carry a failure")

    @property
    def is_terminal(self) -> bool:
        """Whether the recorded lifecycle has stopped.

        Based on the provisional member set only. It encodes no transition rules
        and no resumption: O-7 remains open.
        """
        return self.status in _TERMINAL_RUN_STATUSES

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        project_id: str,
        initiated_by_user_id: Optional[str] = None,
        task_id: str = "",
    ) -> "RunRecord":
        """Build a queued run record with a fresh Platform identity."""
        return cls(
            id=new_id(),
            organization_id=organization_id,
            project_id=project_id,
            created_at=utc_now(),
            initiated_by_user_id=initiated_by_user_id,
            task_id=task_id,
        )


_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunRecordStatus.SUCCEEDED,
        RunRecordStatus.FAILED,
        RunRecordStatus.CANCELLED,
        RunRecordStatus.TIMED_OUT,
        RunRecordStatus.INTERRUPTED,
    }
)


# --------------------------------------------------------------------------- #
# physical usage
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UsageRecord:
    """A Platform-side physical usage observation for one execution attempt.

    Three identities are carried separately and an ambiguous single ``run_id`` is
    deliberately **absent**:

    * ``run_record_id`` — the Platform ``RunRecord.id`` this measurement belongs to;
    * ``core_run_id`` — the Core correlation identity from ``PhysicalTelemetry``;
    * ``attempt_number`` — the physical attempt the measurement describes.

    The architecture contract fixes ``UNIQUE(run_record_id, attempt_number)``
    (R-1). No database constraint is implemented here and none can be, but the
    model does not contradict it: one run record plus one attempt number
    identifies one measurement.

    **This record contains no money.** There is no price, cost, charge, wallet,
    balance, margin, or revenue field, and none may be added without a separate
    billing decision. ``UsageRecord`` is a measurement, not a financial record,
    and it must never become a ``CostRecord`` inside Core.
    """

    id: str
    organization_id: str
    run_record_id: str
    core_run_id: str
    attempt_number: int
    created_at: datetime
    provider_name: str = ""
    model_name: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    duration_seconds: float = 0.0
    success: bool = False
    fallback: bool = False
    tool_call_count: int = 0
    completed_at: Optional[datetime] = None
    error_type: str = ""

    def __post_init__(self) -> None:
        _require_identifier(self.id, "UsageRecord.id")
        _require_identifier(self.organization_id, "UsageRecord.organization_id")
        _require_identifier(self.run_record_id, "UsageRecord.run_record_id")
        _require_identifier(self.core_run_id, "UsageRecord.core_run_id")
        _require_count(self.attempt_number, "UsageRecord.attempt_number")
        _require_aware(self.created_at, "UsageRecord.created_at")
        if self.provider_name:
            _require_text(self.provider_name, "UsageRecord.provider_name", max_length=100)
        if self.model_name:
            _require_text(self.model_name, "UsageRecord.model_name", max_length=200)
        for label, value in (
            ("UsageRecord.input_tokens", self.input_tokens),
            ("UsageRecord.output_tokens", self.output_tokens),
            ("UsageRecord.cached_tokens", self.cached_tokens),
            ("UsageRecord.tool_call_count", self.tool_call_count),
        ):
            _require_count(value, label)
        _require_bool(self.success, "UsageRecord.success")
        _require_bool(self.fallback, "UsageRecord.fallback")
        _require_optional_aware(self.completed_at, "UsageRecord.completed_at")
        if self.error_type:
            _require_text(self.error_type, "UsageRecord.error_type", max_length=100)
        if isinstance(self.duration_seconds, bool) or not isinstance(
            self.duration_seconds, (int, float)
        ):
            raise DomainContractError("UsageRecord.duration_seconds must be a number")
        if not math.isfinite(self.duration_seconds):
            raise DomainContractError(
                "UsageRecord.duration_seconds must be a finite number"
            )
        if self.duration_seconds < 0:
            raise DomainContractError(
                "UsageRecord.duration_seconds must be non-negative"
            )

    @property
    def total_tokens(self) -> int:
        """Total tokens measured for this attempt."""
        return self.input_tokens + self.output_tokens

    @classmethod
    def create(
        cls,
        *,
        organization_id: str,
        run_record_id: str,
        core_run_id: str,
        attempt_number: int,
        provider_name: str = "",
        model_name: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        duration_seconds: float = 0.0,
        success: bool = False,
        fallback: bool = False,
        tool_call_count: int = 0,
        completed_at: Optional[datetime] = None,
        error_type: str = "",
    ) -> "UsageRecord":
        """Build a usage record with a fresh identifier."""
        return cls(
            id=new_id(),
            organization_id=organization_id,
            run_record_id=run_record_id,
            core_run_id=core_run_id,
            attempt_number=attempt_number,
            provider_name=provider_name,
            model_name=model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            duration_seconds=duration_seconds,
            success=success,
            fallback=fallback,
            tool_call_count=tool_call_count,
            completed_at=completed_at,
            error_type=error_type,
            created_at=utc_now(),
        )
