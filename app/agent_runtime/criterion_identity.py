"""Trusted task and criterion identity for one production run.

This module is the criterion-identity boundary. It answers, deterministically and
server-side, three questions the rest of the system must never guess:

* **Which task?** ``TaskIdentity`` - the task id plus a fingerprint over the
  trusted task content.
* **Which criterion?** ``CriterionIdentity`` - the criterion id, the task it
  belongs to, and a fingerprint over the criterion and its verification
  expectation.
* **Which run?** ``RunCriterionBinding`` - one frozen binding per run, created
  only by trusted composition, that ties the task identity and every criterion
  identity to exactly one ``run_id``.

Identity is **not** authorization. A task or criterion identity grants no
execution, tool, workspace, network, credential, or approval authority; it only
makes a verdict attributable. Nothing here executes anything, and nothing here is
reachable from an HTTP request, a task context, an LLM, a plan, a decision, or a
tool result.

Fingerprints are canonical: they are SHA-256 over a JSON projection with sorted
keys, so the same trusted content always yields the same digest and any
substitution changes it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Mapping


class CriterionIdentityError(ValueError):
    """Raised when a task or criterion identity cannot be trusted."""


class IdentityFailureCode(str, Enum):
    """Closed taxonomy of identity failures, for bounded event metadata."""

    RUN_ID_MISMATCH = "run_id_mismatch"
    TASK_ID_MISMATCH = "task_id_mismatch"
    CRITERION_NOT_BOUND = "criterion_not_bound"
    CRITERION_TASK_MISMATCH = "criterion_task_mismatch"
    CRITERION_FINGERPRINT_MISMATCH = "criterion_fingerprint_mismatch"
    TASK_FINGERPRINT_MISMATCH = "task_fingerprint_mismatch"
    BINDING_REPLACEMENT = "binding_replacement"
    IDENTITY_MISSING = "identity_missing"


def _digest(payload: Mapping[str, object]) -> str:
    """Deterministic SHA-256 over a canonical JSON projection."""
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


@dataclass(frozen=True)
class TaskIdentity:
    """Immutable identity of one trusted task."""

    task_id: str
    task_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise CriterionIdentityError("task_id must be a non-empty string")
        if not _is_digest(self.task_fingerprint):
            raise CriterionIdentityError("task_fingerprint must be a sha256 digest")
        object.__setattr__(self, "task_id", self.task_id.strip())

    @classmethod
    def from_specification(cls, specification: object) -> "TaskIdentity":
        """Derive the identity from a trusted ``TaskSpecification``.

        The fingerprint covers only trusted task content: its identity, title,
        description, and requirement ids/descriptions. It never includes a
        command, an environment value, a credential, or a raw output.
        """
        task_id = getattr(specification, "task_id", "")
        if not isinstance(task_id, str) or not task_id.strip():
            raise CriterionIdentityError(
                "task identity requires a trusted task specification with a task_id"
            )
        requirements: list[dict[str, object]] = []
        for requirement in getattr(specification, "requirements", ()) or ():
            requirements.append(
                {
                    "requirement_id": str(getattr(requirement, "requirement_id", "")),
                    "description": str(getattr(requirement, "description", "")),
                    "required": bool(getattr(requirement, "required", True)),
                }
            )
        requirements.sort(key=lambda entry: str(entry["requirement_id"]))
        payload = {
            "task_id": task_id.strip(),
            "title": str(getattr(specification, "title", "")),
            "description": str(getattr(specification, "description", "")),
            "requirements": requirements,
        }
        return cls(task_id=task_id.strip(), task_fingerprint=_digest(payload))

    def assert_matches(self, other: object) -> None:
        """Fail closed when two task identities disagree."""
        if not isinstance(other, TaskIdentity):
            raise CriterionIdentityError("task identity must be a TaskIdentity")
        if self.task_id != other.task_id:
            raise CriterionIdentityError(
                f"task identity belongs to {self.task_id!r}, not {other.task_id!r}"
            )
        if self.task_fingerprint != other.task_fingerprint:
            raise CriterionIdentityError(
                "task fingerprint does not match the trusted task content"
            )

    def bounded_summary(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_fingerprint": self.task_fingerprint,
        }


@dataclass(frozen=True)
class CriterionIdentity:
    """Immutable identity of one criterion owned by one task.

    A criterion cannot exist as globally named text: it always carries the task it
    belongs to and a fingerprint over its own content plus its verification
    expectation.
    """

    criterion_id: str
    task_id: str
    criterion_fingerprint: str

    def __post_init__(self) -> None:
        for name, value in (
            ("criterion_id", self.criterion_id),
            ("task_id", self.task_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise CriterionIdentityError(f"{name} must be a non-empty string")
        if not _is_digest(self.criterion_fingerprint):
            raise CriterionIdentityError(
                "criterion_fingerprint must be a sha256 digest"
            )
        object.__setattr__(self, "criterion_id", self.criterion_id.strip())
        object.__setattr__(self, "task_id", self.task_id.strip())

    @classmethod
    def derive(
        cls,
        *,
        criterion: object,
        task_id: str,
        expectation: object | None,
    ) -> "CriterionIdentity":
        """Derive a criterion identity from trusted criterion content."""
        payload = _criterion_payload(criterion, task_id)
        payload["expectation"] = _expectation_payload(expectation)
        return cls(
            criterion_id=str(payload["criterion_id"]),
            task_id=str(payload["task_id"]),
            criterion_fingerprint=_digest(payload),
        )

    @staticmethod
    def fingerprint_for_content(
        criterion: object, *, task_id: str, expectation: object | None = None
    ) -> str:
        """Fingerprint a criterion's own content, optionally with an expectation.

        Used to prove that a criterion delivered to verification is the very
        criterion the run was bound to, not merely one sharing its id.
        """
        payload = _criterion_payload(criterion, task_id)
        payload["expectation"] = _expectation_payload(expectation)
        return _digest(payload)

    def assert_belongs_to(self, task_id: str) -> None:
        if self.task_id != task_id:
            raise CriterionIdentityError(
                f"criterion {self.criterion_id!r} belongs to task {self.task_id!r}, "
                f"not {task_id!r}"
            )

    def bounded_summary(self) -> dict[str, object]:
        return {
            "criterion_id": self.criterion_id,
            "task_id": self.task_id,
            "criterion_fingerprint": self.criterion_fingerprint,
        }


@dataclass(frozen=True)
class RunCriterionBinding:
    """One frozen identity binding for one run.

    Created only by trusted composition from a trusted ``TaskSpecification`` and
    the operator's acceptance declaration. It is immutable, and the harness
    refuses a second, different binding for the same run, so criteria cannot be
    swapped after a run starts.
    """

    run_id: str
    task_identity: TaskIdentity
    criterion_identities: tuple[CriterionIdentity, ...]
    expectations: Mapping[str, object] = field(default_factory=dict)
    # Canonical content of every criterion, frozen at binding time. Verification
    # proves that the criterion it is about to evaluate has this exact content, so
    # a criterion that merely reuses a bound id cannot pass.
    criterion_contents: Mapping[str, object] = field(default_factory=dict)
    source: str = "operator_composition"

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise CriterionIdentityError("run_id must be a non-empty string")
        if not isinstance(self.task_identity, TaskIdentity):
            raise CriterionIdentityError("task_identity must be a TaskIdentity")
        identities = tuple(self.criterion_identities)
        if not identities:
            # An empty criterion set must never be mistaken for a permissive one.
            raise CriterionIdentityError(
                "a run binding must contain at least one criterion identity"
            )
        seen: set[str] = set()
        for identity in identities:
            if not isinstance(identity, CriterionIdentity):
                raise CriterionIdentityError(
                    "criterion_identities must be CriterionIdentity values"
                )
            if identity.criterion_id in seen:
                raise CriterionIdentityError(
                    f"duplicate criterion identity: {identity.criterion_id!r}"
                )
            seen.add(identity.criterion_id)
            # Criterion ownership is checked here, once, at binding time.
            identity.assert_belongs_to(self.task_identity.task_id)
        object.__setattr__(self, "run_id", self.run_id.strip())
        object.__setattr__(self, "criterion_identities", identities)
        object.__setattr__(self, "expectations", dict(self.expectations or {}))
        object.__setattr__(
            self, "criterion_contents", dict(self.criterion_contents or {})
        )

    @property
    def task_id(self) -> str:
        return self.task_identity.task_id

    @property
    def criterion_ids(self) -> tuple[str, ...]:
        return tuple(i.criterion_id for i in self.criterion_identities)

    @property
    def fingerprint(self) -> str:
        """Deterministic fingerprint over the whole binding."""
        return _digest(
            {
                "run_id": self.run_id,
                "task": self.task_identity.bounded_summary(),
                "criteria": [
                    i.bounded_summary()
                    for i in sorted(
                        self.criterion_identities, key=lambda i: i.criterion_id
                    )
                ],
            }
        )

    def identity_for(self, criterion_id: object) -> CriterionIdentity | None:
        if not isinstance(criterion_id, str):
            return None
        for identity in self.criterion_identities:
            if identity.criterion_id == criterion_id:
                return identity
        return None

    def assert_belongs_to(self, run_id: str, task_id: str) -> None:
        """Fail closed when the binding is not this run's binding."""
        if self.run_id != run_id:
            raise CriterionIdentityError(
                f"criterion binding belongs to run {self.run_id!r}, not {run_id!r}"
            )
        if self.task_identity.task_id != task_id:
            raise CriterionIdentityError(
                f"criterion binding belongs to task {self.task_identity.task_id!r}, "
                f"not {task_id!r}"
            )

    def validate_expectations(self, expectations: Mapping[str, object]) -> None:
        """Prove that a criterion-to-expectation map is exactly this binding.

        Every key must be a bound criterion, and its derived fingerprint must
        equal the frozen one. A substituted criterion, a substituted expectation,
        or an extra key is refused.
        """
        if not isinstance(expectations, Mapping):
            raise CriterionIdentityError("expectations must be a mapping")
        bound = set(self.criterion_ids)
        provided = {str(key) for key in expectations}
        unknown = sorted(provided - bound)
        if unknown:
            raise CriterionIdentityError(
                "verification expectations name criteria that are not bound to this "
                f"run: {unknown}"
            )
        missing = sorted(bound - provided)
        if missing:
            # A bound criterion that is not about to be verified could only ever
            # yield an incomplete verdict, so it is refused rather than skipped.
            raise CriterionIdentityError(
                "bound criteria have no verification expectation: "
                f"{missing}"
            )
        for criterion_id in sorted(provided):
            identity = self.identity_for(criterion_id)
            if identity is None:
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} is not bound to this run"
                )
            frozen = self.expectations.get(criterion_id)
            if frozen is None:
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} has no frozen expectation"
                )
            derived = _expectation_payload(expectations[criterion_id])
            if _expectation_payload(frozen) != derived:
                raise CriterionIdentityError(
                    f"expectation for criterion {criterion_id!r} does not match the "
                    "frozen binding"
                )

    def validate_criteria(self, criteria: Iterable[object]) -> None:
        """Prove that an acceptance criterion set is exactly this binding.

        The binding is the canonical criterion identity, so a request whose
        acceptance criteria differ from it in any direction - a missing criterion
        or an extra one - is refused. Ownership and fingerprints are re-checked
        against the trusted ids, never against position.
        """
        if isinstance(criteria, (str, bytes)) or not isinstance(criteria, Iterable):
            raise CriterionIdentityError("criteria must be an iterable of criteria")
        bound = set(self.criterion_ids)
        provided: set[str] = set()
        delivered: dict[str, object] = {}
        for criterion in criteria:
            criterion_id = getattr(criterion, "criterion_id", None)
            if not isinstance(criterion_id, str) or not criterion_id.strip():
                raise CriterionIdentityError(
                    "acceptance criteria must carry a non-empty criterion_id"
                )
            provided.add(criterion_id.strip())
            delivered[criterion_id.strip()] = criterion
        missing = sorted(bound - provided)
        if missing:
            raise CriterionIdentityError(
                f"acceptance criteria omit bound criteria: {missing}"
            )
        unknown = sorted(provided - bound)
        if unknown:
            raise CriterionIdentityError(
                f"acceptance criteria name criteria that are not bound: {unknown}"
            )
        for criterion_id in sorted(provided):
            identity = self.identity_for(criterion_id)
            if identity is None:
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} is not bound to this run"
                )
            if identity.task_id != self.task_identity.task_id:
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} belongs to another task"
                )
            frozen_content = self.criterion_contents.get(criterion_id)
            if frozen_content is None:
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} has no frozen content"
                )
            try:
                delivered_content = _criterion_payload(
                    delivered[criterion_id], self.task_identity.task_id
                )
            except CriterionIdentityError:
                raise
            except Exception as exc:  # noqa: BLE001 - unusable content is refused
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} content is unusable: "
                    f"{type(exc).__name__}"
                ) from exc
            if dict(frozen_content) != delivered_content:
                # Same id, different criterion: identity, description, required
                # flag, requirement, or task ownership all disagree.
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} content does not match the frozen "
                    "binding"
                )

    def validate_request(
        self,
        *,
        run_id: str,
        task_id: str,
        criteria: Iterable[object],
        expectations: Mapping[str, object],
    ) -> None:
        """The single canonical identity check for one verification attempt.

        It proves, in one place, the invariant the boundary exists for::

            bound criteria == request acceptance criteria == verified criteria

        A run whose request disagrees with its own frozen binding in any
        direction is refused, so an incomplete criterion set can never receive a
        verdict.
        """
        self.assert_belongs_to(run_id, task_id)
        self.validate_criteria(criteria)
        self.validate_expectations(expectations)

    def assert_same_identity(self, other: object) -> None:
        """Prove a later attempt keeps exactly this identity.

        A revision may change a plan and an attempt, never the task or the
        criteria it is judged by.
        """
        if not isinstance(other, RunCriterionBinding):
            raise CriterionIdentityError("binding must be a RunCriterionBinding")
        self.task_identity.assert_matches(other.task_identity)
        if self.run_id != other.run_id:
            raise CriterionIdentityError(
                f"binding belongs to run {self.run_id!r}, not {other.run_id!r}"
            )
        if set(self.criterion_ids) != set(other.criterion_ids):
            raise CriterionIdentityError(
                "criterion identity changed between attempts"
            )
        for criterion_id in self.criterion_ids:
            mine = self.identity_for(criterion_id)
            theirs = other.identity_for(criterion_id)
            if mine is None or theirs is None:
                raise CriterionIdentityError(
                    f"criterion {criterion_id!r} is missing from one binding"
                )
            if mine.criterion_fingerprint != theirs.criterion_fingerprint:
                raise CriterionIdentityError(
                    f"criterion fingerprint for {criterion_id!r} changed between "
                    "attempts"
                )

    def bounded_summary(self) -> dict[str, object]:
        """Bounded identity metadata for events. Never raw criterion text."""
        return {
            "run_id": self.run_id,
            "task_id": self.task_identity.task_id,
            "task_fingerprint": self.task_identity.task_fingerprint,
            "criterion_ids": list(self.criterion_ids),
            "criterion_fingerprints": {
                i.criterion_id: i.criterion_fingerprint
                for i in self.criterion_identities
            },
            "criterion_count": len(self.criterion_identities),
            "binding_fingerprint": self.fingerprint,
            "criterion_source": self.source,
        }


def bind_run_criteria(
    *,
    run_id: str,
    specification: object,
    criteria: Iterable[object],
    expectations: Mapping[str, object],
    source: str = "operator_composition",
) -> RunCriterionBinding:
    """Trusted composition: bind a task's criteria to one run.

    This is the only way a production binding comes into existence. It requires a
    trusted task specification, so a criterion can never be created without the
    task that owns it.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise CriterionIdentityError("run_id must be a non-empty string")
    task_identity = TaskIdentity.from_specification(specification)

    if not isinstance(expectations, Mapping) or not expectations:
        raise CriterionIdentityError(
            "a run binding requires at least one verification expectation"
        )

    criteria = tuple(criteria)
    if not criteria:
        raise CriterionIdentityError("a run binding requires at least one criterion")

    identities: list[CriterionIdentity] = []
    for criterion in criteria:
        criterion_id = getattr(criterion, "criterion_id", "")
        if criterion_id not in expectations:
            raise CriterionIdentityError(
                f"criterion {criterion_id!r} has no verification expectation, so it "
                "could never be evaluated"
            )
        identities.append(
            CriterionIdentity.derive(
                criterion=criterion,
                task_id=task_identity.task_id,
                expectation=expectations[criterion_id],
            )
        )

    return RunCriterionBinding(
        run_id=run_id.strip(),
        task_identity=task_identity,
        criterion_identities=tuple(identities),
        expectations=dict(expectations),
        criterion_contents={
            str(getattr(criterion, "criterion_id", "")).strip(): _criterion_payload(
                criterion, task_identity.task_id
            )
            for criterion in criteria
        },
        source=source,
    )


def _criterion_payload(criterion: object, task_id: object) -> dict[str, object]:
    """Canonical, comparable projection of a criterion's own content."""
    criterion_id = getattr(criterion, "criterion_id", "")
    if not isinstance(criterion_id, str) or not criterion_id.strip():
        raise CriterionIdentityError("criterion_id must be a non-empty string")
    return {
        "criterion_id": criterion_id.strip(),
        "task_id": task_id.strip() if isinstance(task_id, str) else "",
        "description": str(getattr(criterion, "description", "")),
        "required": bool(getattr(criterion, "required", True)),
        "requirement_id": str(getattr(criterion, "requirement_id", "")),
    }


def _expectation_payload(expectation: object | None) -> dict[str, object]:
    """Canonical projection of a verification expectation."""
    if expectation is None:
        return {}
    return {
        "relative_path": str(getattr(expectation, "relative_path", "")),
        "exists": bool(getattr(expectation, "exists", False)),
        "sha256": getattr(expectation, "sha256", None),
    }


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)
