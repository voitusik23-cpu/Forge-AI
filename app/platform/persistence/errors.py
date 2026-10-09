"""Persistence-level errors for the Platform layer.

These are the only exceptions a repository is allowed to raise. A raw driver
exception must never escape as the Platform persistence API: callers above this
boundary are application services, and an application service should not have to
know which driver is underneath, or parse a PostgreSQL ``SQLSTATE`` to decide
whether a name was taken.

The hierarchy is deliberately small and each member exists because a caller must
be able to act differently:

* :class:`PersistenceError` -- the base, for "something went wrong in storage".
* :class:`EntityNotFound` -- the caller asked for a specific record and it is not
  there. Distinct from "you may not see it", which is
  :class:`PermissionDeniedError`; conflating the two would let an authorization
  failure masquerade as absence.
* :class:`InvalidIdentifierError` -- an identifier could not be represented in
  storage. This is a **programming error at the boundary**, raised before any SQL
  is sent, so a malformed identifier never turns into a confusing driver error.
* :class:`UniqueViolationError`, :class:`ForeignKeyViolationError`,
  :class:`CheckViolationError` -- the database constraints refused the write. The
  constraints are the final authority; a pre-check in application code can never
  replace them, and these errors are how a lost race is surfaced.
* :class:`PermissionDeniedError` -- row-level security, a table privilege, or a
  role boundary refused the operation.
* :class:`ConcurrentModificationError` -- a compare-and-set lifecycle update matched
  no row because another writer moved the record first, or because the record was not
  in the state the caller expected. Distinct from :class:`EntityNotFound` on purpose:
  the row usually exists, and reporting "not found" for a lost race would send a
  caller looking for the wrong problem.
* :class:`TransactionError` -- the transaction could not be started, committed, or
  used coherently. Also covers a repository call made outside an active
  transaction, which is a programming error this layer must make loud rather than
  silently auto-committing.
* :class:`ConnectionError` -- the database could not be reached.
"""

from __future__ import annotations

from typing import Optional

__all__ = [
    "PersistenceError",
    "EntityNotFound",
    "InvalidIdentifierError",
    "UniqueViolationError",
    "ForeignKeyViolationError",
    "CheckViolationError",
    "PermissionDeniedError",
    "ConcurrentModificationError",
    "TransactionError",
    "ConnectionError",
]


class PersistenceError(Exception):
    """Base class for every Platform persistence failure."""


class EntityNotFound(PersistenceError):
    """A requested record does not exist in the current visibility scope."""

    def __init__(self, entity: str, identifier: Optional[str] = None) -> None:
        self.entity = entity
        self.identifier = identifier
        detail = f"{entity} not found"
        if identifier is not None:
            detail = f"{detail}: {identifier}"
        super().__init__(detail)


class InvalidIdentifierError(PersistenceError):
    """An identifier cannot be represented in storage.

    The domain contract accepts any opaque non-empty string, while the PostgreSQL
    schema stores identifiers as ``uuid``. This error is the honest boundary: the
    adapter validates before sending SQL, so the failure names the field rather
    than surfacing a driver message about a cast.
    """

    def __init__(self, label: str, value: object) -> None:
        self.label = label
        self.value = value
        super().__init__(
            f"{label} is not a valid persistence identifier: {value!r}"
        )


class UniqueViolationError(PersistenceError):
    """A unique constraint rejected the write, including a lost race."""

    def __init__(self, message: str, constraint: Optional[str] = None) -> None:
        self.constraint = constraint
        super().__init__(message)


class ForeignKeyViolationError(PersistenceError):
    """A foreign key rejected the write.

    In this schema that most often means a cross-tenant reference, which the
    composite keys of K-7 and the provenance keys are designed to refuse.
    """

    def __init__(self, message: str, constraint: Optional[str] = None) -> None:
        self.constraint = constraint
        super().__init__(message)


class CheckViolationError(PersistenceError):
    """A CHECK constraint rejected the write."""

    def __init__(self, message: str, constraint: Optional[str] = None) -> None:
        self.constraint = constraint
        super().__init__(message)


class PermissionDeniedError(PersistenceError):
    """Row-level security, a privilege, or a role boundary refused the operation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConcurrentModificationError(PersistenceError):
    """A compare-and-set update matched no row.

    A lifecycle transition carries the state it expects as part of the ``UPDATE``, so
    two writers racing for the same record cannot both succeed; the loser sees this
    instead of a successful-looking write. It is also what a caller gets when the
    record was not in the state it named, which is why the message carries the
    expectation rather than only the identifier.
    """

    def __init__(
        self, entity: str, identifier: Optional[str] = None, detail: str = ""
    ) -> None:
        self.entity = entity
        self.identifier = identifier
        self.detail = detail
        message = f"{entity} was modified concurrently or was not in the expected state"
        if identifier is not None:
            message += f": {identifier}"
        if detail:
            message += f" ({detail})"
        super().__init__(message)


class TransactionError(PersistenceError):
    """The transaction is not in a usable state for this operation."""


class ConnectionError(PersistenceError):  # noqa: A001 - intentional boundary name
    """The database could not be reached."""
