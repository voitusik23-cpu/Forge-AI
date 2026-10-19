"""Durable storage for discovery sessions: one small JSON file per session.

Deliberately separate from the Run Store. A discovery session is *not* a run: it
precedes one, it is tiny, and mixing the two would put a pre-execution artifact
inside the store whose records are execution evidence. The shape therefore mirrors
the Run Store's proven mechanics -- a conservative id allow-list, a re-proved
containment check, a temporary file written and fsynced before ``os.replace`` --
without sharing its schema or its directory.

Location: ``FORGE_DISCOVERY_ROOT`` when set (tests redirect it), otherwise
``<repository>/.forge/discovery``, which is gitignored runtime state.

Nothing secret is stored here. Answers are the user's own text, and an answer
that looks like a credential has already been replaced by a marker before it
reaches this module.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Optional

from app.discovery.models import DiscoverySession

__all__ = [
    "DISCOVERY_ROOT_ENV_VAR",
    "SCHEMA_VERSION",
    "default_discovery_root",
    "is_safe_session_id",
    "DiscoveryStore",
]


#: Where a deployment can redirect the discovery store (tests use a temp dir).
DISCOVERY_ROOT_ENV_VAR = "FORGE_DISCOVERY_ROOT"

#: Written into every file so a future reader can tell what it is reading.
SCHEMA_VERSION = "forge.discovery.session.v1"

#: Session ids become file names, so they are restricted to a conservative
#: charset. ``.`` and ``..`` satisfy a character class while naming the directory
#: itself and its parent, so they are refused explicitly, and ``path_for``
#: re-proves containment instead of trusting the pattern alone.
_SAFE_SESSION_ID = re.compile(r"^(?!\.\.?$)[A-Za-z0-9._-]{1,128}$")


def is_safe_session_id(session_id: object) -> bool:
    """True when ``session_id`` may be used as a file name inside the store root."""

    return isinstance(session_id, str) and _SAFE_SESSION_ID.match(session_id) is not None


def default_discovery_root() -> Path:
    """The configured discovery store root.

    Resolution order:

    1. ``FORGE_DISCOVERY_ROOT`` when set, which is how tests redirect storage;
    2. ``<repository>/.forge/discovery``, derived from this module's own location
       so the answer does not depend on the process working directory.
    """

    configured = os.environ.get(DISCOVERY_ROOT_ENV_VAR)
    if configured and configured.strip():
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / ".forge" / "discovery"


class DiscoveryStore:
    """Read and write one JSON file per discovery session."""

    def __init__(self, root: Optional[object] = None) -> None:
        self.root = Path(root) if root is not None else default_discovery_root()

    # ------------------------------------------------------------------ paths
    def path_for(self, session_id: object) -> Path:
        """The file path for one session id, or ``ValueError`` when it is unsafe."""

        if not is_safe_session_id(session_id):
            raise ValueError("unsafe discovery session id")
        target = (self.root / f"{session_id}.json").resolve(strict=False)
        root = self.root.resolve(strict=False)
        if target != root and root not in target.parents:
            raise ValueError("discovery session path escapes the store root")
        return target

    def exists(self, session_id: object) -> bool:
        if not is_safe_session_id(session_id):
            return False
        return self.path_for(session_id).is_file()

    # ------------------------------------------------------------------- I/O
    def write(self, session: DiscoverySession) -> Path:
        """Atomically persist one session, replacing any previous version."""

        target = self.path_for(session.session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {"schema": SCHEMA_VERSION, **session.to_dict()},
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".session-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except BaseException:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        return target

    def read(self, session_id: object) -> Optional[DiscoverySession]:
        """The stored session, or ``None`` when absent or unreadable.

        A corrupt file is reported as absent rather than partially trusted: a
        half-read session would be a worse answer than "no such session".
        """

        if not is_safe_session_id(session_id):
            return None
        try:
            path = self.path_for(session_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            return DiscoverySession.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None
