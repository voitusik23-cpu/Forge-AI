"""Closed status vocabularies for the Platform domain contracts.

These enums are **Stage 1 / Step 1 domain contracts** only. They describe the
lifecycle a future persistence layer must be able to store; they implement no
persistence, no transition engine, and no authorization.

Two of the values are deliberately provisional:

* :class:`RunRecordStatus` implements the lifecycle semantics the Stage 1
  Architecture Contract requires (section 21, R-6) but **the final status enum
  remains an open decision (O-7)**. Adding or renaming a member is a decision
  record, not a silent edit.
* :class:`APIKeyStatus` deliberately says nothing about the API key authority
  model, which stays open between an organization-scoped service principal and a
  creator-membership-derived credential (section 21, R-5).

A status is **never** authorization. ``ACTIVE`` means a record is not retired; it
does not grant a permission, and it never reaches the Core execution authority
chain.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "UserStatus",
    "OrganizationStatus",
    "MembershipRole",
    "MembershipStatus",
    "ProjectStatus",
    "ProviderAccountStatus",
    "APIKeyStatus",
    "RunRecordStatus",
]


class _DomainEnum(str, Enum):
    """Base for domain vocabularies so values serialize as plain strings."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


class UserStatus(_DomainEnum):
    """Whether a user identity may authenticate.

    ``SUSPENDED`` is not a deletion: the identity still exists and its
    memberships still exist. Whether a suspended user's credentials are refused
    is an authentication decision for a later step.
    """

    ACTIVE = "active"
    SUSPENDED = "suspended"


class OrganizationStatus(_DomainEnum):
    """Whether a tenant is operational."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class MembershipRole(_DomainEnum):
    """The role a membership carries inside one organization.

    This vocabulary exists because the Stage 1 Architecture Contract already
    names these four roles. It is **not** an RBAC engine: nothing here maps a
    role to a filesystem path, a command, a tool, or a network permission. A role
    is a statement about a human's standing in a tenant, and the mapping from a
    role to an allowed action belongs to the authorization step, not to this
    contract.
    """

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    BILLING = "billing"


class MembershipStatus(_DomainEnum):
    """Whether a membership currently authorizes anything.

    ``REVOKED`` and ``INACTIVE`` are deliberately distinct: a revoked membership
    was withdrawn, an inactive one is merely not currently effective. Neither is
    an active authorization, and both must be excluded by any future
    authorization check.
    """

    ACTIVE = "active"
    INACTIVE = "inactive"
    REVOKED = "revoked"


# --------------------------------------------------------------------------- #
# tenancy-scoped resources
# --------------------------------------------------------------------------- #


class ProjectStatus(_DomainEnum):
    """Lifecycle of a project inside a tenant."""

    ACTIVE = "active"
    ARCHIVED = "archived"


class ProviderAccountStatus(_DomainEnum):
    """Whether a provider credential reference may be used for a run."""

    ACTIVE = "active"
    DISABLED = "disabled"


class APIKeyStatus(_DomainEnum):
    """Whether an API key may be presented for authentication.

    ``REVOKED`` is terminal for the key. This enum takes no position on whether
    the key's authority derives from the organization or from the creator's
    membership; that model is still open (R-5).
    """

    ACTIVE = "active"
    REVOKED = "revoked"


# --------------------------------------------------------------------------- #
# run lifecycle
# --------------------------------------------------------------------------- #


class RunRecordStatus(_DomainEnum):
    """The external lifecycle of a Platform run.

    Required semantics come from the Stage 1 Architecture Contract (R-6): a
    record must be able to express normal success, normal failure, cancellation,
    timeout, and execution interruption / crash / orphan.

    **O-7 remains open.** This member set is provisional and exists so the model
    cannot fail to express those outcomes; it is not the final status enum, and
    it implies no transition rules, no terminality, and no resumption. In
    particular nothing here says a run may be resumed, and nothing here is
    authority.

    A worker loss is represented by ``INTERRUPTED``. The mechanism that detects
    it — lease, heartbeat, timeout, or reconciliation — is deliberately not
    chosen by this contract.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"
