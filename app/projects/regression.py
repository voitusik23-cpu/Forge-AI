"""A change that breaks a working project is rolled back from its own snapshot.

Why this module exists
----------------------

The owner's own project (`.forge/projects-visible/Почтовый центр …`) was a **working
application**: a Python server the desktop shortcut started, and a page that answered.
A change run replaced `app.py` with an answer that exits `1`, nothing served the port,
and the shortcut showed nothing. The change run reported `COMPLETED`, the project
record said `current_version: 8 · COMPLETED`, and nobody noticed -- because the write
gate checked only that the file **compiles**, never that the application still *starts*.

Then the same project answered `200` and still looked broken: a change replaced
`index.html` with a stripped copy that no longer linked the `styles.css` and `app.js`
that were on disk, so the owner's window showed plain, unstyled text. The guard this
module had at that point asked one question -- "does `GET /` answer?" -- and the answer
was yes. That is the second measured defect, and it is why the check below is a
**page quality gate**, not a liveness check.

This module is the missing check, and it is deliberately small:

1. **Snapshot before writing.** The writer already copies every file it is about to
   modify into ``<workspace>/.forge/backups/<run_id>/`` before it writes. That copy
   *is* the pre-change snapshot; nothing here makes a second one.
2. **Proof before the change.** :func:`capture_launch_state` records whether the
   project was launchable at all -- a process entry, or a static page that really
   answered -- together with :class:`PageFacts` describing the page it served: its
   size, its title, the local assets it references, and a structural fingerprint
   (elements with an ``id``, ``<link rel=stylesheet>`` tags, ``<script src>`` tags).
   The page is read from the snapshot source (the file that is on disk *before* the
   change, which is exactly what the writer copies) or, for a static project, from the
   preview that just answered. A project that never launched is exempt: a first
   creation cannot break a launch it never had.
3. **Proof after the change.** :func:`verify_change` starts the project through the
   same supervisor the owner's button uses (:class:`app.projects.runner.LocalRunner`)
   or serves the page through the same read-only preview
   (:class:`app.projects.preview.PreviewRegistry`), and reads the page ``GET /``
   returned. The page must answer ``200``; every local asset it references must answer
   ``200``; it must not lose a reference the old page had (a stylesheet or script whose
   file is still on disk); its structural fingerprint must not collapse (``id`` count
   at least half, ``<link>``/``<script>`` counts not zero where they were not zero);
   and it must not shrink below :data:`PAGE_MIN_SIZE_RATIO` of the old page when the
   old page was a real page. No browser is required and no JavaScript is executed:
   the facts are read from the bytes, so the gate is the same for a served page and
   for whatever a process answers at ``/``.
4. **Rollback, proved.** When the page does not answer or does not survive the gate,
   every file the change wrote is restored from the snapshot (a file the change
   *created* is removed), and the restored bytes are re-read and digested. The
   rollback is a fact with a digest per file, not a claim.

Honesty rules this module keeps
-------------------------------

* It never rolls a change back on evidence that the project's **own code** did not
  cause: when the supervisor could not even start the entry (a missing interpreter,
  a stale approval, the execution chain's own refusal), the outcome is
  ``not_verified`` with the reason published -- not a rollback of a change that may be
  perfectly good. A run that starts and then fails, or that starts and never answers,
  **is** evidence about the code, and that is the case this guard acts on. A page that
  answers but lost its styles is the same kind of evidence, and it says so: the reason
  names what broke («пропали стили: styles.css больше не подключён», «не отдаётся
  app.js», «страница стала пустой»).
* A page that could not be compared -- no body was returned, so there was nothing to
  judge -- is reported as *not checked* rather than as passed: the verdict is ``kept``
  with ``page.checked: false`` and the evidence block says the quality of the page was
  not measured.
* A rollback that cannot restore every file is reported as ``rollback_failed`` with the
  paths that were not restored. It is never reported as success, and never as a
  rollback that happened.
* The message is the owner's sentence plus the reason from the run's own log; no file
  body and no credential is ever copied into it.
* Everything here is bounded: a fixed number of files, a fixed budget, a bounded log
  tail, a bounded page read and a bounded asset list, and no network outside loopback.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
import time
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote

from app.execution.paths import PathSecurityError, normalize_workspace_relative_path
from app.projects.entrypoint import (
    SERVE_KIND_STATIC,
    EntryPoint,
    resolve_entry,
)
from app.projects.launcher import READINESS_TIMEOUT_SECONDS
from app.projects.ports import MAX_FETCH_BYTES, fetch_http
from app.runtime.run_store import is_safe_run_id

__all__ = [
    "CHANGE_LAUNCH_BUDGET_SECONDS",
    "DEFAULT_LOG_TAIL_LINES",
    "LaunchSnapshot",
    "OUTCOME_KEPT",
    "OUTCOME_NOT_REQUIRED",
    "OUTCOME_NOT_VERIFIED",
    "OUTCOME_ROLLED_BACK",
    "OUTCOME_ROLLBACK_FAILED",
    "PAGE_MIN_IDS_RATIO",
    "PAGE_MIN_SIZE_RATIO",
    "PAGE_ROLLBACK_PREFIX",
    "PAGE_SIZE_THRESHOLD_BYTES",
    "PageFacts",
    "ProbeAnswer",
    "ROLLBACK_PREFIX",
    "RollbackRecord",
    "SNAPSHOT_DIRECTORY",
    "capture_launch_state",
    "default_launch_probe",
    "page_facts",
    "restore_snapshot",
    "verify_change",
]

#: Where the writer keeps the pre-change copy of every file it modifies. The same
#: value as :data:`app.agent_runtime.code_writer.BACKUP_DIRECTORY`, named here so a
#: reader of this module does not have to import the writer to learn it.
SNAPSHOT_DIRECTORY = ".forge/backups"

#: The budget a change gets to prove the project still starts. It is the launch
#: readiness timeout the product already uses
#: (:data:`app.projects.launcher.READINESS_TIMEOUT_SECONDS`, itself a little longer
#: than the supervisor's 8 s startup window) -- not a second, larger allowance
#: invented for this check.
CHANGE_LAUNCH_BUDGET_SECONDS = float(READINESS_TIMEOUT_SECONDS)

#: How many lines of the run's own log one refusal quotes.
DEFAULT_LOG_TAIL_LINES = 12

#: How many files one rollback will restore. The writer's own cap is 20 per answer;
#: this is that cap, so a rollback can never be asked for more than one run wrote.
MAX_ROLLBACK_FILES = 20

#: The owner's sentence. The reason from the log follows it in parentheses.
ROLLBACK_PREFIX = "Изменения откатаны: после правки приложение перестало запускаться"

#: The owner's sentence for the other measured failure: the application still answers,
#: but the page it serves is no longer the page it served before. What broke follows it
#: in parentheses, in the same words a person would use about a page.
PAGE_ROLLBACK_PREFIX = "Изменения откатаны: страница приложения сломалась"

#: The size below which a page is not "a page" any more: a stripped `index.html` that
#: only keeps a heading is a few hundred bytes. The shrink rule applies only to a page
#: that was above this threshold before the change, so a legitimately tiny page is
#: never penalised for being tiny.
PAGE_SIZE_THRESHOLD_BYTES = 1024

#: A page must keep at least this share of the bytes it served before the change.
PAGE_MIN_SIZE_RATIO = 0.6

#: A page must keep at least this share of its elements that carry an ``id``. Losing
#: half of the document's anchors is the measured shape of a degraded page, not an edit.
PAGE_MIN_IDS_RATIO = 0.5

#: The most assets one check will ask the server for. A page with more references than
#: this is checked on the references it lists first (document order), and the count of
#: what was actually asked is reported, never the count that was skipped.
MAX_PAGE_ASSETS = 24

#: The suffix list of a *local asset*: a reference the served page makes to a file of
#: its own. Anything else (an anchor, an absolute URL, a data URI) is not an asset and
#: is not fetched.
ASSET_SUFFIXES = (
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".bmp",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".mp4",
    ".webm",
)

#: Reference schemes that are not a file in the project, so nothing can be fetched.
_SKIPPED_SCHEMES = ("data:", "http:", "https:", "//", "mailto:", "tel:", "javascript:", "#")

#: The change was verified: the project answered after it, so it was kept.
OUTCOME_KEPT = "kept"
#: The project was not launchable before the change, so no launch was required.
OUTCOME_NOT_REQUIRED = "not_required"
#: The project could not be started by this process at all; the change is untouched.
OUTCOME_NOT_VERIFIED = "not_verified"
#: The project answered before the change and did not after it: files restored.
OUTCOME_ROLLED_BACK = "rolled_back"
#: The restore did not complete: reported as a failure, never as a success.
OUTCOME_ROLLBACK_FAILED = "rollback_failed"

#: The words a probe can answer with. ``ran`` is the one fact that separates "the
#: project's own code failed" from "this process could not run it at all".
_RAN = "ran"
_NOT_RAN = "not_ran"


def _workspace_root(workspace: object) -> Optional[Path]:
    """A ``Path`` for a workspace value, or ``None`` when it is not usable."""

    if workspace is None:
        return None
    try:
        if isinstance(workspace, (str, os.PathLike)):
            root = Path(workspace)
        else:
            root = Path(getattr(workspace, "root", workspace))
    except (TypeError, ValueError):
        return None
    return root


def _digest_of(path: Path) -> str:
    """The SHA-256 of one file, or ``""`` when it cannot be read."""

    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


# --------------------------------------------------------------------------- #
# the page: what it said before the change, and what it says after it
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PageFacts:
    """What one page *is*, read from its own bytes -- never from a browser.

    The facts are deliberately coarse: a size, the title, the local assets the page
    references, and three counts that collapse when a page is stripped (elements with
    an ``id``, ``<link rel=stylesheet>`` tags, ``<script src>`` tags). They are enough
    to tell "the same page, edited" from "a page that lost its styles, its script and
    its structure", and they need no JavaScript engine, no DOM and no screenshot.
    """

    size: int = 0
    title: str = ""
    ids: int = 0
    stylesheets: int = 0
    scripts: int = 0
    #: Workspace-relative paths of the local assets the page references, in document
    #: order, deduplicated. A reference to another origin or to a data URI is not here.
    assets: Tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        """True when no page was read at all (``size == 0``), not "a small page"."""

        return int(self.size) <= 0

    def to_dict(self) -> Dict[str, Any]:
        """The public shape: counts and names, never a body."""

        return {
            "size": int(self.size),
            "title": self.title,
            "ids": int(self.ids),
            "stylesheets": int(self.stylesheets),
            "scripts": int(self.scripts),
            "assets": list(self.assets),
        }


def _asset_path(reference: object, base: str = "") -> str:
    """The site-relative path a reference names, or ``""`` for a non-asset.

    A reference becomes an asset only when it is a **local file with an asset
    suffix**: an anchor, a query-only URL, another origin, a ``data:`` URI and a
    ``javascript:`` handler are all refused here, so the guard never fetches
    something the browser would not fetch as a file of this page.

    ``base`` is the directory the page itself is served from (empty for a page at the
    site root). A reference written ``/styles.css`` is resolved from the site root and
    one written ``styles.css`` from ``base``, exactly as a browser resolves them; the
    result is the path the server is asked for.
    """

    raw = str(reference or "").strip()
    if not raw:
        return ""
    lowered = raw.casefold()
    if lowered.startswith(_SKIPPED_SCHEMES):
        return ""
    path = raw.split("#", 1)[0].split("?", 1)[0].strip()
    if not path:
        return ""
    try:
        path = unquote(path)
    except Exception:  # noqa: BLE001 - a reference this odd is not an asset
        return ""
    path = path.replace("\\", "/")
    absolute = path.startswith("/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    if not path:
        return ""
    if not absolute and base:
        path = f"{base.rstrip('/')}/{path}"
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            # A reference that climbs out of the project is not a file of this page.
            return ""
        parts.append(part)
    path = "/".join(parts)
    if not path or not path.casefold().endswith(ASSET_SUFFIXES):
        return ""
    return path


def _page_base(entry_path: object) -> str:
    """The site-relative directory a page is served from (``""`` at the root)."""

    path = str(entry_path or "").replace("\\", "/")
    return path.rsplit("/", 1)[0] if "/" in path else ""


class _PageParser(HTMLParser):
    """Read the page's own markup: title, ids, stylesheets, scripts and references."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids = 0
        self.stylesheets = 0
        self.scripts = 0
        self.references: list[str] = []
        self._title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        name = str(tag or "").casefold()
        values = {
            str(key or "").casefold(): str(value or "")
            for key, value in (attrs or ())  # type: ignore[union-attr]
        }
        if "id" in values:
            self.ids += 1
        if name == "title":
            self._in_title = True
        if name == "link":
            rel = values.get("rel", "").casefold().split()
            href = values.get("href", "")
            if "stylesheet" in rel:
                self.stylesheets += 1
            if href:
                self.references.append(href)
            return
        if name == "script":
            src = values.get("src", "")
            if src:
                self.scripts += 1
                self.references.append(src)
            return
        if name in ("img", "source", "video", "audio", "embed", "iframe", "input"):
            src = values.get("src", "")
            if src:
                self.references.append(src)

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if str(tag or "").casefold() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(str(data or ""))

    @property
    def title(self) -> str:
        return " ".join(part.strip() for part in self._title_parts if part.strip())[:200]


def page_facts(page: object, *, base: str = "") -> PageFacts:
    """The :class:`PageFacts` of one page body (bytes or text), never raising.

    ``base`` is the directory the page is served from, used to resolve a relative
    reference the way a browser would (see :func:`_asset_path`).

    A body that is not HTML at all parses to "no markup": a size and empty counts.
    That is the honest reading -- a page that answers with JSON has no stylesheet to
    lose -- and it is what makes "the page became an error document" visible as a
    size collapse instead of as an exception.
    """

    if isinstance(page, (bytes, bytearray, memoryview)):
        body = bytes(page)
        text = body.decode("utf-8", errors="replace")
        size = len(body)
    else:
        text = str(page or "")
        size = len(text.encode("utf-8", errors="replace"))
    if size <= 0:
        return PageFacts()
    parser = _PageParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:  # noqa: BLE001 - a malformed body is still a body
        pass
    ordered: list[str] = []
    for reference in parser.references:
        path = _asset_path(reference, base)
        if path and path not in ordered:
            ordered.append(path)
    return PageFacts(
        size=size,
        title=parser.title,
        ids=parser.ids,
        stylesheets=parser.stylesheets,
        scripts=parser.scripts,
        assets=tuple(ordered),
    )


def _page_file(root: Optional[Path], entry_path: str = "") -> Optional[Path]:
    """The file a project's page is served from, read from the tree, or ``None``.

    A static entry *is* the page. A process project usually serves a root page file;
    when it generates the page in code there is no file to read before the change, and
    the caller is told so by an empty :class:`PageFacts` instead of a guess.
    """

    if root is None:
        return None
    candidates: list[str] = []
    if entry_path.casefold().endswith((".html", ".htm")):
        candidates.append(entry_path)
    candidates.extend(("index.html", "index.htm"))
    seen: set[str] = set()
    for relative in candidates:
        if relative in seen or not relative or ".." in relative.split("/"):
            continue
        seen.add(relative)
        target = root.joinpath(*relative.split("/"))
        try:
            if target.is_file() and not target.is_symlink():
                return target
        except OSError:
            continue
    return None


def _page_facts_of_file(target: Optional[Path], *, base: str = "") -> PageFacts:
    """The facts of one page file, reading at most :data:`MAX_FETCH_BYTES` of it."""

    if target is None:
        return PageFacts()
    try:
        with target.open("rb") as stream:
            return page_facts(stream.read(MAX_FETCH_BYTES), base=base)
    except OSError:
        return PageFacts()


# --------------------------------------------------------------------------- #
# what the project was before the change
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LaunchSnapshot:
    """Whether the project was launchable **before** the change, and how.

    ``launchable`` is only ever set from a fact: a process entry whose own file is on
    disk (that is what "its entry was a process" means, and the supervisor starts
    exactly that file), or a static page a read-only preview really answered on.
    Everything else -- no entry point, a missing file, an entry this build cannot run
    -- is ``launchable = False`` with the reason word kept.

    ``page`` is the page that was served before the change, read from the snapshot
    source: the root page file on disk (which is what the writer is about to copy) or
    the body the read-only preview just returned. It is empty when there is no page to
    read, and an empty page never becomes a reason to roll anything back.
    """

    launchable: bool = False
    entry_kind: str = ""
    entry_path: str = ""
    serve_kind: str = ""
    reason: str = ""
    page: PageFacts = field(default_factory=PageFacts)
    page_source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "launchable": bool(self.launchable),
            "entry_kind": self.entry_kind,
            "entry_path": self.entry_path,
            "serve_kind": self.serve_kind,
            "reason": self.reason,
            "page": self.page.to_dict(),
            "page_source": self.page_source,
        }


@dataclass(frozen=True)
class ProbeAnswer:
    """What one launch attempt of the project proved.

    ``ran`` is the split that matters: ``False`` means this process could not start
    the project's own code (a refused spawn, no interpreter), so the attempt says
    nothing about the change. ``False`` with ``ran = True`` means the project's own
    code was started and did not answer -- that is evidence.

    ``port`` and ``page`` are the answer itself: the loopback port that spoke and the
    bounded body ``GET /`` returned on it. An empty ``page`` means the body could not
    be read, and the caller reports the quality of the page as *not checked* instead
    of inventing a verdict about it.

    ``assets`` is what the server answered for each file the page references. It is
    measured by the probe because only the probe holds the server open: the page and
    its assets have to be read in the same window, or the check would ask a closed
    port. The judgement of those answers belongs to the caller.
    """

    status: int = 0
    ran: bool = False
    reason: str = ""
    log_tail: Tuple[str, ...] = ()
    port: int = 0
    page: bytes = b""
    assets: Tuple[Tuple[str, int], ...] = ()

    @property
    def answered(self) -> bool:
        return 200 <= int(self.status or 0) < 300


#: One launch attempt. It receives the workspace and the entry point, and must not
#: write anything except what a launch already writes (a log, a remembered port).
LaunchProbe = Callable[[Path, "EntryPoint"], ProbeAnswer]


def capture_launch_state(
    workspace: object,
    *,
    entry: Optional[EntryPoint] = None,
    probe: Optional[LaunchProbe] = None,
    budget: float = CHANGE_LAUNCH_BUDGET_SECONDS,
) -> LaunchSnapshot:
    """Was this project launchable **before** the change?

    The entry point is resolved from the workspace on disk (never from a manifest this
    module would have to trust), and the answer is a fact about that tree:

    * a **process** entry whose own file exists -> launchable: the supervisor starts
      exactly that file, so its presence is what "it was a process" means;
    * a **static** page -> launchable only when a read-only preview really answered
      ``GET /`` on it just now, because a page that does not serve is not launchable;
    * anything else -> not launchable, with the reason kept.

    A project with no entry point at all is the first-creation case: there is no
    launch to break, so the change path keeps its existing compile gate only.
    """

    root = _workspace_root(workspace)
    if root is None or not root.is_dir():
        return LaunchSnapshot(reason="project_workspace_missing")

    resolved = entry
    if resolved is None:
        try:
            resolved = resolve_entry((), workspace=root).entry
        except Exception:  # noqa: BLE001 - an unreadable tree is "no entry point"
            resolved = None
    if resolved is None:
        return LaunchSnapshot(reason="no_entry_point")

    kind = str(getattr(resolved, "kind", "") or "")
    path = str(getattr(resolved, "path", "") or "")
    serve_kind = str(getattr(resolved, "serve_kind", "") or "")
    reason = str(getattr(resolved, "reason", "") or "")

    if serve_kind == SERVE_KIND_STATIC:
        answer = (probe or default_launch_probe)(root, resolved)
        return LaunchSnapshot(
            launchable=answer.answered,
            entry_kind=kind,
            entry_path=path,
            serve_kind=serve_kind,
            reason="" if answer.answered else (answer.reason or "the page did not answer"),
            # The page the preview really returned is the page that was served before
            # the change: it is used as-is instead of re-reading the file it came from.
            page=(
                page_facts(answer.page, base=_page_base(path))
                if answer.answered and answer.page
                else PageFacts()
            ),
            page_source=path if answer.answered and answer.page else "",
        )

    try:
        target = root.joinpath(*path.split("/")) if path else None
    except ValueError:
        target = None
    if target is None or not target.is_file():
        return LaunchSnapshot(
            entry_kind=kind,
            entry_path=path,
            serve_kind=serve_kind,
            reason="entry_file_missing",
        )
    if reason or not tuple(getattr(resolved, "command", ()) or ()):
        return LaunchSnapshot(
            entry_kind=kind,
            entry_path=path,
            serve_kind=serve_kind,
            reason=reason or "entry_command_unavailable",
        )
    # The page a process project serves is usually a file in its own tree; reading it
    # here -- before a byte is written -- is reading the snapshot the writer will take.
    # A project that generates its page in code has no file, and its page facts stay
    # empty: the after-check then reports "not checked" instead of inventing a baseline.
    page_file = _page_file(root, path)
    page_source = ""
    if page_file is not None:
        try:
            page_source = page_file.relative_to(root).as_posix()
        except ValueError:
            page_source = ""
    return LaunchSnapshot(
        launchable=True,
        entry_kind=kind,
        entry_path=path,
        serve_kind=serve_kind,
        page=_page_facts_of_file(page_file, base=_page_base(page_source)),
        page_source=page_source,
    )


# --------------------------------------------------------------------------- #
# the real probe: the product's own supervisor and preview
# --------------------------------------------------------------------------- #


def _static_probe(root: Path, entry: EntryPoint) -> ProbeAnswer:
    """Serve the page read-only, read ``GET /`` and its body, and stop the server."""

    try:
        from app.projects.preview import PreviewRegistry
        from app.projects.ports import port_from_url
    except Exception:  # noqa: BLE001 - an importable preview is a hard requirement
        return ProbeAnswer(ran=False, reason="preview_unavailable")

    registry = PreviewRegistry()
    key = "forge-change-check"
    try:
        started = dict(registry.start(key, root, entry) or {})
    except Exception:  # noqa: BLE001 - a preview failure is not evidence about code
        return ProbeAnswer(ran=False, reason="preview_failed")
    try:
        url = str(started.get("url", "") or "")
        if not started.get("running") or url == "":
            return ProbeAnswer(
                ran=False, reason=str(started.get("reason", "") or "preview_failed")
            )
        port = port_from_url(url)
        status, body, assets = _read_page(port)
        return ProbeAnswer(
            status=int(status or 0),
            ran=True,
            reason="" if 200 <= status < 300 else "no_answer",
            port=port,
            page=body,
            assets=assets,
        )
    finally:
        try:
            registry.stop(key)
        except Exception:  # noqa: BLE001 - a preview left behind must not break a check
            pass


def _process_probe(
    root: Path, entry: EntryPoint, *, budget: float, project_id: str
) -> ProbeAnswer:
    """Start the project through the supervisor, prove ``GET /``, then stop it.

    The supervisor is the one the owner's button uses: it resolves the argv, enforces
    the approval and the containment, and discovers the port a real ``GET /``
    answered on. The launch is approved here with the digest the runner itself
    computed for this exact command, so the approval boundary is unchanged -- this
    module does not invent a second way to start a process.
    """

    try:
        from app.projects.preview import PreviewRegistry
        from app.projects.runner import (
            REFUSED,
            RUNNING,
            RUN_BLOCKED,
            TERMINAL_STATES,
            LocalRunner,
            RunTarget,
        )
    except Exception:  # noqa: BLE001
        return ProbeAnswer(ran=False, reason="runner_unavailable")

    previews = PreviewRegistry()
    runner = LocalRunner(previews=previews)
    target = RunTarget(
        project_id=project_id, workspace=root, entry=entry, project_path=""
    )
    started = False
    try:
        payload = runner.start_confirmed(
            target,
            confirm=True,
            digest=runner.digest_for(target),
            offline=False,
        )
        payload = dict(payload or {})
        state = str(payload.get("state", "") or "")
        if state in (REFUSED, RUN_BLOCKED):
            # The project's own code was never started: this is not evidence about
            # the change, so the caller must not roll anything back.
            return ProbeAnswer(
                ran=False,
                reason=str(payload.get("reason", "") or state.lower()),
                log_tail=tuple(_log_lines(payload)),
            )
        started = True
        deadline = time.monotonic() + max(0.5, float(budget))
        while time.monotonic() < deadline:
            state = str(runner.state(project_id) or "")
            if state == RUNNING:
                port = int(runner.port(project_id) or 0)
                if port:
                    status, body, assets = _read_page(port)
                    return ProbeAnswer(
                        status=int(status or 0),
                        ran=True,
                        reason="" if 200 <= status < 300 else "no_answer",
                        log_tail=tuple(_log_lines(runner.log(target))),
                        port=port,
                        page=body,
                        assets=assets,
                    )
            if state in TERMINAL_STATES:
                break
            time.sleep(0.2)
        tail = tuple(_log_lines(runner.log(target)))
        reason = "the process ended without answering"
        exit_code = runner.status(target).get("exit_code")
        if isinstance(exit_code, int):
            reason = f"процесс завершился с кодом {exit_code}"
        return ProbeAnswer(status=0, ran=started, reason=reason, log_tail=tail)
    except Exception as error:  # noqa: BLE001 - a broken probe is not a broken project
        return ProbeAnswer(ran=started, reason=f"probe_error:{type(error).__name__}")
    finally:
        try:
            runner.stop(project_id)
        except Exception:  # noqa: BLE001 - stopping is best effort
            pass
        try:
            runner.stop_all()
        except Exception:  # noqa: BLE001
            pass


def _log_lines(payload: object, limit: int = DEFAULT_LOG_TAIL_LINES) -> Tuple[str, ...]:
    """The last bounded, non-empty lines of a run payload's log."""

    lines: Sequence[Any] = ()
    if isinstance(payload, Mapping):
        log = payload.get("log")
        if isinstance(log, Mapping):
            raw = log.get("lines")
            if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
                lines = list(raw)
    kept = [str(item) for item in lines if str(item).strip()]
    return tuple(kept[-max(1, int(limit)) :])


def default_launch_probe(
    root: Path,
    entry: EntryPoint,
    *,
    budget: float = CHANGE_LAUNCH_BUDGET_SECONDS,
    project_id: str = "proj-forge-change-check",
) -> ProbeAnswer:
    """The real check: the product's own preview for a page, its runner for a process."""

    if str(getattr(entry, "serve_kind", "") or "") == SERVE_KIND_STATIC:
        return _static_probe(root, entry)
    return _process_probe(root, entry, budget=budget, project_id=project_id)


# --------------------------------------------------------------------------- #
# the rollback
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RestoredFile:
    """One file the rollback put back, with the digest that proves it."""

    path: str
    sha256: str
    size: int

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "sha256": self.sha256, "size": int(self.size)}


@dataclass
class RollbackRecord:
    """What the change-verification did, in facts.

    ``outcome`` is one of the module's five ``OUTCOME_*`` words. ``rolled_back`` is
    true **only** when every written file was restored and every restored digest
    matches the snapshot; a partial restore is ``rollback_failed``.

    ``page_checked`` says whether the served page was really judged (a body was read).
    ``degradation`` is the list of what broke, in the owner's words, and it is empty
    for a page that passed -- the two are never the same fact. ``assets`` is what the
    server answered for each reference the new page made, so "every asset answers"
    is a measurement rather than a claim.
    """

    outcome: str = OUTCOME_NOT_REQUIRED
    checked: bool = False
    launchable_before: bool = False
    launchable_after: bool = False
    http_status: int = 0
    reason: str = ""
    message: str = ""
    snapshot: str = ""
    restored: Tuple[RestoredFile, ...] = ()
    removed: Tuple[str, ...] = ()
    failed: Tuple[str, ...] = ()
    log_tail: Tuple[str, ...] = ()
    before: LaunchSnapshot = field(default_factory=LaunchSnapshot)
    page_checked: bool = False
    before_page: PageFacts = field(default_factory=PageFacts)
    after_page: PageFacts = field(default_factory=PageFacts)
    degradation: Tuple[str, ...] = ()
    assets: Tuple[Tuple[str, int], ...] = ()

    @property
    def rolled_back(self) -> bool:
        """True only for a rollback that completed and whose digests were checked."""

        return self.outcome == OUTCOME_ROLLED_BACK and not self.failed

    @property
    def status(self) -> str:
        """The word the run record stores: ``failed`` for a broken restore."""

        if self.outcome == OUTCOME_ROLLBACK_FAILED:
            return "failed"
        return "rolled_back" if self.rolled_back else self.outcome

    def to_dict(self) -> Dict[str, Any]:
        """The public shape. Digests, never contents; no absolute path."""

        return {
            "outcome": self.outcome,
            "status": self.status,
            "rolled_back": bool(self.rolled_back),
            "checked": bool(self.checked),
            "launchable_before": bool(self.launchable_before),
            "launchable_after": bool(self.launchable_after),
            "http_status": int(self.http_status or 0),
            "reason": self.reason,
            "message": self.message,
            "snapshot": self.snapshot,
            "restored": [item.to_dict() for item in self.restored],
            "removed": list(self.removed),
            "failed": list(self.failed),
            "log_tail": list(self.log_tail),
            "before": self.before.to_dict(),
            # What the page was and what it became, and -- when it broke -- why.
            "degradation": list(self.degradation),
            "page": {
                "checked": bool(self.page_checked),
                "before": self.before_page.to_dict(),
                "after": self.after_page.to_dict(),
                "assets": [
                    {"path": path, "status": int(status)}
                    for path, status in self.assets
                ],
            },
        }


def _snapshot_root(root: Path, run_id: str) -> Optional[Path]:
    if not isinstance(run_id, str) or not is_safe_run_id(run_id):
        return None
    return root / SNAPSHOT_DIRECTORY / run_id


def restore_snapshot(
    workspace: object,
    run_id: str,
    paths: Sequence[object],
) -> Tuple[Tuple[RestoredFile, ...], Tuple[str, ...], Tuple[str, ...]]:
    """Put every file of one change back; return ``(restored, removed, failed)``.

    A file the change **modified** has a copy in the snapshot directory and is written
    back from it, then re-read so the returned digest is a fact about the file that
    is on disk now. A file the change **created** has no copy, so it is removed: that
    is what "the tree before the change" means for it.

    Never raises: a path that cannot be restored is returned in ``failed``, and the
    caller reports a partial rollback as a failure. A path outside the workspace is
    refused by the repository's own normaliser rather than by a second rule here.
    """

    root = _workspace_root(workspace)
    snapshot = _snapshot_root(root, run_id) if root is not None else None
    if root is None or snapshot is None:
        return (), (), tuple(str(item) for item in paths)[:MAX_ROLLBACK_FILES]

    restored: list[RestoredFile] = []
    removed: list[str] = []
    failed: list[str] = []

    for raw in list(paths)[:MAX_ROLLBACK_FILES]:
        try:
            relative = normalize_workspace_relative_path(str(raw or ""))
        except PathSecurityError:
            failed.append(str(raw or ""))
            continue
        if relative in ("", "."):
            failed.append(str(raw or ""))
            continue
        target = root.joinpath(*relative.split("/"))
        backup = snapshot.joinpath(*relative.split("/"))
        try:
            if backup.is_file() and not backup.is_symlink():
                data = backup.read_bytes()
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".forge-restore")
                with temporary.open("wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
                digest = _digest_of(target)
                if digest != hashlib.sha256(data).hexdigest():
                    failed.append(relative)
                    continue
                restored.append(
                    RestoredFile(path=relative, sha256=digest, size=len(data))
                )
            else:
                if target.exists() or target.is_symlink():
                    target.unlink()
                removed.append(relative)
        except OSError:
            failed.append(relative)
    return tuple(restored), tuple(removed), tuple(failed)


def _rollback_message(reason: str, log_tail: Sequence[str]) -> str:
    """The owner's sentence, the reason, and the run's own log tail."""

    text = ROLLBACK_PREFIX
    if reason:
        text = f"{text} ({reason})"
    tail = [item for item in log_tail if str(item).strip()]
    if tail:
        text = text + "\n" + "\n".join(str(item) for item in tail)
    return text


def _page_rollback_message(degradation: Sequence[str]) -> str:
    """The owner's page sentence and the list of what broke, in one line."""

    detail = "; ".join(str(item) for item in degradation if str(item).strip())
    return f"{PAGE_ROLLBACK_PREFIX} ({detail})" if detail else PAGE_ROLLBACK_PREFIX


# --------------------------------------------------------------------------- #
# the quality gate: the page must not be worse than it was
# --------------------------------------------------------------------------- #

#: One loopback read of one path on the port the probe answered on: ``(status, body)``.
PageFetch = Callable[[int, str], Tuple[int, bytes]]


def _is_style(path: str) -> bool:
    return str(path).casefold().endswith(".css")


def _is_script(path: str) -> bool:
    return str(path).casefold().endswith((".js", ".mjs"))


def _read_page(
    port: int, fetch: PageFetch = None
) -> Tuple[int, bytes, Tuple[Tuple[str, int], ...]]:
    """Read ``GET /`` and every local asset it references, in one open window.

    Called by the probes while the project is on the wire. The page is read first and
    its own references decide what is asked for next, so the gate compares the assets
    *this* page names -- never a list the change could have supplied.
    """

    read = fetch or fetch_http
    status, body = read(int(port or 0), "/")
    if not 200 <= int(status or 0) < 300 or not body:
        return int(status or 0), body, ()
    facts = page_facts(body)
    assets: list[Tuple[str, int]] = []
    for item in facts.assets[:MAX_PAGE_ASSETS]:
        try:
            asset_status, _asset_body = read(int(port or 0), "/" + item)
        except Exception:  # noqa: BLE001 - a read that raises is not an answer
            asset_status = 0
        assets.append((item, int(asset_status or 0)))
    return int(status or 0), body, tuple(assets)


def _loss_word(path: str) -> str:
    """How a lost reference is said in the owner's language."""

    if _is_style(path):
        return "пропали стили"
    if _is_script(path):
        return "пропал скрипт"
    return "пропал файл"


def _file_present(root: Optional[Path], relative: str) -> bool:
    """True when the project still holds the file a reference names."""

    if root is None or not relative:
        return False
    try:
        return root.joinpath(*str(relative).split("/")).is_file()
    except OSError:
        return False


def _page_degradation(
    before: PageFacts,
    after: PageFacts,
    *,
    root: Optional[Path],
    measured: Mapping[str, int],
) -> Tuple[Tuple[str, ...], Tuple[Tuple[str, int], ...]]:
    """What got worse, in the owner's words, and what each new asset answered.

    ``measured`` is what the probe read from the live server for the files the *new*
    page references. Every rule is a *comparison* with the page that was served before
    the change, so a project that never had a stylesheet is never asked to have one,
    and a page that is simply small is never called empty. The rules are independent
    and each names its own finding: the caller joins them, so a degraded page is
    reported with all the reasons that hold rather than only the first one noticed.
    """

    reasons: list[str] = []
    answers: list[Tuple[str, int]] = []

    def add(text: str) -> None:
        if text and text not in reasons:
            reasons.append(text)

    if after.empty:
        # The server answered, but with nothing a page could be read from.
        if not before.empty:
            add("страница стала пустой")
        return tuple(reasons), tuple(answers)

    # 1. the size: a page that lost most of its bytes is not the same page
    if (
        before.size > PAGE_SIZE_THRESHOLD_BYTES
        and after.size < before.size * PAGE_MIN_SIZE_RATIO
    ):
        add(f"страница уменьшилась: {after.size} Б вместо {before.size} Б")

    # 2. the structure: losing half the document's anchors is a collapse, not an edit
    if before.ids > 0 and after.ids < before.ids * PAGE_MIN_IDS_RATIO:
        add(
            f"структура страницы разрушилась: элементов с id "
            f"{after.ids} вместо {before.ids}"
        )

    # 3. the title: a page that had one and no longer does is not the same document
    if before.title and not after.title:
        add("у страницы пропал заголовок")

    # 4. the references it had: a file still on disk must still be linked. A rename is
    #    allowed only because the old file is gone -- and then the new reference is
    #    judged by rule 6 (it has to answer 200).
    lost = [item for item in before.assets if item not in after.assets]
    for item in lost:
        if _file_present(root, item):
            add(f"{_loss_word(item)}: {item} больше не подключён")

    # 5. the counts that collapse even when no single file can be named (a stylesheet
    #    that was deleted and not replaced, a script tag that became inline prose)
    if (
        before.stylesheets > 0
        and after.stylesheets == 0
        and not any(_is_style(item) for item in lost)
    ):
        add("пропали стили: страница больше не подключает таблицу стилей")
    if (
        before.scripts > 0
        and after.scripts == 0
        and not any(_is_script(item) for item in lost)
    ):
        add("пропал скрипт: страница больше не подключает скрипт")

    # 6. what the page references now must really be served. This is the browser's own
    #    criterion -- a 404 on a referenced file is a broken page, whatever the page
    #    says -- and it is measured, not assumed: the status is kept per asset.
    for item in after.assets[:MAX_PAGE_ASSETS]:
        status = int(measured.get(item, 0) or 0)
        answers.append((item, status))
        if status != 200:
            add(f"не отдаётся {item}" + (f" (HTTP {status})" if status else ""))

    return tuple(reasons), tuple(answers)


def _snapshot_page_facts(root: Optional[Path], run_id: str) -> PageFacts:
    """The page the snapshot holds for one run, or empty facts when it holds none.

    The writer's backup directory *is* the pre-change tree: when the change rewrote a
    page file, the bytes of the page that was served before it are in there. This is
    the fallback used when the page could not be read before the write.
    """

    snapshot = _snapshot_root(root, run_id) if root is not None else None
    if snapshot is None:
        return PageFacts()
    target = _page_file(snapshot)
    return _page_facts_of_file(target)


# --------------------------------------------------------------------------- #
# the check the change path runs
# --------------------------------------------------------------------------- #


def verify_change(
    *,
    workspace: object,
    before: LaunchSnapshot,
    written_paths: Sequence[object],
    run_id: str,
    project_id: str = "proj-forge-change-check",
    budget: float = CHANGE_LAUNCH_BUDGET_SECONDS,
    probe: Optional[LaunchProbe] = None,
) -> RollbackRecord:
    """Prove the project still works -- and that its page did not get worse.

    The order is the order the facts become known:

    * not launchable before -> no launch is required and nothing is rolled back
      (``not_required``); the existing compile gate already judged the files;
    * launched, answered, and the page passed the quality gate -> ``kept``, with the
      HTTP status and the page counts that proved it;
    * launched, answered, and the page lost its styles, its script, its structure or
      its size, or references a file that does not answer -> every written file is
      restored from the snapshot (``rolled_back``) and the reasons are published;
    * could not be started by this process -> ``not_verified``: the change is left
      alone and the reason is published, because a spawn this process refused says
      nothing about the project's own code;
    * started and did not answer -> every written file is restored from the snapshot
      (``rolled_back``), or the restore is reported as ``rollback_failed``.
    """

    root = _workspace_root(workspace)
    snapshot = _snapshot_root(root, run_id) if root is not None else None
    record = RollbackRecord(
        outcome=OUTCOME_NOT_REQUIRED,
        checked=bool(before.launchable),
        launchable_before=bool(before.launchable),
        snapshot=snapshot.name if snapshot is not None else "",
        before=before,
    )
    if not before.launchable:
        record.reason = before.reason or "the project was not launchable before the change"
        return record
    if root is None or not root.is_dir():
        record.outcome = OUTCOME_NOT_VERIFIED
        record.reason = "project_workspace_missing"
        return record

    entry = None
    try:
        entry = resolve_entry((), workspace=root).entry
    except Exception:  # noqa: BLE001 - an unreadable tree has no entry point
        entry = None
    if entry is None:
        # The change removed the entry point: that is the change's own doing, so it is
        # treated exactly like "it no longer starts".
        answer = ProbeAnswer(ran=True, reason="entry_point_vanished")
    else:
        answer = (probe or _default_probe_for(project_id, budget))(root, entry)

    record.http_status = int(answer.status or 0)
    record.log_tail = tuple(answer.log_tail)
    record.launchable_after = answer.answered

    if answer.answered:
        # THE QUALITY GATE. Answering 200 is necessary and not sufficient: the page
        # has to still be the page, so its own facts are compared with the facts the
        # snapshot recorded before a byte was written.
        base = _page_base(getattr(entry, "path", "") if entry is not None else "")
        before_page = before.page
        if before_page.empty:
            # The page could not be read before the write; the snapshot still holds
            # the old bytes of any page file the change rewrote.
            from_snapshot = _snapshot_page_facts(root, run_id)
            if not from_snapshot.empty:
                before_page = from_snapshot
        after_page = page_facts(answer.page, base=base)
        record.before_page = before_page
        record.after_page = after_page
        record.page_checked = bool(answer.page) or not before_page.empty
        degradation, answers = _page_degradation(
            before_page,
            after_page,
            root=root,
            measured=dict(answer.assets or ()),
        )
        record.degradation = degradation
        record.assets = answers
        if not degradation:
            record.outcome = OUTCOME_KEPT
            return record
        # The page broke: fall through to the same restore every other refusal uses.
        record.reason = "; ".join(degradation)
    elif not answer.ran:
        # Nothing of the project was started, so nothing is known about the change.
        record.outcome = OUTCOME_NOT_VERIFIED
        record.reason = answer.reason or "the project could not be started by this check"
        return record
    else:
        record.reason = answer.reason or ("HTTP %d" % record.http_status)

    restored, removed, failed = restore_snapshot(root, run_id, written_paths)
    record.restored = restored
    record.removed = removed
    record.failed = failed
    if failed:
        record.outcome = OUTCOME_ROLLBACK_FAILED
        prefix = PAGE_ROLLBACK_PREFIX if record.degradation else ROLLBACK_PREFIX
        record.message = (
            "Изменения НЕ откатаны полностью: не удалось восстановить "
            + ", ".join(failed[:5])
            + f". {prefix.replace('откатаны', 'не откатаны')} не подтверждён."
        )
        return record
    record.outcome = OUTCOME_ROLLED_BACK
    if record.degradation:
        record.message = _page_rollback_message(record.degradation)
    else:
        record.message = _rollback_message(record.reason, record.log_tail)
    return record


def _default_probe_for(project_id: str, budget: float) -> LaunchProbe:
    """The real probe, bound to this run's budget and project id."""

    def probe(root: Path, entry: EntryPoint) -> ProbeAnswer:
        return default_launch_probe(
            root, entry, budget=budget, project_id=project_id
        )

    return probe
