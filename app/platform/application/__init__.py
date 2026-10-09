"""Platform Application Services (Stage 1 / Step 4).

Application Services sit **above** persistence and are the only layer that decides
authorization. The dependency direction is one-way:

```
  HTTP surface / clients                    <- may present a credential, nothing else
    -> authentication verifier              <- the only producer of an identity
    -> Application Services  (this package) <- resolves tenancy, decides authorization
    -> persistence                          <- storage and containment only
    -> PostgreSQL + FORCE RLS               <- the containment backstop
```

What belongs here:

* the use cases, their inputs and outputs;
* the authorization decision, in :mod:`app.platform.application.authorization`;
* the transaction boundaries the use cases own;
* the translation of storage failures into the closed application error vocabulary.

What does **not** belong here: credential verification (that is the verifier, and the
mechanism is open decision O-2), SQL, RLS policy semantics, Core execution, the
Platform -> Core transport, billing, and any schema change.

Implemented in this slice: **AS-1** (list the caller's organizations) and **AS-2** (list
projects in a selected organization). Project creation, run preparation, `RunRecord`, the
Platform -> Core port, API keys, member administration, and provider accounts are **not
implemented**; see design section 12 for the ordered next step.

Two guarantees a reader should be able to find without reading every file:

* **client identifiers are claims.** ``organization_id`` is checked against the caller's
  own membership set, and a mismatch is refused. The server never substitutes the right
  value, and a cross-tenant identifier is answered ``not_found`` rather than
  ``not_permitted`` so it cannot be used to enumerate other tenants;
* **authorization precedes the transaction.** The subject is resolved and may refuse
  before any tenant-scoped Unit of Work is opened, so a refused request cannot leave a
  partial write and cannot even read tenant data.
"""

from app.platform.application.authorization import (
    SubjectIdentity,
    bind_organization,
    requires_subject,
    resolve_candidate_organizations,
    resolve_subject,
    select_candidate,
)
from app.platform.application.context import AuthorizedContext
from app.platform.application.errors import (
    AmbiguousOrganization,
    ApplicationError,
    Conflict,
    ErrorCode,
    InvalidRequest,
    NotPermitted,
    NotFound,
    SubjectNotAdmissible,
    TransactionAborted,
    Unauthenticated,
    Unavailable,
    translate_persistence_error,
)
from app.platform.application.organizations import (
    DiscoveryService,
    OrganizationSummary,
    ProjectSummary,
)

__all__ = [
    # use cases
    "DiscoveryService",
    "OrganizationSummary",
    "ProjectSummary",
    # authorization
    "AuthorizedContext",
    "SubjectIdentity",
    "requires_subject",
    "resolve_subject",
    "resolve_candidate_organizations",
    "select_candidate",
    "bind_organization",
    # errors
    "ApplicationError",
    "ErrorCode",
    "Unauthenticated",
    "SubjectNotAdmissible",
    "NotPermitted",
    "AmbiguousOrganization",
    "NotFound",
    "Conflict",
    "InvalidRequest",
    "TransactionAborted",
    "Unavailable",
    "translate_persistence_error",
]
