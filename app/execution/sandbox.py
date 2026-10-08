"""The filesystem boundary of one production execution.

This module is the single server-side answer to "is this path inside the run's
workspace?". It is deliberately explicit about what it is and is not:

* It **is** a real, server-side containment check. It resolves symlinks and
  junctions, rejects traversal and absolute paths, and runs both at the authority
  boundary (``RunScope``) and again immediately before the child process is
  spawned, so a path cannot be swapped between the two.
* It is **not** an OS-level sandbox. CPython's standard library exposes no
  portable mechanism to confine a child process's filesystem access on Windows,
  so a spawned process still holds the parent's token and can open any path that
  token may open. That residual risk is recorded in ``RESIDUAL_RISK`` and in the
  architecture documentation rather than papered over.

Nothing here executes anything, and nothing here is reachable from a task, a
decision, a plan, a tool intent, or a request: the workspace root always comes
from the frozen scope.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.execution.paths import PathSecurityError, normalize_workspace_relative_path


class WorkspaceContainmentError(PathSecurityError):
    """Raised when a path is not contained by the run's workspace."""


#: Honest statement of what this boundary cannot do. It is a module constant so
#: it cannot be reworded per run, and it is surfaced in documentation and tests.
RESIDUAL_RISK = (
    "No OS-level filesystem sandbox is applied. A spawned process inherits the "
    "parent's security token and may open any path that token may open, including "
    "paths outside the workspace. This module constrains the paths the execution "
    "plane itself resolves, not the syscalls of the child process."
)


def canonical_root(root: Path) -> Path:
    """Canonicalize a workspace root once, following symlinks and junctions.

    ``os.path.realpath`` is used in addition to ``Path.resolve`` because on
    Windows it also resolves junctions and substituted drives, which
    ``Path.resolve`` does not always follow.
    """
    if not isinstance(root, Path):
        root = Path(root)
    try:
        resolved = Path(os.path.realpath(str(root)))
        return resolved.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        raise WorkspaceContainmentError(
            f"workspace root cannot be canonicalized: {type(exc).__name__}"
        ) from exc


def _is_within(candidate: Path, root: Path) -> bool:
    """True when ``candidate`` is ``root`` or lives beneath it.

    Both arguments must already be canonical. Comparing canonical paths is what
    makes a symlink or junction that points outside the workspace fail here even
    though its *lexical* name sits inside.
    """
    if candidate == root:
        return True
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


@dataclass(frozen=True)
class WorkspaceBoundary:
    """Canonical, immutable filesystem perimeter for one run.

    The root is captured once from the trusted scope. Every containment question
    is answered against this canonical root, so ``..``, an absolute path outside
    the root, and a symlink that resolves outside are all refused.
    """

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", canonical_root(self.root))

    @classmethod
    def for_workspace(cls, workspace: object) -> "WorkspaceBoundary":
        """Build the boundary from an explicit workspace contract.

        An explicit workspace is required: a boundary is never inferred from a
        request, a task, a decision, or the current process directory.
        """
        root = getattr(workspace, "root", None)
        if not isinstance(root, Path):
            raise WorkspaceContainmentError(
                "workspace boundary requires a workspace with a Path root"
            )
        return cls(root=root)

    def contains(self, candidate: object) -> bool:
        """Report whether a resolved path is inside the boundary."""
        if not isinstance(candidate, Path):
            try:
                candidate = Path(str(candidate))
            except (TypeError, ValueError):
                return False
        return _is_within(canonical_root(candidate), self.root)

    def resolve_relative(self, relative: object) -> Path:
        """Resolve a workspace-relative path and prove it stays inside.

        The lexical form is normalized first, which rejects ``..`` segments and
        absolute paths outright, and the result is then canonicalized so a symlink
        pointing outside the workspace is refused as well.
        """
        if not isinstance(relative, str) or not relative.strip():
            raise WorkspaceContainmentError("path must be a non-empty string")
        try:
            normalized = normalize_workspace_relative_path(relative)
        except PathSecurityError as exc:
            raise WorkspaceContainmentError(str(exc)) from exc
        if not isinstance(normalized, str):
            raise WorkspaceContainmentError("normalized path must be a string")
        candidate = canonical_root(self.root / normalized)
        if not _is_within(candidate, self.root):
            raise WorkspaceContainmentError(
                "path resolves outside the workspace boundary"
            )
        return candidate

    def describe(self) -> dict[str, object]:
        """Bounded, non-sensitive description for events and diagnostics."""
        return {
            "workspace_root": str(self.root),
            "os_level_sandbox": False,
            "residual_risk": RESIDUAL_RISK,
        }


def assert_no_escape_targets(targets: object, boundary: WorkspaceBoundary) -> None:
    """Prove that declared artifact targets stay inside the boundary."""
    if not targets:
        return
    if not isinstance(boundary, WorkspaceBoundary):
        raise WorkspaceContainmentError("boundary must be a WorkspaceBoundary")
    for target in targets:
        boundary.resolve_relative(target)
