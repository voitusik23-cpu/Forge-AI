"""The authorized operation context (Stage 1 / Step 4).

:class:`AuthorizedContext` is what the authorization layer produces and what a use case
consumes. It is the **only** object that carries a bound tenant into a repository call,
and it can be built only by the resolver in :mod:`app.platform.application.authorization`.

Why the guard, rather than a plain dataclass: an ``AuthorizedContext`` is the difference
between "the caller sent an organization_id" and "the server decided this organization".
If a caller could construct one, the authorization step would be skippable by anyone who
could write a keyword argument -- the same reasoning that guards
:class:`~app.platform.principal.AuthenticatedPrincipal` and Core's
``AuthorizedExecution``.

Like those two, this is an **accident guard and not a process boundary**: Python cannot
stop hostile in-process code. What it guarantees is that no ordinary code path can
manufacture an authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from app.platform.models import Membership, Organization
from app.platform.principal import AuthenticatedPrincipal

__all__ = ["AuthorizedContext", "AuthorizationSentinel"]


class AuthorizationSentinel:
    """The token type that guards context creation.

    A dedicated class rather than ``object()`` so that the resolver, the context and any
    future administrative resolver can share one module-level instance without exposing
    a bare ``object`` that is trivial to reproduce.
    """

    __slots__ = ()


#: The module-private sentinel. Reached through the module object by the resolver, which
#: lives in the same package, and by nothing else.
_AUTHORIZATION_SENTINEL = AuthorizationSentinel()


@dataclass(frozen=True)
class AuthorizedContext:
    """A verified principal bound to exactly one organization, and the membership that
    authorizes it.

    ``organization`` and ``membership`` are the **read** records the decision was made
    from, carried so a use case can act without re-reading them and being able to
    disagree with the decision. They are data, not new authority: the authority is the
    fact that this object exists and names one tenant.
    """

    principal: AuthenticatedPrincipal
    organization: Organization
    membership: Membership

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError(
            "AuthorizedContext cannot be constructed directly; it is produced by "
            "app.platform.application.authorization, which is the only layer that "
            "decides authorization"
        )

    @classmethod
    def create(
        cls,
        principal: AuthenticatedPrincipal,
        organization: Organization,
        membership: Membership,
        *,
        authorization_token: object = None,
    ) -> "AuthorizedContext":
        """Build a context. Callable only with the module-private sentinel."""

        if authorization_token is not _AUTHORIZATION_SENTINEL:
            raise PermissionError(
                "an AuthorizedContext can only be created by the authorization "
                "resolver; a client-supplied organization_id is a claim and never an "
                "authorization"
            )
        if not isinstance(principal, AuthenticatedPrincipal):
            raise TypeError("principal must be an AuthenticatedPrincipal")
        if not isinstance(organization, Organization):
            raise TypeError("organization must be an Organization")
        if not isinstance(membership, Membership):
            raise TypeError("membership must be a Membership")
        if str(membership.organization_id) != str(organization.id):
            raise ValueError(
                "the membership does not belong to the organization it would authorize"
            )
        if str(membership.user_id) != str(principal.subject_id):
            raise ValueError(
                "the membership does not belong to the principal it would authorize"
            )

        instance = object.__new__(cls)
        object.__setattr__(instance, "principal", principal)
        object.__setattr__(instance, "organization", organization)
        object.__setattr__(instance, "membership", membership)
        return instance

    @property
    def organization_id(self) -> str:
        """The bound tenant. Taken from the resolved organization, never from a claim."""

        return str(self.organization.id)

    @property
    def subject_id(self) -> str:
        return str(self.principal.subject_id)

    def __repr__(self) -> str:
        return (
            f"AuthorizedContext(organization_id={self.organization_id!r}, "
            f"subject_id={self.subject_id!r}, "
            f"role={self.membership.role.value!r})"
        )


def authorization_sentinel() -> AuthorizationSentinel:
    """Return the private sentinel.

    A function rather than a public constant so that importing it is a deliberate act
    that reads as one in a diff. Only
    :mod:`app.platform.application.authorization` calls it.
    """

    return _AUTHORIZATION_SENTINEL
