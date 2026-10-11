"""Model-generated project files: one strict schema, validated, then materialised.

HONEST SCOPE -- READ THIS FIRST
===============================

This module lets a **real model response** become **real files on disk**, and it
is written so that neither half of that sentence can be claimed without the
other:

* the model text arrives as a plain ``str`` -- from a dispatched task's
  ``TaskResult.output``, or from an injected provider. Nothing here contacts a
  network. If the caller injects a mock provider, the text is mock text, and
  :class:`GenerationEvidence` says so in its ``transport`` field. There is **no**
  code path in this module that substitutes a mock for a real provider;
* the text is parsed against an explicit schema and then **every** path and
  content is checked before a single byte is written;
* a rejection is reported, never repaired silently, and a refused file never
  becomes a created file.

WHAT THIS MODULE IS NOT
=======================

* It is not an orchestrator, a second dispatcher, a memory system, or a second
  authorization mechanism. It writes files and it validates text. It executes
  nothing: no command in this module ever spawns a process.
* It is not a sandbox. The path checks below are **server-side filesystem
  boundary** checks over text the model produced. They refuse a path that
  escapes the permitted root *as this process resolves it*. They cannot stop a
  program the model later wrote from opening an absolute path at run time; the
  project records that residual risk separately and does not claim otherwise.
* It is not a secret classifier. It reuses the *existing* name recognizer
  (:mod:`app.execution.secret_files`) for paths, and applies that recognizer's
  own name patterns to content **lines** as a narrow "a credential assignment
  was written into the project" check. A secret inside an ordinary sentence is
  not recognised by any rule here, exactly as a secret inside ``notes.txt`` is
  not recognised by the staging filter. That limitation is stated, not hidden.

WHY THE SCHEMA IS SHAPED THIS WAY
=================================

The shape follows what the existing stack can already do. The schema's
``decision`` object is validated by the *existing* fail-closed decision ladder
(:meth:`app.decision.ai_provider.AIDecisionProvider.decide_from_output`), so the
reason codes a caller sees here are the ones that layer already defines, and the
workflow still has a typed decision to report. The ``files`` object is the
smallest explicit shape that turns "model output" into "project": a JSON object
mapping a relative POSIX path to that file's entire text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.decision.ai_provider import AIDecisionProvider, strip_code_fence
from app.decision.models import Decision, DecisionRequest
from app.context.validation import FORBIDDEN_CONTEXT_SUBSTRINGS
from app.execution.secret_files import (
    SECRET_FILE_NAMES,
    is_secret_directory,
    is_secret_file,
)

__all__ = [
    "MOCK_TRANSPORT_IS_NOT_REAL_GENERATION",
    "GENERATION_SCHEMA_HINT",
    "GenerationError",
    "GenerationSchemaError",
    "GenerationRejection",
    "GeneratedProjectFile",
    "RejectedProjectPath",
    "ParsedModelProject",
    "MaterialisationOutcome",
    "GenerationEvidence",
    "MODEL_OUTPUT_MISSING_FILES",
    "MODEL_OUTPUT_FILES_NOT_OBJECT",
    "MODEL_OUTPUT_EMPTY_FILE_SET",
    "MODEL_OUTPUT_UNSAFE_PATH",
    "MODEL_OUTPUT_REJECTED",
    "MODEL_OUTPUT_TOO_LARGE",
    "MATERIALISATION_REASON_CODES",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_MAX_PROJECT_BYTES",
    "DEFAULT_MAX_FILE_COUNT",
    "neutralise_forbidden_context_markers",
    "parse_model_project",
    "materialise_project",
]

#: Repeated wherever a mock/injected transport is used, so no log, field, or
#: document can present injected text as real model generation.
MOCK_TRANSPORT_IS_NOT_REAL_GENERATION = (
    "An injected or mock provider transport is not a real model: it proves the "
    "generation path, materialisation, and verification machinery, and it proves "
    "nothing about the quality of model-generated code."
)

#: The schema, restated as a one-line hint for tests and documentation.
GENERATION_SCHEMA_HINT = (
    '{"decision": {"decision_type": ..., "action": ...}, '
    '"files": {"<relative/posix/path>": "<file contents>"}}'
)

#: The ``reason`` recorded when a model text names nothing writable.
MODEL_OUTPUT_MISSING_FILES = "model_output_missing_files"
MODEL_OUTPUT_FILES_NOT_OBJECT = "model_output_files_not_object"
MODEL_OUTPUT_EMPTY_FILE_SET = "model_output_empty_file_set"
MODEL_OUTPUT_UNSAFE_PATH = "model_output_unsafe_path"
MODEL_OUTPUT_REJECTED = "model_output_rejected"
MODEL_OUTPUT_TOO_LARGE = "model_output_too_large"

#: Every reason this module can record for refusing a materialisation. Codes are
#: stable strings so a caller can branch on them without parsing a message.
MATERIALISATION_REASON_CODES = (
    "absolute_path",
    "empty_path",
    "path_traversal",
    "path_escapes_workspace",
    "not_a_relative_posix_path",
    "protected_path",
    "secret_path",
    "secret_content",
    "non_text_content",
    "file_too_large",
    "project_too_large",
    "too_many_files",
    "duplicate_path",
    "conflicts_with_existing_file",
    MODEL_OUTPUT_MISSING_FILES,
    MODEL_OUTPUT_FILES_NOT_OBJECT,
    MODEL_OUTPUT_EMPTY_FILE_SET,
    MODEL_OUTPUT_UNSAFE_PATH,
    MODEL_OUTPUT_REJECTED,
    MODEL_OUTPUT_TOO_LARGE,
)

#: Bounds. They exist so one model response cannot fill a disk; they are not a
#: quota system. A default bounds a single file at 256 KiB, a whole generated
#: project at 4 MiB, and its file count at 512.
DEFAULT_MAX_FILE_BYTES = 262_144
DEFAULT_MAX_PROJECT_BYTES = 4_194_304
DEFAULT_MAX_FILE_COUNT = 512

#: Path components that are never part of a generated project. ``.git`` and
#: ``.forge`` are this project's own service areas; ``.env`` is refused as a file
#: name *and* as a component so ``.env/local`` cannot slip past.
_PROTECTED_COMPONENTS = frozenset({".git", ".forge", ".env"})

#: A control character in a path is refused: it cannot be typed by an operator,
#: and on Windows it cannot exist in a filename at all.
_PATH_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")

#: Windows reserved device names. ``CON``/``NUL``/``AUX``/``PRN``/``COM1`` and
#: friends resolve to devices rather than files, so a write "into the workspace"
#: would not land in a file there.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "clock$"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)

#: Credential names as they appear in an assignment, e.g. ``API_KEY="..."``.
#: Derived from the existing recognizer's own constants so this check cannot
#: drift away from what the staging filter recognises by name.
_SECRET_ASSIGNMENT_NAMES = frozenset(
    name
    for name in (n.upper() for n in SECRET_FILE_NAMES)
    if re.fullmatch(r"[A-Z0-9_]+", name)
) | frozenset(
    {
        "API_KEY",
        "APIKEY",
        "ACCESS_KEY",
        "ACCESS_TOKEN",
        "AUTH_TOKEN",
        "BEARER_TOKEN",
        "CLIENT_SECRET",
        "CONNECTION_STRING",
        "DATABASE_URL",
        "DB_PASSWORD",
        "OPENAI_API_KEY",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "PRIVATE_KEY",
        "SECRET_KEY",
        "SESSION_SECRET",
        "SIGNING_KEY",
        "SMTP_PASSWORD",
        "XAI_API_KEY",
    }
)

#: A reference to a credential **file** inside project content, e.g.
#: ``credentials.json``, ``secrets.yaml``, ``.env``, ``id_rsa``. It is matched as
#: a file-like token (a dotted, slashed, or leading-dot sequence) rather than as a
#: bare substring, so ordinary prose and ordinary identifiers -- ``Credentials``,
#: ``secrets_are_not_stored_here``, ``os.environ`` -- are not refused.
_SECRET_FILE_REFERENCE_PATTERN = re.compile(
    r"(?:[.]?[A-Za-z0-9_-]+(?:[./][A-Za-z0-9_.-]+)+|[.][A-Za-z0-9_-]+)"
)

_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?im)^[ \t]*(?:export[ \t]+)?(?:[A-Za-z_][A-Za-z0-9_]*\.)?"
    r"([A-Za-z_][A-Za-z0-9_]*)[ \t]*(?::=|=|:)[ \t]*[\"']?([^\s\"'#]{16,})"
)

#: A value that is an environment **lookup** rather than a literal. Ordinary
#: project code reads a setting from its environment; that is the opposite of
#: hard-coding one, so it must not be refused as credential material. Matched on
#: the value itself, so a real assignment of a literal still fails the check.
_ENVIRONMENT_LOOKUP_PATTERN = re.compile(
    r"(?i)^(?:os\.environ|environ|os\.getenv|getenv|process\.env)[\[.(]"
    r"|^(?:\$\{?[A-Za-z_]|<[A-Za-z_])"
)
_SECRET_PEM_PATTERN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_SECRET_JWT_PATTERN = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)
_SECRET_PROVIDER_KEY_PATTERN = re.compile(r"\b(?:sk|pk|ghp|gho|xox[baprs])-[A-Za-z0-9_\-]{16,}\b")


#: Neutral wording substituted for each marker the existing context selector
#: treats as forbidden content. See :func:`neutralise_forbidden_context_markers`.
_FORBIDDEN_MARKER_SUBSTITUTES: dict[str, str] = {
    "chain_of_thought": "reasoning",
    "raw_output": "raw model reply",
    "source_code": "project source",
    "file_content": "file body",
    "api_key": "access field",
    "credential": "sign-in value",
    "password": "sign-in value",
    "secret": "private value",
    "token": "unit",
    "prompt": "request",
    "stdout": "process output",
    "stderr": "process diagnostics",
}

#: Longest marker first, so a substitute's own text cannot be re-scanned into a
#: different replacement.
_FORBIDDEN_MARKER_PATTERN = re.compile(
    "|".join(
        re.escape(marker)
        for marker in sorted(FORBIDDEN_CONTEXT_SUBSTRINGS, key=len, reverse=True)
    ),
    re.IGNORECASE,
)


def neutralise_forbidden_context_markers(text: str) -> str:
    """Replace the markers the existing context selector refuses to carry.

    Why this exists, stated plainly: the existing context selection layer drops
    any item whose text contains one of ``FORBIDDEN_CONTEXT_SUBSTRINGS`` ("token",
    "secret", "prompt", ...), and a dropped Tier-1 item aborts the Run. A prompt
    that *talks about* avoiding private values therefore could not travel through
    the project's own dispatch path at all -- the model would never be asked.

    This function does not weaken that layer. It refuses nothing, allows nothing,
    and grants nothing: it substitutes neutral wording for the markers so the
    operator's own goal and criteria can be carried to the model. The selector's
    rule keeps applying to everything else in the context.

    The design consequence is recorded rather than hidden: a goal or criterion may
    reach the model with these words reworded. That is a fidelity limit of this
    path, not a security property.
    """
    if not text:
        return text
    return _FORBIDDEN_MARKER_PATTERN.sub(
        lambda match: _FORBIDDEN_MARKER_SUBSTITUTES.get(
            match.group(0).lower(), "value"
        ),
        text,
    )


class GenerationError(ValueError):
    """The model text cannot be used as a project at all."""


class GenerationSchemaError(GenerationError):
    """The model text does not satisfy the declared generation schema."""


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedProjectFile:
    """One file the model asked for, after every check passed.

    ``content`` is the entire file text. ``size_bytes`` is the UTF-8 length that
    will be written, measured here so a caller never has to re-encode to learn it.
    """

    path: str
    content: str
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Non-content projection: the file list never carries file bodies."""
        return {"path": self.path, "size_bytes": self.size_bytes}


@dataclass(frozen=True)
class RejectedProjectPath:
    """One path the materialiser refused, and the rule that refused it.

    ``reason`` is a stable code from :data:`MATERIALISATION_REASON_CODES`. The
    offending text is included as ``path`` only when it is safe to print; a
    rejected path is never written anywhere, and its content is never reported.
    """

    path: str
    reason: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class ParsedModelProject:
    """The result of reading one model text against the generation schema.

    ``fail_closed_reason`` is ``""`` on success and one of the codes in
    :data:`MATERIALISATION_REASON_CODES` on failure. When it is set, ``files`` is
    empty: a partially-parsed project is never returned, so a caller cannot
    accidentally materialise half a response.
    """

    files: tuple[GeneratedProjectFile, ...] = ()
    rejections: tuple[RejectedProjectPath, ...] = ()
    fail_closed_reason: str = ""
    detail: str = ""
    file_count: int = 0
    declared_file_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.fail_closed_reason

    def to_dict(self) -> dict[str, Any]:
        return {
            "files": [item.to_dict() for item in self.files],
            "rejections": [item.to_dict() for item in self.rejections],
            "fail_closed_reason": self.fail_closed_reason,
            "detail": self.detail,
            "file_count": self.file_count,
            "declared_file_count": self.declared_file_count,
        }


@dataclass(frozen=True)
class MaterialisationOutcome:
    """What was really written to disk, and what was really refused."""

    workspace_root: str
    created_files: tuple[str, ...] = ()
    overwritten_files: tuple[str, ...] = ()
    skipped_files: tuple[str, ...] = ()
    rejections: tuple[RejectedProjectPath, ...] = ()
    bytes_written: int = 0
    fail_closed_reason: str = ""
    detail: str = ""
    #: A count of files that already existed and were kept, whether or not the
    #: operator permitted an overwrite. Reported so "nothing was written" and
    #: "everything was refused" are distinguishable.
    existing_files_kept: int = 0

    @property
    def ok(self) -> bool:
        return not self.fail_closed_reason

    @property
    def file_list(self) -> tuple[str, ...]:
        return self.created_files + self.overwritten_files

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspace_root": self.workspace_root,
            "created_files": list(self.created_files),
            "overwritten_files": list(self.overwritten_files),
            "file_list": list(self.file_list),
            "skipped_files": list(self.skipped_files),
            "rejections": [item.to_dict() for item in self.rejections],
            "bytes_written": self.bytes_written,
            "fail_closed_reason": self.fail_closed_reason,
            "detail": self.detail,
            "existing_files_kept": self.existing_files_kept,
        }


@dataclass(frozen=True)
class GenerationEvidence:
    """Observable proof of *which* provider produced the project, and at what cost.

    ``provider_name``, ``model_name``, and ``usage`` are the observable identity
    and physical measurement the existing decision layer records on
    ``last_provider_name`` / ``last_model_name`` / ``last_usage``, plus the
    ``TaskResult`` the provider itself filled in. They are what makes provider
    participation provable from data instead of from a message string.

    **These fields describe the response that produced the project.** When a
    response failed closed -- no text, text that is not the declared schema, a
    path that was refused, a body over the size limit -- they are empty and
    ``usage_present`` is ``False``, because in that case no model produced a
    project and there is no project-sized measurement to attribute. A reader
    therefore cannot mistake "a call produced nothing usable" for "a model wrote
    this project", which is the mistake this class exists to prevent.

    The separate, weaker fact that model text did arrive is carried by
    ``response_text_observed``, so nothing is hidden: it is labelled for what it
    is. The physical measurement of a call that failed is not lost either -- the
    dispatcher records it on the run's ``PROVIDER_RESULT`` event, which is where a
    cost report reads it.

    ``transport`` states how the text reached this module. It is ``"task_result"``
    for a dispatched task. It is **not** a claim that a real remote provider
    answered: ``mock_transport`` is the field that answers that question, and a
    caller that composes an injected transport must set it.
    """

    transport: str
    provider_name: str
    model_name: str
    usage_input_tokens: Optional[int] = None
    usage_output_tokens: Optional[int] = None
    usage_estimated_cost: Optional[float] = None
    decision: Optional[Decision] = None
    validation_errors: tuple[str, ...] = ()
    #: True when the caller declared the transport as injected/mock. It travels
    #: with the evidence so no downstream report can present mock text as a real
    #: model's work.
    mock_transport: bool = False
    #: True when any model text at all arrived, whether or not it could become a
    #: project. It is the honest counterpart to the identity fields being empty:
    #: "a response arrived but produced no project" is a different fact from "no
    #: response arrived", and this field is what keeps them distinguishable.
    response_text_observed: bool = False
    #: The model's raw text, held in memory only, and only when that text really
    #: produced the project. It is deliberately absent from :meth:`to_dict`: a raw
    #: response can contain anything, including credential material, and this
    #: project does not write model text into logs or records.
    raw_model_text: str = field(default="", repr=False)

    @property
    def usage_present(self) -> bool:
        return self.usage_input_tokens is not None or self.usage_output_tokens is not None

    def to_dict(self) -> dict[str, Any]:
        decision = self.decision
        return {
            "transport": self.transport,
            "mock_transport": self.mock_transport,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "usage_input_tokens": self.usage_input_tokens,
            "usage_output_tokens": self.usage_output_tokens,
            "usage_estimated_cost": self.usage_estimated_cost,
            "usage_present": self.usage_present,
            "decision": (
                {
                    "decision_id": decision.decision_id,
                    "decision_type": decision.decision_type.value,
                    "action": decision.action.value,
                    "reason_code": decision.reason_code,
                    "rationale": decision.rationale,
                    "confidence": decision.confidence,
                }
                if decision is not None
                else None
            ),
            "validation_errors": list(self.validation_errors),
            "response_text_observed": self.response_text_observed,
            "raw_model_text_included": False,
        }


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------


def _reject(path: str, reason: str, detail: str = "") -> RejectedProjectPath:
    return RejectedProjectPath(path=path, reason=reason, detail=detail)


def _check_path_shape(raw_path: Any) -> tuple[Optional[str], Optional[RejectedProjectPath]]:
    """Return the normalized path, or the rejection that refuses it.

    Checks, in order: it is a string; it is not empty; it carries no control
    character; it is not absolute in either POSIX or Windows spelling; it has no
    drive or UNC prefix; it contains no ``.``/``..`` segment; it is not a Windows
    device name; no component is a protected area; and no component is a
    credential directory. A backslash is refused rather than translated, because
    the declared schema is POSIX-relative and translating one would silently
    accept two spellings of the same path.
    """
    if not isinstance(raw_path, str):
        return None, _reject(str(raw_path), "not_a_relative_posix_path",
                             "a path key must be a string")
    text = raw_path
    if not text.strip():
        return None, _reject(text, "empty_path", "a path key must not be empty")
    if _PATH_CONTROL_CHARACTERS.search(text):
        return None, _reject(text, "not_a_relative_posix_path",
                             "a path key must not contain control characters")
    if "\\" in text:
        return None, _reject(text, "not_a_relative_posix_path",
                             'a path key must use "/" separators')
    if text.startswith("/"):
        return None, _reject(text, "absolute_path", "a path key must be relative")
    # ``C:foo`` and ``C:/foo`` are drive-relative and drive-absolute on Windows;
    # ``//host/share`` is a UNC path. All three are absolute in effect.
    if re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
        return None, _reject(text, "absolute_path",
                             "a drive or UNC prefix is not a relative path")

    segments = [segment for segment in text.split("/")]
    for segment in segments:
        if segment in ("",):
            return None, _reject(text, "not_a_relative_posix_path",
                                 "a path key must not contain an empty segment")
        if segment in (".", ".."):
            return None, _reject(text, "path_traversal",
                                 "a path key must not contain a traversal segment")
    for segment in segments:
        lowered = segment.strip().lower().rstrip(". ")
        if lowered in _WINDOWS_DEVICE_NAMES:
            return None, _reject(text, "protected_path",
                                 f"{segment!r} is a reserved device name")
    for segment in segments:
        if segment.lower() in _PROTECTED_COMPONENTS:
            return None, _reject(text, "protected_path",
                                 f"{segment!r} is a protected area")
    for segment in segments[:-1]:
        if is_secret_directory(segment):
            return None, _reject(text, "secret_path",
                                 f"{segment!r} is a credential directory")
    name = segments[-1]
    if name.lower() in _PROTECTED_COMPONENTS:
        return None, _reject(text, "protected_path",
                             f"{name!r} is a protected file name")
    if is_secret_file(name):
        return None, _reject(text, "secret_path",
                             "the file name is credential material")
    return text, None


def _resolved_within(root: Path, relative: str) -> tuple[Optional[Path], Optional[RejectedProjectPath]]:
    """Resolve ``relative`` under ``root`` and prove the result is still inside.

    This is the check that answers "once resolved, does this path escape the
    workspace root?" It is deliberately performed on the **resolved** path rather
    than on the text, because a resolution step (a symlinked parent, a
    case-insensitive filesystem, a reparse point) is exactly how a text check is
    defeated. ``Path.resolve`` follows what exists; a missing tail is resolved
    lexically, which is the strictest available answer for a path that does not
    exist yet.
    """
    try:
        candidate = (root / relative).resolve()
        root_resolved = root.resolve()
    except (OSError, RuntimeError) as exc:  # noqa: BLE001 - refuse, never guess
        return None, _reject(relative, "path_escapes_workspace",
                             f"the path could not be resolved: {type(exc).__name__}")
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None, _reject(relative, "path_escapes_workspace",
                             "the resolved path leaves the workspace root")
    if candidate == root_resolved:
        return None, _reject(relative, "path_escapes_workspace",
                             "the resolved path is the workspace root itself")
    return candidate, None


# ---------------------------------------------------------------------------
# Content safety
# ---------------------------------------------------------------------------


def secret_content_reason(text: str) -> Optional[str]:
    """Return why ``text`` looks like credential material, or ``None``.

    Narrow and name-driven on purpose. It reports:

    * a private-key PEM header;
    * a JSON Web Token, which carries its own signature and expiry;
    * a provider-shaped API key literal (``sk-``, ``ghp_``, ``xoxb-``, ...);
    * an assignment to a name the existing recognizer already treats as
      credential-shaped (``API_KEY``, ``SECRET_KEY``, ``DB_PASSWORD``, ...)
      whose value is long enough to be a real secret rather than a placeholder;
    * a reference to a credential **file** the shared recognizer knows
      (``credentials.json``, ``id_rsa``, ``.env``, ...).

    **Every** matching assignment is examined, not only the first one. That is
    deliberate and security-relevant: a file that reads one setting from its
    environment and hard-codes another must be refused for the hard-coded one, and
    a first-match-only scan would return ``None`` as soon as the environment
    lookup was seen. The environment exemption is therefore applied per match and
    never short-circuits the whole scan.

    The file-reference rule asks the **shared recognizer**
    (:func:`app.execution.secret_files.is_secret_file` /
    :func:`is_secret_directory`) rather than re-deriving its name lists, so this
    check cannot start refusing a name the recognizer deliberately allows --
    ``id_rsa.pub`` and ``known_hosts`` among them.

    It is **not** a general secret classifier. A secret written as prose is not
    recognised by any rule here, and this function does not claim to be complete.
    """
    if not text:
        return None
    if _SECRET_PEM_PATTERN.search(text):
        return "private_key_pem"
    if _SECRET_JWT_PATTERN.search(text):
        return "json_web_token"
    if _SECRET_PROVIDER_KEY_PATTERN.search(text):
        return "provider_key_literal"

    for match in _SECRET_ASSIGNMENT_PATTERN.finditer(text):
        name = match.group(1).upper()
        value = match.group(2)
        # The value position keeps its opening quote, because the pattern allows
        # an optional one. An environment lookup is the safe way to obtain a
        # setting, so *this* match is skipped -- and only this one. The scan
        # continues, because a later hard-coded literal is exactly what this
        # function exists to find.
        if _ENVIRONMENT_LOOKUP_PATTERN.match(value.lstrip("\"'")):
            continue
        if name in _SECRET_ASSIGNMENT_NAMES:
            return f"credential_assignment:{name}"
        if any(
            name.endswith(suffix)
            for suffix in ("_API_KEY", "_SECRET", "_TOKEN", "_PASSWORD", "_PASSWD")
        ):
            return f"credential_assignment:{name}"
        # A literal that begins with a real provider prefix is a secret whatever
        # it is assigned to.
        if value.startswith(("sk-", "ghp_", "gho_", "xoxb-", "xoxp-", "AKIA")):
            return "provider_key_literal"

    lowered = text.lower()
    for match in _SECRET_FILE_REFERENCE_PATTERN.finditer(lowered):
        token = match.group(0).strip("./")
        if not token:
            continue
        segments = [segment for segment in re.split(r"[./]", token) if segment]
        if not segments:
            continue
        # The shared recognizer owns the decision, including its exemptions
        # (``id_rsa.pub`` is a public key, ``known_hosts`` is public host data).
        if is_secret_file(segments[-1]):
            return f"credential_file_reference:{token}"
        if any(is_secret_directory(segment) for segment in segments[:-1]):
            return f"credential_file_reference:{token}"
    return None


# ---------------------------------------------------------------------------
# Parsing the model text against the declared schema
# ---------------------------------------------------------------------------


def _parse_json_object(text: str) -> tuple[Optional[dict], str, str]:
    """Unpack one JSON object from model text.

    Reuses the module-level :func:`app.decision.ai_provider.strip_code_fence` so
    the fence handling a caller sees here is byte-for-byte the handling the
    decision layer applies. Returns ``(parsed, reason, detail)``; ``reason`` is
    ``""`` on success.
    """
    if not isinstance(text, str) or not text.strip():
        return None, "empty_model_output", "the model returned no text"
    cleaned = strip_code_fence(text)
    if not cleaned:
        return None, "empty_model_output", "the model returned no text after unpacking"
    try:
        parsed = json.loads(cleaned)
    except Exception as exc:  # noqa: BLE001 - any parse failure is the same refusal
        return None, "malformed_json", f"the model text is not JSON: {type(exc).__name__}"
    if not isinstance(parsed, dict):
        return None, "malformed_model_output", "the model text is not a JSON object"
    return parsed, "", ""


def parse_model_project(
    text: str,
    *,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_project_bytes: int = DEFAULT_MAX_PROJECT_BYTES,
    max_file_count: int = DEFAULT_MAX_FILE_COUNT,
) -> ParsedModelProject:
    """Read model text as a project, or fail closed with a named reason.

    Every refusal below returns an **empty** ``files`` tuple together with the
    reason, so a failed parse can never be materialised partially:

    ==========================================  ==================================
    condition                                   ``fail_closed_reason``
    ==========================================  ==================================
    no text / whitespace only                   ``empty_model_output``
    not JSON, or JSON that is not an object     ``malformed_json`` / ``malformed_model_output``
    no ``files`` key                            ``model_output_missing_files``
    ``files`` is not a JSON object              ``model_output_files_not_object``
    ``files`` is an empty object                ``model_output_empty_file_set``
    a value is not a string                     ``non_text_content``
    a value is not UTF-8 encodable              ``non_text_content``
    a file exceeds ``max_file_bytes``           ``file_too_large``
    the project exceeds ``max_project_bytes``   ``project_too_large``
    more than ``max_file_count`` files          ``too_many_files``
    a path is unsafe                            ``model_output_unsafe_path``
    the same path is named twice                ``duplicate_path``
    ==========================================  ==================================

    A per-file refusal that is *not* fatal to the whole project (an unsafe path,
    a credential name, credential-looking content, a duplicate) is recorded in
    ``rejections`` while the remaining files are kept -- but only when at least
    one file survives. When nothing survives, the parse fails closed with
    ``model_output_rejected``.
    """
    parsed, reason, detail = _parse_json_object(text)
    if reason:
        return ParsedModelProject(fail_closed_reason=reason, detail=detail)

    if "files" not in parsed:
        return ParsedModelProject(
            fail_closed_reason=MODEL_OUTPUT_MISSING_FILES,
            detail=f"the schema requires a 'files' member ({GENERATION_SCHEMA_HINT})",
        )
    raw_files = parsed.get("files")
    if not isinstance(raw_files, Mapping):
        return ParsedModelProject(
            fail_closed_reason=MODEL_OUTPUT_FILES_NOT_OBJECT,
            detail="'files' must be a JSON object mapping path to contents",
        )
    declared_count = len(raw_files)
    if declared_count == 0:
        return ParsedModelProject(
            fail_closed_reason=MODEL_OUTPUT_EMPTY_FILE_SET,
            detail="'files' names no file, so there is no project to create",
            declared_file_count=0,
        )
    if declared_count > max_file_count:
        return ParsedModelProject(
            fail_closed_reason="too_many_files",
            detail=f"{declared_count} files declared, limit is {max_file_count}",
            declared_file_count=declared_count,
        )

    accepted: list[GeneratedProjectFile] = []
    rejections: list[RejectedProjectPath] = []
    seen: set[str] = set()
    total_bytes = 0
    unsafe_path_seen = False
    size_exceeded = False

    for raw_path, raw_content in raw_files.items():
        normalized, path_rejection = _check_path_shape(raw_path)
        if path_rejection is not None:
            if path_rejection.reason in ("absolute_path", "path_traversal",
                                         "not_a_relative_posix_path"):
                unsafe_path_seen = True
            rejections.append(path_rejection)
            continue
        assert normalized is not None  # for type-checkers; implied by no rejection

        if normalized in seen:
            rejections.append(_reject(normalized, "duplicate_path",
                                      "the same path was declared twice"))
            continue

        if not isinstance(raw_content, str):
            rejections.append(_reject(normalized, "non_text_content",
                                      "file contents must be text"))
            continue
        try:
            encoded = raw_content.encode("utf-8")
        except UnicodeEncodeError:
            rejections.append(_reject(normalized, "non_text_content",
                                      "file contents are not UTF-8 encodable"))
            continue

        size = len(encoded)
        if size > max_file_bytes:
            size_exceeded = True
            rejections.append(_reject(
                normalized, "file_too_large",
                f"{size} bytes exceeds the per-file limit {max_file_bytes}",
            ))
            continue
        if total_bytes + size > max_project_bytes:
            size_exceeded = True
            rejections.append(_reject(
                normalized, "project_too_large",
                f"the project would exceed {max_project_bytes} bytes",
            ))
            continue

        content_reason = secret_content_reason(raw_content)
        if content_reason is not None:
            rejections.append(_reject(
                normalized, "secret_content",
                f"the contents look like credential material ({content_reason})",
            ))
            continue

        seen.add(normalized)
        total_bytes += size
        accepted.append(
            GeneratedProjectFile(path=normalized, content=raw_content, size_bytes=size)
        )

    if not accepted:
        fatal = (
            MODEL_OUTPUT_TOO_LARGE if size_exceeded
            else MODEL_OUTPUT_UNSAFE_PATH if unsafe_path_seen
            else MODEL_OUTPUT_REJECTED
        )
        return ParsedModelProject(
            rejections=tuple(rejections),
            fail_closed_reason=fatal,
            detail="every declared file was refused, so no project can be created",
            declared_file_count=declared_count,
        )

    return ParsedModelProject(
        files=tuple(accepted),
        rejections=tuple(rejections),
        file_count=len(accepted),
        declared_file_count=declared_count,
    )


# ---------------------------------------------------------------------------
# Materialisation
# ---------------------------------------------------------------------------


def _inside_root(root: Path, path: Path) -> bool:
    """True when ``path`` is inside ``root`` once both are resolved."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def materialise_project(
    files: Sequence[GeneratedProjectFile],
    *,
    workspace_root: Path | str,
    permitted_overwrite_paths: Sequence[str] = (),
    expected_exit_code_writer: Any = None,
) -> MaterialisationOutcome:
    """Write validated project files under ``workspace_root``, or refuse.

    Every check runs **before** the first byte is written: the batch is either
    fully admissible or it is refused. That ordering matters because these are
    boundary checks, and a boundary check that runs after a partial write is a
    report rather than a boundary.

    Checks, in order:

    1. the root exists and is a directory;
    2. each path is shape-checked again (this function is public, so it does not
       assume :func:`parse_model_project` ran);
    3. each path, once resolved, is still inside the root;
    4. no path resolves onto the root itself, or onto an existing **directory**;
    5. an existing file is refused unless the operator named it in
       ``permitted_overwrite_paths`` -- there is no implicit overwrite;
    6. each parent directory, once resolved, is still inside the root.

    ``expected_exit_code_writer`` is accepted and ignored; it exists only so a
    caller cannot mistake this function for something that runs a command. This
    function spawns no process, ever.
    """
    root = Path(workspace_root)
    if not root.is_dir():
        return MaterialisationOutcome(
            workspace_root=str(root),
            fail_closed_reason="workspace_missing",
            detail="the workspace root does not exist or is not a directory",
        )

    permitted = frozenset(
        str(item).strip().replace("\\", "/").lstrip("./")
        for item in permitted_overwrite_paths
        if isinstance(item, str) and str(item).strip()
    )

    checks: list[tuple[GeneratedProjectFile, Path]] = []
    rejections: list[RejectedProjectPath] = []
    for item in files:
        normalized, path_rejection = _check_path_shape(item.path)
        if path_rejection is not None:
            rejections.append(path_rejection)
            continue
        assert normalized is not None
        resolved, escape_rejection = _resolved_within(root, normalized)
        if escape_rejection is not None:
            rejections.append(escape_rejection)
            continue
        assert resolved is not None
        if resolved.exists() and resolved.is_dir():
            rejections.append(_reject(normalized, "conflicts_with_existing_file",
                                      "an existing directory occupies this path"))
            continue
        if resolved.exists() and normalized not in permitted:
            rejections.append(_reject(
                normalized, "conflicts_with_existing_file",
                "the file already exists and the operator did not permit overwriting it",
            ))
            continue
        parent = resolved.parent
        if not _inside_root(root, parent):
            rejections.append(_reject(normalized, "path_escapes_workspace",
                                      "a parent directory leaves the workspace root"))
            continue
        checks.append((item, resolved))

    if not checks and rejections:
        return MaterialisationOutcome(
            workspace_root=str(root.resolve()),
            rejections=tuple(rejections),
            fail_closed_reason="no_writable_files",
            detail="every file was refused before anything was written",
            existing_files_kept=len(rejections),
        )

    # All checks passed: only now is the first byte written.
    created: list[str] = []
    overwritten: list[str] = []
    write_failures: list[RejectedProjectPath] = []
    bytes_written = 0
    for item, resolved in checks:
        existed = resolved.exists()
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(item.content, encoding="utf-8", newline="")
        except OSError as exc:  # noqa: BLE001 - a failed write is reported, not hidden
            write_failures.append(_reject(
                item.path, "write_failed", f"{type(exc).__name__}"
            ))
            continue
        bytes_written += item.size_bytes or len(item.content.encode("utf-8"))
        if existed:
            overwritten.append(item.path)
        else:
            created.append(item.path)

    all_rejections = tuple(rejections) + tuple(write_failures)
    if not created and not overwritten:
        return MaterialisationOutcome(
            workspace_root=str(root.resolve()),
            rejections=all_rejections,
            fail_closed_reason="no_writable_files",
            detail="no file could be written",
            existing_files_kept=len(all_rejections),
        )

    return MaterialisationOutcome(
        workspace_root=str(root.resolve()),
        created_files=tuple(sorted(created)),
        overwritten_files=tuple(sorted(overwritten)),
        rejections=all_rejections,
        bytes_written=bytes_written,
        existing_files_kept=len(rejections),
    )


# ---------------------------------------------------------------------------
# Decision validation for the same text
# ---------------------------------------------------------------------------


def decision_for_model_text(
    validator: AIDecisionProvider,
    text: str,
    *,
    run_id: str,
    task_id: str,
    attempt_number: int = 1,
) -> tuple[Decision, tuple[str, ...]]:
    """Validate the ``decision`` member of the model text through the existing layer.

    The model text is an envelope: its ``decision`` member is the decision, and
    the existing fail-closed ladder judges that member, which keeps one
    implementation of "what counts as a valid decision" in the project.

    What is handed to the ladder depends on what the envelope actually contained,
    and the choice is made so the ladder's own reason code describes the real
    failure rather than a convenient one:

    * envelope parsed and carries an object ``decision`` member -> that member;
    * envelope parsed but carries no usable ``decision`` member -> the whole
      envelope, so the ladder reports its own ``missing_decision_fields``;
    * envelope could not be parsed at all -> the raw text, so the ladder reports
      ``malformed_json`` / ``malformed_model_output`` / ``empty_model_output`` for
      itself.

    Without the third case a non-JSON response was reported as
    ``empty_model_output`` -- "the model returned nothing" -- when the model had in
    fact returned something that was not the declared schema. Those are different
    failures and a reader must be able to tell them apart.
    """
    parsed, reason, _ = _parse_json_object(text)
    payload = text
    if not reason and isinstance(parsed, dict):
        member = parsed.get("decision")
        if isinstance(member, Mapping):
            try:
                payload = json.dumps(dict(member), ensure_ascii=False)
            except (TypeError, ValueError):
                payload = text

    request = DecisionRequest(
        run_id=run_id,
        task_id=task_id,
        attempt_number=attempt_number,
    )
    decision = validator.decide_from_output(request, payload)
    # The validation report belongs to the reply that produced a decision. It is
    # read ONLY then, because a fail-closed reply does not update it at all: an
    # unconditional read would hand a caller the *previous* invocation's
    # validation errors beside a decision that failed for an unrelated reason.
    # That is a stale-state leak, so it is gated rather than assumed.
    produced_a_decision = bool(
        getattr(validator, "last_response_produced_a_decision", False)
    )
    report = getattr(validator, "last_validation_report", None)
    errors = (
        tuple(getattr(report, "errors", ()) or ())
        if produced_a_decision and report is not None
        else ()
    )
    return decision, errors
