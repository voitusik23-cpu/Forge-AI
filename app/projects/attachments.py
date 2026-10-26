"""Screenshots the owner attaches to a change request.

The owner's own question produced this module: his generated app showed an honest but
useless error, and the only way to describe it to Forge was to *type* it. A screenshot is
what he actually has, so «Что сделать?» accepts one.

What an attachment is -- and is not
-----------------------------------
* It **is** an artefact kept with the project, under
  ``<project>/.forge/attachments/<timestamp>-<name>``, so the owner can look at it later
  and so a later reader of the project can see what was reported.
* It is **not** model input. No provider wired into this repository receives image bytes,
  so the composed task text says so in words
  (:data:`ATTACHMENT_NOTICE`) and the interface asks for the error text in one line.
  Pretending otherwise would be the dishonest part of this whole feature.

Four rules make storing one safe
--------------------------------
* **Images only, decided by magic bytes.** A ``.png`` name on an executable is refused;
  the extension is corrected to what the bytes really are.
* **At most** :data:`MAX_ATTACHMENT_BYTES` -- the owner's screenshot is not a payload.
* **The name is sanitised** into a plain file name: no directory, no ``..``, no trailing
  dot or space, no reserved character, no reserved Windows device name, bounded length.
* **Inside the project and nowhere else.** The folder is resolved from the project's own
  workspace, the written path is checked to stay under it, and the path that is published
  is the project-relative one, never an absolute host path.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.projects.models import ProjectError

__all__ = [
    "ATTACHMENTS_SUBDIRECTORY",
    "ATTACHMENT_NOTICE",
    "FORGE_DIRECTORY",
    "MAX_ATTACHMENTS_PER_CHANGE",
    "MAX_ATTACHMENT_BYTES",
    "AttachmentNotAnImage",
    "AttachmentRefused",
    "AttachmentTooLarge",
    "existing_attachment",
    "image_kind",
    "sanitise_filename",
    "store_attachment",
    "with_attachment_notices",
]

#: The largest single screenshot this layer will keep: 5 MB.
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024

#: How many screenshots one change request may name. Each one costs a notice line in the
#: task text, and a change request is a sentence, not a gallery.
MAX_ATTACHMENTS_PER_CHANGE = 5

#: The project's own private folder (it already holds ``launch.log`` for a generated app)
#: and the subdirectory the screenshots land in.
FORGE_DIRECTORY = ".forge"
ATTACHMENTS_SUBDIRECTORY = "attachments"

#: The sentence the model reads. It is fixed text because it is the *honest* half of the
#: feature: the image is an artefact for the owner, and the model is told in so many words
#: that it cannot see it. ``{path}`` is the published, project-relative location.
ATTACHMENT_NOTICE = (
    "Приложен скриншот: {path}. Модель не видит изображения — "
    "если на скриншоте текст ошибки, он также приложен ниже."
)

#: Longest kept stem, so one absurd file name cannot dominate a card or a task text.
MAX_FILENAME_STEM = 60

#: Magic bytes -> (kind, canonical extension). The extension of a stored file is the one
#: the bytes prove, never the one the client claimed.
_IMAGE_SIGNATURES: Tuple[Tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", ".png"),
    (b"\xff\xd8\xff", "jpeg", ".jpg"),
    (b"GIF87a", "gif", ".gif"),
    (b"GIF89a", "gif", ".gif"),
)

#: Names Windows refuses as a file stem, whatever the extension.
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)

#: Characters that are not a plain file name on at least one supported system.
_FORBIDDEN_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class AttachmentRefused(ProjectError):
    """The attachment was not kept, and this is why (a sentence the owner can read)."""

    code = "attachment_refused"


class AttachmentNotAnImage(AttachmentRefused):
    """The bytes are not an image this layer recognises."""

    code = "attachment_not_an_image"


class AttachmentTooLarge(AttachmentRefused):
    """The bytes are over :data:`MAX_ATTACHMENT_BYTES`."""

    code = "attachment_too_large"


def image_kind(data: bytes) -> Optional[Tuple[str, str]]:
    """``(kind, extension)`` proven by the bytes, or ``None`` for anything else.

    Pure: it reads the first bytes only and never touches the filesystem. The extension
    is what the file must be stored as -- a mismatch with the claimed name is exactly how
    a text file gets disguised as a screenshot.
    """

    blob = bytes(data or b"")
    for signature, kind, extension in _IMAGE_SIGNATURES:
        if blob.startswith(signature):
            return kind, extension
    # RIFF container: ``RIFF`` + 4 size bytes + ``WEBP``.
    if len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "webp", ".webp"
    return None


def sanitise_filename(name: object, extension: str) -> str:
    """One plain, safe file name with ``extension``, derived from the owner's ``name``.

    The stem is kept as far as it is readable (the chip shows it back to the owner), but
    it can never carry a directory, a ``..``, a reserved character, a trailing dot or
    space, or an unbounded length. An empty stem becomes ``screenshot``.
    """

    raw = str(name or "").strip()
    # A client may send a full path: keep only the last segment, on either separator.
    raw = re.split(r"[\\/]", raw)[-1]
    # Windows normalises Unicode names, and so must a sanitiser that compares them.
    raw = unicodedata.normalize("NFKC", raw)
    stem = os.path.splitext(raw)[0]
    stem = _FORBIDDEN_CHARACTERS.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .")
    stem = stem.lstrip(".").strip(" .")
    if stem.lower() in _RESERVED_STEMS:
        stem = f"_{stem}"
    stem = stem[:MAX_FILENAME_STEM].strip(" .")
    if not stem:
        stem = "screenshot"
    return f"{stem}{extension}"


def _attachments_folder(workspace: Any) -> Path:
    """The one folder attachments live in, proven to be under ``workspace``."""

    if workspace is None:
        raise AttachmentRefused(
            "У проекта нет папки на диске, поэтому скриншот некуда положить."
        )
    root = Path(workspace)
    folder = root / FORGE_DIRECTORY / ATTACHMENTS_SUBDIRECTORY
    try:
        resolved_root = root.resolve(strict=False)
        resolved_folder = folder.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - a path that cannot be resolved
        raise AttachmentRefused(
            "Папку проекта не удалось прочитать, поэтому скриншот не сохранён."
        ) from exc
    if resolved_folder != resolved_root and not resolved_folder.is_relative_to(
        resolved_root
    ):
        raise AttachmentRefused(
            "Скриншот можно сохранить только внутри папки проекта."
        )
    return folder


def store_attachment(
    workspace: Any,
    data: bytes,
    filename: object,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Write one screenshot into the project's own folder and describe it.

    The returned ``relative`` path is relative to the project folder
    (``.forge/attachments/<timestamp>-<name>``); the caller publishes it under the
    project's own published location, so no absolute host path reaches a payload.
    """

    blob = bytes(data or b"")
    if not blob:
        raise AttachmentNotAnImage("Пустой файл: скриншот не сохранён.")
    if len(blob) > MAX_ATTACHMENT_BYTES:
        raise AttachmentTooLarge(
            "Скриншот больше 5 МБ: уменьшите его и приложите снова."
        )
    proven = image_kind(blob)
    if proven is None:
        raise AttachmentNotAnImage(
            "Это не изображение (PNG, JPEG, GIF или WebP): приложите скриншот."
        )
    kind, extension = proven
    name = sanitise_filename(filename, extension)

    folder = _attachments_folder(workspace)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AttachmentRefused(
            "Папку для скриншотов не удалось создать, поэтому файл не сохранён."
        ) from exc

    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    target = folder / f"{stamp}-{name}"
    counter = 2
    while target.exists():
        target = folder / f"{stamp}-{counter}-{name}"
        counter += 1

    # The written path must still be a direct child of the attachments folder -- the
    # sanitised name already guarantees it, and this check is the guarantee that survives
    # a future change to the sanitiser.
    try:
        resolved_folder = folder.resolve(strict=False)
        resolved_target = target.resolve(strict=False)
    except OSError as exc:  # pragma: no cover - a path that cannot be resolved
        raise AttachmentRefused(
            "Путь для скриншота не удалось проверить, поэтому файл не сохранён."
        ) from exc
    if resolved_target.parent != resolved_folder:
        raise AttachmentRefused(
            "Скриншот можно сохранить только внутри папки проекта."
        )

    try:
        with open(target, "xb") as handle:
            handle.write(blob)
    except OSError as exc:
        raise AttachmentRefused(
            "Скриншот не удалось записать на диск, поэтому он не приложен."
        ) from exc

    return {
        # The owner's own name, sanitised: this is what the chip shows him back.
        "name": name,
        # ...and the name it really has on disk, which carries the timestamp so two
        # screenshots of one problem never overwrite each other.
        "stored_name": target.name,
        "relative": f"{FORGE_DIRECTORY}/{ATTACHMENTS_SUBDIRECTORY}/{target.name}",
        "bytes": len(blob),
        "kind": kind,
    }


def existing_attachment(workspace: Any, project_path: str, value: object) -> str:
    """The validated, project-relative reference for one already stored screenshot.

    A client sends back the path it was given. It is *re-derived* here instead of being
    trusted: it must be inside this project's own attachments folder, name one plain file
    (no separator, no ``..``), and that file must exist on disk. Anything else is refused,
    so a change request can never name ``.env`` or a file of another project.
    """

    text = str(value or "").strip().replace("\\", "/")
    prefix = (
        f"{str(project_path or '').strip().rstrip('/')}/"
        f"{FORGE_DIRECTORY}/{ATTACHMENTS_SUBDIRECTORY}/"
    )
    if not text.startswith(prefix):
        raise AttachmentRefused(
            "Приложенный файл не принадлежит этому проекту: приложите скриншот заново."
        )
    name = text[len(prefix) :]
    if not name or "/" in name or ".." in name or name in {".", ".."}:
        raise AttachmentRefused(
            "Приложенный файл не принадлежит этому проекту: приложите скриншот заново."
        )
    folder = _attachments_folder(workspace)
    try:
        present = (folder / name).is_file()
    except OSError:  # pragma: no cover - defensive
        present = False
    if not present:
        raise AttachmentRefused(
            "Приложенный скриншот не найден в проекте: приложите его снова."
        )
    return f"{prefix}{name}"


def with_attachment_notices(task_text: str, references: Sequence[str]) -> str:
    """The task text the model reads: the notices first, then the owner's request.

    The notice says «... он также приложен ниже» -- the owner's own words really are
    below it, which is why the notices come first. Nothing here ever claims that the model
    looked at the image.
    """

    kept = [str(item).strip() for item in references or () if str(item).strip()]
    if not kept:
        return str(task_text or "")
    notices = "\n".join(ATTACHMENT_NOTICE.format(path=item) for item in kept)
    body = str(task_text or "").strip()
    return f"{notices}\n\n{body}" if body else notices
