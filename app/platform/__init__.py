"""Platform domain contracts (Stage 1 / Step 1).

This package holds the pure domain contracts the future Platform persistence layer
will be written against. It contains **no** persistence, ORM, migration,
authentication, session, authorization engine, credential resolution, API
endpoint, transport, or billing code.

Boundary rules (Stage 1 Architecture Contract sections 1 and 11):

* Core must never import this package — nothing under ``app/`` outside
  ``app/platform/`` may depend on it;
* this package imports only the standard library;
* an identifier is a reference, never proof of authority;
* every tenant-owned record carries an explicit ``organization_id``;
* no record here holds credential material or money.
"""

from __future__ import annotations

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
    DomainContractError,
    Membership,
    Organization,
    Project,
    ProviderAccount,
    RunRecord,
    UsageRecord,
    User,
    new_id,
    utc_now,
)

__all__ = [
    # domain records
    "User",
    "Organization",
    "Membership",
    "Project",
    "ProviderAccount",
    "APIKey",
    "RunRecord",
    "UsageRecord",
    # vocabularies
    "UserStatus",
    "OrganizationStatus",
    "MembershipRole",
    "MembershipStatus",
    "ProjectStatus",
    "ProviderAccountStatus",
    "APIKeyStatus",
    "RunRecordStatus",
    # helpers
    "DomainContractError",
    "new_id",
    "utc_now",
]
