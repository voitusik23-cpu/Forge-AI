"""The row <-> domain boundary.

This module is the ONLY place that knows both a domain record and a physical
column. It is deliberately boring: no business rules, no authorization, no
transactions. It answers two questions:

* given a database row, what domain record does it represent?
* given a domain record, what SQL parameters represent it?

Keeping that in one module is what stops the domain contracts from drifting
towards ORM models. ``app/platform/models.py`` does not import this module, this
module imports nothing from a driver, and neither knows about the other's
storage concerns.

**Identifiers.** The domain contract accepts any opaque non-empty string; the
schema stores ``uuid``. :func:`as_uuid` is the validation boundary: it converts an
opaque identifier to the value PostgreSQL accepts, or raises
:class:`InvalidIdentifierError` naming the field. Nothing here sends an
unvalidated identifier into SQL.

**Nullability.** The domain uses ``''`` for "absent" in several optional text and
timestamp positions (``slug``, ``key_prefix``, ``workspace_ref``) and ``None`` in
others. The mapping is explicit in both directions rather than assumed:

===========================  ==================  ====================
domain                       storage             rule
===========================  ==================  ====================
``str`` default ``''``       ``NULL``            empty means absent
``Optional[str] = None``     ``NULL``            None means absent
``tuple[str, ...] = ()``     ``text[] NOT NULL`` empty array
``Mapping = {}``             ``jsonb NOT NULL``  must be an object
===========================  ==================  ====================
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional, Sequence
from uuid import UUID

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
from app.platform.persistence.errors import InvalidIdentifierError

__all__ = [
    "as_uuid",
    "optional_uuid",
    "text_or_empty",
    "optional_text",
    "uuid_to_text",
    "user_from_row",
    "organization_from_row",
    "membership_from_row",
    "project_from_row",
    "provider_account_from_row",
    "api_key_from_row",
    "run_record_from_row",
    "usage_record_from_row",
]


# --------------------------------------------------------------------------- #
# identifier boundary
# --------------------------------------------------------------------------- #
def as_uuid(value: object, label: str) -> UUID:
    """Return the UUID a domain identifier represents, or raise.

    Accepts a ``UUID`` or its canonical string form. Anything else -- including a
    string the domain would happily accept as opaque, such as ``'abc.def:ghi'`` --
    is refused here, before SQL is built. That is the point: the domain keeps its
    opaque-identifier contract and storage keeps ``uuid``, and the disagreement
    surfaces as a named persistence error rather than a driver cast failure.
    """

    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except (ValueError, AttributeError, TypeError) as exc:
            raise InvalidIdentifierError(label, value) from exc
    raise InvalidIdentifierError(label, value)


def optional_uuid(value: object, label: str) -> Optional[UUID]:
    """Like :func:`as_uuid`, but ``None`` and ``''`` mean absent."""

    if value is None or value == "":
        return None
    return as_uuid(value, label)


def uuid_to_text(value: object, label: str) -> str:
    """Return the canonical string form, for a ``text`` column that holds a UUID."""

    return str(as_uuid(value, label))


# --------------------------------------------------------------------------- #
# text / null helpers
# --------------------------------------------------------------------------- #
def text_or_empty(value: object) -> str:
    """``NULL`` becomes the domain's empty string."""

    return "" if value is None else str(value)


def optional_text(value: object) -> Optional[str]:
    """The domain's empty string becomes ``NULL``; anything else is kept."""

    if value is None:
        return None
    text = str(value)
    return text if text != "" else None


# --------------------------------------------------------------------------- #
# row -> domain
# --------------------------------------------------------------------------- #
def user_from_row(row: Mapping[str, Any]) -> User:
    return User(
        id=str(row["id"]),
        email=str(row["email"]),
        created_at=row["created_at"],
        status=UserStatus(row["status"]),
        display_name=str(row["display_name"]),
    )


def organization_from_row(row: Mapping[str, Any]) -> Organization:
    return Organization(
        id=str(row["id"]),
        name=str(row["name"]),
        created_at=row["created_at"],
        is_personal=bool(row["is_personal"]),
        status=OrganizationStatus(row["status"]),
        slug=text_or_empty(row["slug"]),
    )


def membership_from_row(row: Mapping[str, Any]) -> Membership:
    return Membership(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        user_id=str(row["user_id"]),
        role=MembershipRole(row["role"]),
        created_at=row["created_at"],
        status=MembershipStatus(row["status"]),
        updated_at=row["updated_at"],
    )


def project_from_row(row: Mapping[str, Any]) -> Project:
    return Project(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        name=str(row["name"]),
        created_at=row["created_at"],
        status=ProjectStatus(row["status"]),
        slug=text_or_empty(row["slug"]),
        workspace_ref=text_or_empty(row["workspace_ref"]),
        updated_at=row["updated_at"],
    )


def provider_account_from_row(row: Mapping[str, Any]) -> ProviderAccount:
    metadata = row["metadata"]
    if isinstance(metadata, str):
        # jsonb comes back decoded, but tolerate a text representation so a row
        # read through a different code path cannot produce a Mapping contract
        # violation in the domain.
        import json

        metadata = json.loads(metadata)
    if not isinstance(metadata, Mapping):
        metadata = {}
    return ProviderAccount(
        id=str(row["id"]),
        provider_name=str(row["provider_name"]),
        created_at=row["created_at"],
        secret_ref=str(row["secret_ref"]),
        # NULL is the system-owned marker and must survive the mapping unchanged.
        organization_id=(
            None if row["organization_id"] is None else str(row["organization_id"])
        ),
        status=ProviderAccountStatus(row["status"]),
        updated_at=row["updated_at"],
        metadata=dict(metadata),
    )


def api_key_from_row(row: Mapping[str, Any]) -> APIKey:
    scopes = row["scopes"]
    return APIKey(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        created_by_user_id=str(row["created_by_user_id"]),
        name=str(row["name"]),
        created_at=row["created_at"],
        status=APIKeyStatus(row["status"]),
        key_prefix=text_or_empty(row["key_prefix"]),
        scopes=tuple(str(s) for s in (scopes or ())),
        expires_at=row["expires_at"],
        revoked_at=row["revoked_at"],
        last_used_at=row["last_used_at"],
        updated_at=row["updated_at"],
    )


def run_record_from_row(row: Mapping[str, Any]) -> RunRecord:
    return RunRecord(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        project_id=str(row["project_id"]),
        created_at=row["created_at"],
        status=RunRecordStatus(row["status"]),
        core_run_id=row["core_run_id"],
        initiated_by_user_id=(
            None
            if row["initiated_by_user_id"] is None
            else str(row["initiated_by_user_id"])
        ),
        task_id=text_or_empty(row["task_id"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        attempt_count=int(row["attempt_count"]),
        failure_classification=text_or_empty(row["failure_classification"]),
        updated_at=row["updated_at"],
    )


def usage_record_from_row(row: Mapping[str, Any]) -> UsageRecord:
    return UsageRecord(
        id=str(row["id"]),
        organization_id=str(row["organization_id"]),
        run_record_id=str(row["run_record_id"]),
        core_run_id=str(row["core_run_id"]),
        attempt_number=int(row["attempt_number"]),
        created_at=row["created_at"],
        provider_name=text_or_empty(row["provider_name"]),
        model_name=text_or_empty(row["model_name"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        cached_tokens=int(row["cached_tokens"]),
        duration_seconds=float(row["duration_seconds"]),
        success=bool(row["success"]),
        fallback=bool(row["fallback"]),
        tool_call_count=int(row["tool_call_count"]),
        completed_at=row["completed_at"],
        error_type=text_or_empty(row["error_type"]),
    )


# --------------------------------------------------------------------------- #
# column lists
# --------------------------------------------------------------------------- #
def columns(*names: str) -> str:
    """Join column names for a SELECT list."""

    return ", ".join(names)


def scopes_to_array(scopes: Sequence[str]) -> list[str]:
    """``tuple[str, ...]`` -> ``text[]``.

    A bare ``str`` is refused rather than accepted. It satisfies ``Sequence[str]``, so
    without this check ``"keys:read"`` would be stored as the nine scopes ``k``, ``e``,
    ``y``, ... -- a silent corruption of the field that says what a key may do, with
    no error anywhere.
    """

    if isinstance(scopes, (str, bytes)):
        raise InvalidIdentifierError("scopes", scopes)
    return [str(scope) for scope in scopes]


def require_mapping(metadata: Mapping[str, object]) -> dict:
    """Validate a metadata mapping before it becomes ``jsonb``.

    ``jsonb`` in this schema is an object, never an array or a scalar; the CHECK
    constraint says so, and checking here turns a constraint violation into a
    named boundary error.
    """

    if not isinstance(metadata, Mapping):
        raise InvalidIdentifierError("metadata", metadata)
    return dict(metadata)


def iso_or_none(value: Optional[datetime]) -> Optional[datetime]:
    """Passthrough used to keep the datetime handling in one visible place."""

    return value
