"""Authentication trust boundary (Stage 1 / Step 4).

This module holds the **shape of a verified identity** and the port through which one
is produced. It holds no credential checking, no session store, no password hashing,
no JWT, no OAuth client, and no database access, and it does not implement a
mechanism: the exact authentication mechanism is open decision **O-2** and is not
invented here.

What it fixes is the *boundary*:

* an identity exists only as an :class:`AuthenticatedPrincipal`, and only a
  component implementing :class:`AuthenticationVerifier` may produce one;
* a principal says **who**, never **what they may do** — it carries no
  ``organization_id``, no ``project_id``, no role, no scope, and no credential;
* nothing that a client can send is a principal. A client-supplied ``user_id``, a
  PostgreSQL GUC such as ``forge.user_id``, an ``APIKey.key_prefix``, a ``RunRecord``
  identifier, and a ``RunScope`` are all **not** proof of identity.

Contract: ``docs/STAGE-1-STEP-4-AUTH-APPLICATION-SERVICES-DESIGN.md`` sections 3 and 8,
and ``docs/STAGE-1-ARCHITECTURE-CONTRACT.md`` sections 3, 4, 11, 16.

Why ``forge.user_id`` is not authentication, stated here because Step 3, migration
``0015`` and ``D-PLATFORM-21`` all say it: the GUC decides which subject a transaction
is *willing to talk about*; it never decides who the caller *is*. Step 3 enforces
"one subject, and always the same one". This module's port is the only thing that may
decide which subject that is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

from app.platform.models import utc_now

__all__ = [
    "SubjectKind",
    "AuthenticationMethod",
    "Credential",
    "AuthenticatedPrincipal",
    "AuthenticationVerifier",
    "AuthenticationError",
]

#: The module-private sentinel that guards principal creation.
#
# This mirrors the mechanism Core already uses for ``AuthorizedExecution`` in
# ``app/execution/intent.py``. It is an **accident guard, not a boundary**: Python
# cannot stop hostile in-process code, and this module does not claim to. What it
# guarantees is that no ordinary code path can mint an identity by accident, so "a
# client sent us a user_id" has no route to becoming a principal.
_PRINCIPAL_SENTINEL = object()

#: Identifiers are opaque strings. The shape check is deliberately the same one the
#: domain contracts use, so a principal cannot carry an identifier the domain layer
#: would later refuse.
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class AuthenticationError(Exception):
    """A credential could not be resolved to exactly one admissible subject.

    One exception for every refusal reason -- missing, malformed, unknown, ambiguous,
    inadmissible, or verifier-unavailable -- because the *caller* must not be able to
    tell them apart. The distinction belongs in the verifier's internal log, not in
    the error that reaches a client (design section 6.3).
    """


class SubjectKind(str, Enum):
    """Which authority model a principal's subject belongs to.

    ``API_KEY`` exists because contract R-5 leaves two API-key authority models open
    (organization-scoped service principal, or creator-membership-derived credential)
    and the choice is the owner's. Recording the *kind* now lets the application layer
    branch on it later without this module having to decide which model wins.
    """

    USER = "user"
    API_KEY = "api_key"
    SERVICE = "service"


class AuthenticationMethod(str, Enum):
    """The class of evidence a verifier accepted. Not the evidence itself.

    Named so that an audit record can say how a subject was established without any
    module needing to store, log, or transport the credential.
    """

    SESSION = "session"
    API_KEY = "api_key"
    SERVICE = "service"


@dataclass(frozen=True)
class Credential:
    """An opaque credential presented by a caller.

    Nothing in this package inspects ``material``. It is a transport envelope whose
    interpretation belongs entirely to the verifier implementation (O-2), which is why
    it has no validation beyond being a non-empty string: validating a format here
    would be inventing the mechanism.
    """

    material: str = field(repr=False)
    scheme: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.material, str) or not self.material.strip():
            raise AuthenticationError("a credential must be a non-empty string")
        if not isinstance(self.scheme, str):
            raise AuthenticationError("credential scheme must be a string")

    def __repr__(self) -> str:  # pragma: no cover - repr is asserted in tests
        # The material is NEVER rendered. A credential that leaks through a traceback
        # or a log line is a credential that has to be rotated.
        return f"Credential(scheme={self.scheme!r}, material=<redacted>)"


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    """A subject whose identity a verifier has established.

    Four fields, and no more. Deliberately absent: ``organization_id``,
    ``project_id``, role, scopes, email, display name, workspace, and any credential
    material. A principal answers **who**; every **what** is resolved per operation by
    the application layer against server data.

    Create one with :meth:`create`. The direct constructor raises, because accepting
    the dataclass constructor would make an unverified identity one keyword away.
    """

    subject_id: str
    subject_kind: SubjectKind
    method: AuthenticationMethod
    verified_at: datetime

    def __init__(self, *args: object, **kwargs: object) -> None:
        # Unreachable in normal use: `create` builds the instance through
        # `object.__new__` and `object.__setattr__`. See `create`.
        raise TypeError(
            "AuthenticatedPrincipal cannot be constructed directly; use "
            "AuthenticatedPrincipal.create(..., principal_token=...) from an "
            "authentication verifier"
        )

    @classmethod
    def create(
        cls,
        subject_id: str,
        *,
        subject_kind: SubjectKind,
        method: AuthenticationMethod,
        verified_at: Optional[datetime] = None,
        principal_token: object = None,
    ) -> "AuthenticatedPrincipal":
        """Build a principal. Only callable with the module-private sentinel.

        ``principal_token`` is positional-in-name only so that a caller in another
        module cannot pass it without deliberately importing the private name. A
        verifier implementation lives in the same trust domain as this module and
        imports the sentinel explicitly; ordinary code never has a reason to.
        """

        if principal_token is not _PRINCIPAL_SENTINEL:
            raise PermissionError(
                "an AuthenticatedPrincipal can only be created by a component that "
                "holds the authentication sentinel; a verified identity is produced "
                "by AuthenticationVerifier.verify, never by a caller"
            )
        if not isinstance(subject_id, str) or not _IDENTIFIER_PATTERN.match(subject_id):
            raise AuthenticationError(
                "subject_id must be an opaque non-empty identifier"
            )
        if not isinstance(subject_kind, SubjectKind):
            raise AuthenticationError(f"unknown subject_kind: {subject_kind!r}")
        if not isinstance(method, AuthenticationMethod):
            raise AuthenticationError(f"unknown authentication method: {method!r}")
        stamp = verified_at or utc_now()
        if not isinstance(stamp, datetime):
            raise AuthenticationError("verified_at must be a datetime")

        instance = object.__new__(cls)
        object.__setattr__(instance, "subject_id", subject_id)
        object.__setattr__(instance, "subject_kind", subject_kind)
        object.__setattr__(instance, "method", method)
        object.__setattr__(instance, "verified_at", stamp)
        return instance

    def __repr__(self) -> str:
        return (
            f"AuthenticatedPrincipal(subject_id={self.subject_id!r}, "
            f"subject_kind={self.subject_kind.value!r}, method={self.method.value!r})"
        )


@runtime_checkable
class AuthenticationVerifier(Protocol):
    """The one component allowed to say "this credential is subject X".

    An implementation belongs at the boundary layer that also owns the transport
    (design section 3.4), **not** in ``app/platform/persistence``: persistence must
    never verify a password, a JWT, or an external token, because a repository that
    could verify a credential would become an authentication authority by accident.

    Implementations must be fail-closed on every path (design section 3.5):

    * missing credential -> refuse;
    * malformed credential -> refuse;
    * unknown subject -> refuse;
    * suspended or otherwise inadmissible subject -> refuse;
    * ambiguous credential (zero or several matching subjects) -> refuse, never
      "pick the first";
    * verifier unavailable -> refuse, never degrade to a permissive path.

    Every refusal raises :class:`AuthenticationError` and returns no principal.
    """

    async def verify(self, credential: Credential) -> AuthenticatedPrincipal:
        """Resolve a credential to exactly one admissible subject, or refuse."""
        ...
