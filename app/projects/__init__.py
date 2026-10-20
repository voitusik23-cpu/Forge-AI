"""Persistent project records, versions, archive and the lifecycle contract.

Two layers live in this package:

* :mod:`app.projects.state` -- the per-attempt project state of an Engineering Run
  attempt (``ProjectState``/``ProjectStateStatus``), which predates this package's
  lifecycle layer and is unchanged;
* the persistent project lifecycle: :mod:`app.projects.models` (records),
  :mod:`app.projects.store` (storage), :mod:`app.projects.lifecycle` (the
  transition table and the completion gate) and :mod:`app.projects.service` (the
  operations a caller uses).
"""

from app.projects.lifecycle import (
    ALLOWED_TRANSITIONS,
    CompletionNotAllowed,
    IllegalTransition,
    LifecycleEvent,
    ProjectLifecycleError,
    advance,
    completion_blockers,
)
from app.projects.models import (
    ArtifactRef,
    ChangeRequest,
    Project,
    ProjectError,
    ProjectStatus,
    ProjectVersion,
    utc_now,
)
from app.projects.service import (
    InvalidChangeRequest,
    NewVersionTask,
    ProjectService,
    RunFacts,
    UnknownProjectVersion,
    run_facts,
)
from app.projects.state import (
    ProjectState,
    ProjectStateStatus,
    derive_project_state,
)
from app.projects.store import (
    ProjectStore,
    UnknownProject,
    default_projects_root,
    is_safe_project_id,
    new_project_id,
)

__all__ = [
    # Engineering Run attempt state (pre-existing, unchanged)
    "ProjectState",
    "ProjectStateStatus",
    "derive_project_state",
    # Lifecycle records
    "ArtifactRef",
    "ChangeRequest",
    "Project",
    "ProjectError",
    "ProjectStatus",
    "ProjectVersion",
    "utc_now",
    # Lifecycle table and gate
    "ALLOWED_TRANSITIONS",
    "CompletionNotAllowed",
    "IllegalTransition",
    "LifecycleEvent",
    "ProjectLifecycleError",
    "advance",
    "completion_blockers",
    # Storage
    "ProjectStore",
    "UnknownProject",
    "default_projects_root",
    "is_safe_project_id",
    "new_project_id",
    # Service
    "InvalidChangeRequest",
    "NewVersionTask",
    "ProjectService",
    "RunFacts",
    "UnknownProjectVersion",
    "run_facts",
]
