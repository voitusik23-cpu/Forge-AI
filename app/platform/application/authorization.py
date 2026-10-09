"""The authorization algorithm (Stage 1 / Step 4, design section 4.1).

Ordered and fail-closed. **This module contains no SQL.** It composes repository calls
that the caller has already placed inside a scope, so the decision can be read in one
place and the storage layer stays storage.

The algorithm, from the design:

```
 1. principal exists                                      else DENY unauthenticated
 2. subject state admissible                              else DENY subject_not_admissible
 3. resolve candidates: organizations reachable through
    the subject's ACTIVE memberships                      empty -> DENY no_organization
 4. bind ONE organization:
       - if the client named one, it must be in the candidate set
       - otherwise it is the subject's sole/organization-less candidate, or the
         operation requires an explicit choice and is refused as ambiguous
    a client-named organization outside the set -> DENY not_permitted
 5. resolve the membership for (subject, organization) -> must be ACTIVE
 6. evaluate the operation's permission requirement
 7. ONLY NOW may the caller perform the effect
```

Two properties are load-bearing and are asserted by tests:

* **the server never repairs a client claim.** A wrong ``organization_id`` is refused;
  substituting the right one would teach a client that a wrong identifier works and
  would hide the defect from an operator;
* **`not_found` rather than `not_permitted` for a cross-tenant identifier.** Design
  section 6.3: answering ``not_permitted`` would disclose that the identifier exists and
  allow enumeration. Inside the caller's own tenant a genuine shortfall is
  ``not_permitted``, because the caller already knows the resource exists.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.platform.application.context import AuthorizedContext, authorization_sentinel
from app.platform.application.errors import (
    AmbiguousOrganization,
    NotPermitted,
    NotFound,
    SubjectNotAdmissible,
    Unauthenticated,
)
from app.platform.enums import MembershipStatus, OrganizationStatus, UserStatus
from app.platform.models import Membership, Organization, User
from app.platform.principal import AuthenticatedPrincipal

__all__ = [
    "SubjectIdentity",
    "requires_subject",
    "resolve_subject",
    "resolve_candidate_organizations",
    "bind_organization",
    "resolve_context",
    "select_candidate",
]


class SubjectIdentity:
    """The verified subject, re-checked against storage.

    ``user`` is the row the check was made from, carried so a caller can act on the same
    facts the decision used instead of re-reading and being able to disagree.
    """

    __slots__ = ("principal", "user")

    def __init__(self, principal: AuthenticatedPrincipal, user: User) -> None:
        self.principal = principal
        self.user = user

    @property
    def subject_id(self) -> str:
        return str(self.principal.subject_id)

    def __repr__(self) -> str:
        return f"SubjectIdentity(subject_id={self.subject_id!r}, status={self.user.status.value!r})"


def requires_subject(
    principal: Optional[AuthenticatedPrincipal],
) -> AuthenticatedPrincipal:
    """Step 1: there is no anonymous principal, so absence is a refusal.

    A raw string is refused too. A client-supplied ``user_id`` is a claim, and this
    check exists so that treating one as an identity fails loudly here rather than
    quietly later.
    """

    if principal is None:
        raise Unauthenticated("authentication is required")
    if not isinstance(principal, AuthenticatedPrincipal):
        raise Unauthenticated(
            "a verified principal is required; a caller-supplied identifier is a "
            "claim and not an identity"
        )
    return principal


async def resolve_subject(unit, principal: AuthenticatedPrincipal) -> SubjectIdentity:
    """Step 2: the subject exists and its state permits acting.

    Run inside a **pre-tenant** scope. The read is the subject's own row, which the
    discovery policy returns only for the subject the transaction declared, so a
    principal naming somebody else finds nothing and is refused -- fail-closed at the
    storage layer as well as here.

    Why re-check at all, when the verifier already admitted the subject: a principal is
    a *value*. It can be older than the current state of the account, and a suspended
    account must not keep acting on a principal issued before the suspension. This is the
    cheap half of that guarantee; the verifier owns the other half (design section 3.4).
    """

    user = await unit.users.get(str(principal.subject_id))
    if user is None or str(user.id) != str(principal.subject_id):
        raise SubjectNotAdmissible("the subject could not be resolved")
    if user.status is not UserStatus.ACTIVE:
        raise SubjectNotAdmissible("the account is not active")
    return SubjectIdentity(principal, user)


async def resolve_candidate_organizations(
    unit, subject: SubjectIdentity
) -> Sequence[Organization]:
    """Step 3: the organizations this subject may choose between.

    Run inside the same **pre-tenant** scope, with the subject already declared, so the
    discovery policy and the session binding agree on whose tenancy is being read.

    Only tenants reached through a membership **and** an active organization are
    candidates: an inactive membership must not appear, and a suspended organization must
    not be selectable.

    An empty result is **not** an error. It says the caller belongs nowhere, which is a
    fact about the caller and discloses nothing about anyone else.
    """

    memberships = await unit.memberships.list_for_user(subject.subject_id)
    active_ids = {
        str(m.organization_id)
        for m in memberships
        if m.status is MembershipStatus.ACTIVE
    }
    if not active_ids:
        return ()

    organizations = await unit.organizations.list_for_user(subject.subject_id)
    return tuple(
        organization
        for organization in organizations
        if str(organization.id) in active_ids
        and organization.status is OrganizationStatus.ACTIVE
    )


async def _require_membership(unit, subject: SubjectIdentity, organization_id: str) -> Membership:
    """Step 5: an ACTIVE membership for this subject in this organization."""

    membership = await unit.memberships.find_for_user(subject.subject_id)
    if membership is None:
        raise NotPermitted("no membership in this organization")
    if str(membership.organization_id) != str(organization_id):
        # Defensive: the repository is already bounded by the session tenant, so a
        # mismatch would mean the session and the argument disagreed -- which must be a
        # refusal, never a silent use of the session's tenant.
        raise NotPermitted("the membership does not belong to this organization")
    if membership.status is not MembershipStatus.ACTIVE:
        raise NotPermitted("the membership is not active")
    return membership


def select_candidate(
    candidates: Sequence[Organization], organization_id: Optional[str]
) -> Organization:
    """Step 4: bind exactly one organization, from the candidate set only.

    A named organization that is not in the set is answered ``not_found``: the caller
    must not be able to tell "another tenant's" from "does not exist" (design section
    6.3). Nothing is substituted.
    """

    if not candidates:
        raise NotPermitted("the subject has no active organization")

    if organization_id is None:
        if len(candidates) > 1:
            raise AmbiguousOrganization(
                "an organization must be chosen and was not"
            )
        return candidates[0]

    wanted = str(organization_id)
    for candidate in candidates:
        if str(candidate.id) == wanted:
            return candidate
    raise NotFound("the organization was not found")


async def bind_organization(unit, subject: SubjectIdentity, organization_id: str) -> AuthorizedContext:
    """Steps 5 and 6 for an already-selected organization, inside a **tenant** scope.

    The organization's own status is re-read here rather than trusted from the
    pre-tenant candidate list, because the deciding transaction is this one: a tenant
    suspended between the two reads must not be usable.

    On success this returns the context -- the single object that carries the bound
    tenant into a repository call.
    """

    membership = await _require_membership(unit, subject, organization_id)

    organization = await unit.organizations.get(organization_id)
    if organization is None:
        # The tenant policy returns nothing for a tenant the subject cannot see, and a
        # suspended tenant must not be usable either way.
        raise NotFound("the organization was not found")
    if organization.status is not OrganizationStatus.ACTIVE:
        raise NotPermitted("the organization is not active")

    return AuthorizedContext.create(
        subject.principal,
        organization,
        membership,
        authorization_token=authorization_sentinel(),
    )


async def resolve_context(
    pre_tenant_unit, tenant_unit_factory, principal: Optional[AuthenticatedPrincipal],
    organization_id: str,
):
    """Steps 1-6 for an operation that binds an organization.

    ``pre_tenant_unit`` is an open pre-tenant Unit of Work; ``tenant_unit_factory`` is an
    async context manager factory taking ``(organization_id, subject_id)`` and yielding
    the tenant-scoped Unit of Work. The caller owns both, because the caller owns the
    transaction boundary (design section 7.1) and this module owns only the decision.
    """

    verified = requires_subject(principal)
    subject = await resolve_subject(pre_tenant_unit, verified)
    candidates = await resolve_candidate_organizations(pre_tenant_unit, subject)
    selected = select_candidate(candidates, organization_id)

    async with tenant_unit_factory(str(selected.id), subject.subject_id) as tenant_unit:
        return await bind_organization(tenant_unit, subject, str(selected.id))
