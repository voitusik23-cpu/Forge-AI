"""What Forge checked, what it assumed, and what it does not know.

Why this module exists
----------------------

A curated reverse-engineering prompt library states one discipline the product was
missing: **every claim carries its evidence, and facts, inferences and unknowns are
separated**. Forge used to report «готово» on the strength of its own summary, so a
wrong claim was indistinguishable from a verified one. This module is that
discipline as code, not as prose.

Three lists, and the rule that separates them
---------------------------------------------

* ``verified`` -- only facts the *system itself measured*:

  - a file was written, with its path, size and SHA-256
    (``kind == "file"``);
  - ``py_compile``/the run's own verification passed, with the command
    (``kind == "verification"``);
  - the acceptance gate passed, with its verdict (``kind == "acceptance"``);
  - the server answered, with the URL and the HTTP status
    (``kind == "server"``);
  - the project version advanced to ``v N`` (``kind == "version"``).

* ``assumed`` -- anything taken from the model's text (its summary, its ``I fixed
  X``, its plan). Every entry carries :data:`ASSUMED_BASIS`, so a reader can see
  that the sentence is quoted, not checked.
* ``unknown`` -- what this run could not establish: no files, no verification, no
  live run, a failed comparison. An unknown is named, never hidden.

The rules are enforced here, in code:

* a file is verified **only** when its path, size and 64-hex digest are all
  present -- a path alone proves nothing;
* verification and acceptance are never verified without at least one proven file,
  because there is nothing they could have judged;
* the run's ``outcome`` word is accepted and **never** turned into a verified
  claim: a summary of the result cannot be its own evidence. A zero-file run
  therefore has an empty ``verified`` list even when it says ``COMPLETED``;
* if nothing was measured, ``verified`` is empty -- it is never padded with the
  outcome, the plan or the answer.

``proof`` carries the raw material the lists were built from: the proven files,
the verification status and command, the acceptance status and code, the run id,
and every command that produced a verified fact. The function is pure: no clock,
no I/O, no provider, no store. The same arguments always produce the same block.

Usage
-----

    evidence = build_evidence(
        outcome=response["outcome"],
        files=[artifact.to_dict() for artifact in version.files],
        verification={"status": version.verification,
                      "command": "python -m compileall -q <folder>"},
        acceptance=version.acceptance,
        model_summary=response["model_intent"].get("summary"),
        answer_excerpt=response["answer_excerpt"],
        run={"run_id": version.run_id, "url": "", "http_status": 0},
        project={"version": version.version, "status": project.status.value},
    )
"""

from __future__ import annotations

from collections.abc import Mapping as _MappingABC
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "ASSUMED_BASIS",
    "EVIDENCE_KINDS",
    "build_evidence",
]

#: The one sentence that marks a model statement as *unchecked*. It is the owner's
#: own formulation: «сказано моделью, не проверено».
ASSUMED_BASIS = "сказано моделью, не проверено"

#: The kinds a verified entry may have. A kind outside this tuple is a modelling
#: error, not a new fact.
EVIDENCE_KINDS: Tuple[str, ...] = (
    "file",
    "verification",
    "acceptance",
    "version",
    "server",
    #: A change that was put back from its own snapshot, and the files that came
    #: back with it (their digest is the proof, not the flag).
    "rollback",
)

_VERIFICATION_PASSED = "passed"
_VERIFICATION_FAILED = "failed"
_VERIFICATION_NOT_APPLICABLE = "not_applicable"
_VERIFICATION_NOT_RUN = "not_run"
_VERIFICATION_PENDING = "pending"

_ACCEPTANCE_PASSED = "passed"

_HEX_DIGITS = frozenset("0123456789abcdef")


# --------------------------------------------------------------------------- #
# small, total readers: a malformed fact is an absent fact, never an exception
# --------------------------------------------------------------------------- #


def _field(item: object, key: str, default: object = "") -> object:
    """Read ``key`` from a mapping or an object, without raising."""

    if isinstance(item, _MappingABC):
        return item.get(key, default)
    return getattr(item, key, default)


def _text(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _digest(value: object) -> str:
    """The value as a lowercase 64-hex SHA-256, or ``""`` when it is not one."""

    text = _text(value).lower()
    if len(text) != 64:
        return ""
    if any(character not in _HEX_DIGITS for character in text):
        return ""
    return text


def _size(value: object) -> Optional[int]:
    """A non-negative integer size, or ``None`` when the value is not a size."""

    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    return number


def _positive_int(value: object) -> int:
    """A positive integer, or ``0`` for "absent / not a number"."""

    if isinstance(value, bool):
        return 0
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _status(value: object) -> str:
    """The status word of a verdict passed as a string, mapping or object."""

    word: object = value
    if isinstance(value, _MappingABC):
        word = (
            value.get("status")
            or value.get("verification")
            or value.get("verdict")
            or ""
        )
    elif not isinstance(value, str):
        word = getattr(value, "status", getattr(value, "verification", ""))
    return _text(word).lower()


def _command(value: object) -> str:
    """The command a verification caller explicitly named, or ``""``."""

    if isinstance(value, _MappingABC):
        return _text(value.get("command"))
    return _text(getattr(value, "command", ""))


def _code(value: object) -> str:
    """The machine code of an acceptance verdict, when one was published."""

    if isinstance(value, _MappingABC):
        return _text(value.get("code"))
    return _text(getattr(value, "code", ""))


# --------------------------------------------------------------------------- #
# the proven files
# --------------------------------------------------------------------------- #


def _proven_files(files: object) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Split the manifest into proven entries and paths without proof.

    A proven entry has a non-empty path, a non-negative integer size **and** a
    64-hex SHA-256. Everything else is returned as a bare path so the caller can
    report it as unproven instead of silently dropping it.
    """

    proven: List[Dict[str, Any]] = []
    unproven: List[str] = []
    if isinstance(files, (str, bytes)) or files is None:
        return proven, unproven
    try:
        items = list(files)  # type: ignore[arg-type]
    except TypeError:
        return proven, unproven
    for item in items:
        path = _text(_field(item, "path"))
        if not path:
            continue
        size = _size(_field(item, "size"))
        digest = _digest(_field(item, "sha256"))
        if size is None or not digest:
            unproven.append(path)
            continue
        proven.append(
            {
                "path": path,
                "size": int(size),
                "sha256": digest,
                "change": _text(_field(item, "change")),
                "kind": _text(_field(item, "kind")),
            }
        )
    return proven, unproven


def _python_paths(proven: Sequence[Mapping[str, Any]]) -> List[str]:
    return [str(item["path"]) for item in proven if str(item["path"]).endswith(".py")]


# --------------------------------------------------------------------------- #
# the block
# --------------------------------------------------------------------------- #


def build_evidence(
    *,
    outcome: object = "",
    files: object = (),
    verification: object = "",
    acceptance: object = "",
    model_summary: object = "",
    answer_excerpt: object = "",
    run: object = None,
    project: object = None,
    rollback: object = None,
) -> Dict[str, Any]:
    """Build the evidence block for one result.

    All arguments are keyword-only and accept a string, a mapping or an object:
    every caller in this repository has a slightly different carrier for the same
    fact, and a presentation function must not force one of them to be reshaped.

    ``outcome`` is read and deliberately **not** used to build a verified claim.
    It is here so a caller can pass the result word without tempting the next
    reader to treat it as proof; a test passes ``COMPLETED`` with no files and
    asserts an empty ``verified`` list.

    ``rollback`` is the change-regression verdict
    (:meth:`app.projects.regression.RollbackRecord.to_dict`). When a change was put
    back, **the rollback itself is the verified fact**: every restored file is listed
    with the digest taken from the file on disk after the restore, so "the tree is the
    previous version's again" is proved rather than asserted. The change that was
    rolled back goes to ``unknown`` -- none of it is on disk, so nothing about it was
    checked. A rollback that could not restore every file is an ``unknown`` too, and
    never a success.
    """

    proven, unproven = _proven_files(files)
    verified: List[Dict[str, Any]] = []
    assumed: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    commands: List[str] = []

    # ------------------------------------------------------------------ files
    for item in proven:
        verified.append(
            {
                "claim": f"файл записан: {item['path']}",
                "evidence": (
                    f"{item['path']} · {item['size']} Б · sha256 {item['sha256']}"
                ),
                "kind": "file",
            }
        )
    has_proof_of_files = bool(proven)
    if not proven and not unproven:
        unknown.append(
            {
                "item": "файлы не записаны",
                "why": "в этом запуске ни один файл не записан",
            }
        )
    if unproven:
        unknown.append(
            {
                "item": "файлы записаны без доказательства",
                "why": (
                    f"у {len(unproven)} файл(ов) нет размера или sha256: "
                    + ", ".join(unproven[:5])
                ),
            }
        )

    # ----------------------------------------------------------- verification
    verification_status = _status(verification)
    verification_command = _command(verification)
    default_command = ""
    python_paths = _python_paths(proven)
    if python_paths:
        default_command = "python -m py_compile " + " ".join(python_paths)
    command = verification_command or default_command
    if (
        verification_status == _VERIFICATION_PASSED
        and has_proof_of_files
        and command
    ):
        # A passed verdict is evidence only with the command that produced it.
        commands.append(command)
        verified.append(
            {
                "claim": "проверка пройдена",
                "evidence": command,
                "kind": "verification",
            }
        )
    elif verification_status == _VERIFICATION_PASSED:
        # A "passed" verdict with no proven file (or no command) has nothing it could
        # have judged, so it is a gap rather than a verified fact.
        unknown.append(
            {
                "item": "проверка без доказательства",
                "why": (
                    "проверка сообщает «passed», но "
                    + (
                        "ни один файл не доказан"
                        if not has_proof_of_files
                        else "команда проверки не названа"
                    )
                ),
            }
        )
    elif verification_status == _VERIFICATION_NOT_APPLICABLE:
        unknown.append(
            {
                "item": "проверка не применима",
                "why": "среди записанных файлов нет .py — проверять нечего",
            }
        )
    elif verification_status == _VERIFICATION_FAILED:
        unknown.append(
            {
                "item": "проверка не пройдена",
                "why": "py_compile завершился ошибкой; файлы запуска откачены",
            }
        )
    elif verification_status == _VERIFICATION_PENDING:
        unknown.append(
            {
                "item": "проверка ещё не выполнена",
                "why": "версия записана, но проверка не завершилась",
            }
        )
    elif verification_status in ("", _VERIFICATION_NOT_RUN):
        unknown.append(
            {
                "item": "проверка не выполнялась",
                "why": "у запуска нет результата проверки",
            }
        )
    else:
        unknown.append(
            {
                "item": "проверка в неизвестном состоянии",
                "why": f"статус проверки: {verification_status}",
            }
        )

    # ------------------------------------------------------------- acceptance
    acceptance_status = _status(acceptance)
    acceptance_code = _code(acceptance)
    if acceptance_status == _ACCEPTANCE_PASSED and has_proof_of_files:
        passed_evidence = "acceptance: passed"
        if acceptance_code:
            passed_evidence = f"{passed_evidence} ({acceptance_code})"
        verified.append(
            {
                "claim": "приёмка пройдена",
                "evidence": passed_evidence,
                "kind": "acceptance",
            }
        )
    elif acceptance_status == _ACCEPTANCE_PASSED:
        unknown.append(
            {
                "item": "приёмка без доказательства",
                "why": "приёмка сообщает «passed», но ни один файл не доказан",
            }
        )
    elif acceptance_status:
        unknown.append(
            {
                "item": "приёмка не подтверждена",
                "why": f"статус приёмки: {acceptance_status}",
            }
        )
    else:
        unknown.append(
            {
                "item": "приёмка не подтверждена",
                "why": "у запуска нет вердикта приёмки",
            }
        )

    # ---------------------------------------------------------------- version
    version_number = _positive_int(
        _field(project, "version") or _field(project, "current_version")
    )
    if version_number and has_proof_of_files:
        verified.append(
            {
                "claim": f"версия v{version_number} записана",
                "evidence": (
                    f"версия v{version_number} · файлов {len(proven)} · "
                    "sha256 в proof.files"
                ),
                "kind": "version",
            }
        )
    elif version_number:
        # The version slot exists but holds no proven file: the number is real, the
        # change it was supposed to prove is not. Say exactly that instead of
        # reporting an empty version as an advance.
        unknown.append(
            {
                "item": "версия без файлов",
                "why": (
                    f"версия v{version_number} записана, но ни один файл не доказан"
                ),
            }
        )
    else:
        unknown.append(
            {
                "item": "версия проекта не записана",
                "why": "у запуска нет проекта или номера версии",
            }
        )

    # ----------------------------------------------------------------- server
    url = _text(_field(run, "url"))
    http_status = _positive_int(
        _field(run, "http_status")
        or _field(run, "status_code")
        or _field(run, "observed_status")
    )
    has_server_answer = bool(
        url.startswith(("http://", "https://")) and http_status
    )
    if has_server_answer:
        verified.append(
            {
                "claim": f"сервер ответил: {url}",
                "evidence": f"GET {url} → {http_status}",
                "kind": "server",
            }
        )
        commands.append(f"GET {url}")
    else:
        unknown.append(
            {
                "item": "живой запуск не проверялся",
                "why": "нет ответа сервера с адресом и кодом ответа",
            }
        )

    # ----------------------------------------------------------------- rollback
    # A rolled-back change replaces what "verified" means for this result: the files
    # of the change are gone, and the one thing that *was* measured is the restore.
    rollback_facts = dict(rollback) if isinstance(rollback, _MappingABC) else {}
    rolled_back = bool(rollback_facts.get("rolled_back")) and bool(rollback_facts)
    rollback_outcome = _text(rollback_facts.get("outcome"))
    rollback_verified: List[Dict[str, Any]] = []
    if rolled_back:
        for item in _proven_files(rollback_facts.get("restored") or ())[0]:
            rollback_verified.append(
                {
                    "claim": f"файл восстановлен из снимка: {item['path']}",
                    "evidence": (
                        f"{item['path']} · {item['size']} Б · "
                        f"sha256 {item['sha256']}"
                    ),
                    "kind": "file",
                }
            )
        if not rollback_verified:
            # A rollback that named no restored file proves nothing, even when it
            # says it happened: the flag alone is a claim.
            unknown.append(
                {
                    "item": "откат без доказательства",
                    "why": "откат заявлен, но ни один восстановленный файл не назван",
                }
            )
        rollback_reason = _text(rollback_facts.get("message")) or _text(
            rollback_facts.get("reason")
        )
        verified.append(
            {
                "claim": (
                    "изменение откатано: приложение перестало запускаться, "
                    "файлы возвращены из снимка"
                ),
                "evidence": (
                    f"восстановлено файлов: {len(rollback_verified)}"
                    + (f" · {rollback_reason}" if rollback_reason else "")
                ),
                "kind": "rollback",
            }
        )
        verified.extend(rollback_verified)
        # The change itself is not on disk, so nothing about it was checked.
        unknown.append(
            {
                "item": "изменение не применено",
                "why": (
                    "правка откатана: после неё приложение перестало запускаться, "
                    "поэтому её результат на диске отсутствует"
                ),
            }
        )
    elif rollback_outcome == "rollback_failed":
        unknown.append(
            {
                "item": "откат не завершён",
                "why": (
                    "не удалось восстановить: "
                    + ", ".join(
                        _text(item)
                        for item in (rollback_facts.get("failed") or ())[:5]
                    )
                ).strip(),
            }
        )
    elif rollback_outcome == "not_verified":
        unknown.append(
            {
                "item": "запуск после правки не проверен",
                "why": (
                    _text(rollback_facts.get("reason"))
                    or "проверка не смогла запустить приложение"
                ),
            }
        )

    # ----------------------------------------------------------------- assumed
    summary = _text(model_summary)
    if summary:
        assumed.append({"claim": summary, "basis": ASSUMED_BASIS})
    excerpt = _text(answer_excerpt)
    if excerpt and excerpt != summary:
        assumed.append({"claim": excerpt, "basis": ASSUMED_BASIS})

    run_id = _text(_field(run, "run_id"))
    return {
        "verified": verified,
        "assumed": assumed,
        "unknown": unknown,
        "proof": {
            "files": proven,
            "verification": {
                "status": verification_status,
                "command": (
                    verification_command
                    or (default_command if verification_status == _VERIFICATION_PASSED else "")
                ),
            },
            "acceptance": {
                "status": acceptance_status,
                "code": acceptance_code,
            },
            "run_id": run_id,
            "commands": commands,
            "rollback": (
                {
                    "outcome": rollback_outcome,
                    "rolled_back": rolled_back,
                    "reason": _text(rollback_facts.get("reason")),
                    "restored": [dict(item) for item in _proven_files(
                        rollback_facts.get("restored") or ()
                    )[0]],
                    "failed": [_text(item) for item in (rollback_facts.get("failed") or ())],
                }
                if rollback_facts
                else None
            ),
        },
    }
