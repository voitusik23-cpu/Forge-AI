"""Application-layer errors (Stage 1 / Step 4).

The **only** errors an application service raises. Two vocabularies exist in the
Platform and they are deliberately separate (design section 6.1):

* the persistence hierarchy ``PersistenceError`` belongs to storage -- a driver
  constraint, a lost compare-and-set, a refused transaction;
* this hierarchy belongs to use cases and is what a caller above reacts to.

An application service **translates**: it never leaks a persistence type. A caller that
had to catch :class:`UniqueViolationError` would be coupled to the schema, and the
meaning of the error would change the first time a constraint changed.

The code list is **closed** (design section 6.2), because an open vocabulary grows
accidentally. Every refusal maps to exactly one code.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
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


class ErrorCode:
    """The closed refusal vocabulary.

    Plain string constants rather than an enum, so a code can be carried across a
    boundary (a log line, an API response) without importing this module.
    """

    UNAUTHENTICATED = "unauthenticated"
    SUBJECT_NOT_ADMISSIBLE = "subject_not_admissible"
    NOT_PERMITTED = "not_permitted"
    AMBIGUOUS_ORGANIZATION = "ambiguous_organization"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    INVALID_REQUEST = "invalid_request"
    TRANSACTION_ABORTED = "transaction_aborted"
    UNAVAILABLE = "unavailable"

    ALL = (
        UNAUTHENTICATED,
        SUBJECT_NOT_ADMISSIBLE,
        NOT_PERMITTED,
        AMBIGUOUS_ORGANIZATION,
        NOT_FOUND,
        CONFLICT,
        INVALID_REQUEST,
        TRANSACTION_ABORTED,
        UNAVAILABLE,
    )


class ApplicationError(Exception):
    """Base class for every application-layer refusal.

    ``code`` is the contract; ``message`` is what a caller may show. ``detail`` is
    internal and must never reach a client: it is where the queried identifier, the
    constraint name, or the SQLSTATE goes, because an operator needs those and a
    cross-tenant caller must not have them (design section 6.4).
    """

    code: str = "application_error"

    def __init__(
        self, message: str = "", *, detail: Optional[str] = None
    ) -> None:
        self.detail = detail
        super().__init__(message)

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else self.code


def _make(name: str, code: str, doc: str):
    """Build one refusal class with a fixed code.

    A closure rather than nine near-identical class bodies, so the code and the class
    cannot drift apart.
    """

    def __init__(self, message: str = "", *, detail: Optional[str] = None) -> None:
        ApplicationError.__init__(self, message, detail=detail)

    return type(
        name,
        (ApplicationError,),
        {"code": code, "__init__": __init__, "__doc__": doc},
    )


Unauthenticated = _make(
    "Unauthenticated",
    ErrorCode.UNAUTHENTICATED,
    "No usable principal. There is no anonymous principal, so this is always a refusal.",
)

SubjectNotAdmissible = _make(
    "SubjectNotAdmissible",
    ErrorCode.SUBJECT_NOT_ADMISSIBLE,
    "A principal exists, but the subject may not act: unknown, suspended, or otherwise "
    "inadmissible.",
)

NotPermitted = _make(
    "NotPermitted",
    ErrorCode.NOT_PERMITTED,
    "The subject has no ACTIVE membership in the bound organization, or the resolved "
    "role is not in the operation's required set.",
)

AmbiguousOrganization = _make(
    "AmbiguousOrganization",
    ErrorCode.AMBIGUOUS_ORGANIZATION,
    "An organization had to be chosen and was not, while more than one is available.",
)

NotFound = _make(
    "NotFound",
    ErrorCode.NOT_FOUND,
    "The named resource is not in the bound tenant. A cross-tenant identifier is "
    "answered with this code rather than not_permitted, so a caller cannot enumerate "
    "other tenants' identifiers (design section 6.3).",
)

Conflict = _make(
    "Conflict",
    ErrorCode.CONFLICT,
    "A compare-and-set was lost, or a uniqueness constraint refused the write.",
)

InvalidRequest = _make(
    "InvalidRequest",
    ErrorCode.INVALID_REQUEST,
    "The request itself is malformed: an identifier that cannot be represented, or a "
    "missing required input.",
)

TransactionAborted = _make(
    "TransactionAborted",
    ErrorCode.TRANSACTION_ABORTED,
    "An earlier failure aborted the transaction, so no further statement can run in it "
    "and a commit would discard the work (persistence H-1). The operation failed; "
    "nothing partially succeeded.",
)

Unavailable = _make(
    "Unavailable",
    ErrorCode.UNAVAILABLE,
    "A dependency refused or was unreachable. An outage never degrades into a "
    "permissive path.",
)


#: Refusal codes for the specific storage failures a caller must be able to act on.
#: Everything else becomes :class:`Unavailable`, because a storage failure the
#: application layer cannot interpret must not look like a permission decision.
_TRANSLATION = {
    "EntityNotFound": NotFound,
    "InvalidIdentifierError": InvalidRequest,
    "UniqueViolationError": Conflict,
    "CheckViolationError": Conflict,
    "ConcurrentModificationError": Conflict,
    "ForeignKeyViolationError": InvalidRequest,
    "TransactionError": TransactionAborted,
    "ConnectionError": Unavailable,
}


def translate_persistence_error(exc: Exception) -> ApplicationError:
    """Turn a storage failure into an application refusal. Never leaks the original type.

    The one case that is **not** a plain translation is
    :class:`~app.platform.persistence.errors.PermissionDeniedError`. Row-level
    security refusing a statement means the application layer's own authorization was
    wrong or missing, so it is a **bug signal** (design section 4.5): the caller sees a
    denial, and the detail records that the layer should have denied first.

    An already-application error passes through unchanged, so translation is
    idempotent.
    """

    if isinstance(exc, ApplicationError):
        return exc

    name = type(exc).__name__
    if name == "PermissionDeniedError":
        return NotPermitted(
            "the operation was refused",
            detail=(
                "row-level security refused the statement, which means the "
                "application layer should have denied it first: this is an internal "
                "inconsistency, not a normal denial. Original: "
                f"{name}: {exc}"
            ),
        )

    target = _TRANSLATION.get(name)
    if target is None:
        return Unavailable(
            "the operation could not be completed",
            detail=f"unmapped storage failure {name}: {exc}",
        )
    return target("the operation could not be completed", detail=f"{name}: {exc}")
