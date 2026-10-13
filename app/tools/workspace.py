"""Explicit filesystem boundary for workspace-scoped tools."""

import stat
from dataclasses import dataclass, field
from pathlib import Path

from app.execution.identity import WorkspaceIdentity
from app.execution.paths import PathSecurityError, normalize_workspace_relative_path


class WorkspacePathError(ValueError):
    """A requested path is invalid or escapes its explicit workspace."""


@dataclass(frozen=True)
class Workspace:
    """An existing absolute directory explicitly supplied for one Run.

    The boundary is bound to the directory itself, not just to its path.  A path
    string is not an identity: renaming the directory away and creating a different
    one at the same path leaves the path identical, and a boundary that compared
    only the path served the replacement's files.  The identity is captured here,
    when the boundary is opened, and re-checked on every operation.
    """

    root: Path | str
    _identity: "WorkspaceIdentity | None" = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        try:
            path = Path(self.root)
            if not path.is_absolute():
                raise WorkspacePathError("workspace root must be absolute")
            root = path.resolve(strict=True)
        except (OSError, RuntimeError, TypeError) as exc:
            raise WorkspacePathError("workspace root is unavailable") from exc
        if not root.is_dir():
            raise WorkspacePathError("workspace root must be a directory")
        try:
            identity = WorkspaceIdentity.capture(root)
        except (OSError, RuntimeError, ValueError) as exc:
            # No identity means no way to tell this directory from a replacement,
            # so the boundary refuses rather than continuing unprotected.
            raise WorkspacePathError("workspace root is unavailable") from exc
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "_identity", identity)

    @staticmethod
    def normalize_relative_path(value: object) -> tuple[str, ...]:
        try:
            normalized = normalize_workspace_relative_path(value)
        except PathSecurityError as exc:
            raise WorkspacePathError(str(exc)) from exc
        if normalized == ".":
            raise WorkspacePathError("path must name a file")
        return tuple(normalized.split("/"))

    def resolve_target(self, relative_path: object) -> tuple[Path, str, tuple[str, ...]]:
        self.assert_available()
        parts = self.normalize_relative_path(relative_path)
        target = self.root.joinpath(*parts)
        self._check_contained(target)
        self._reject_link_components(parts)
        return target, "/".join(parts), parts

    def create_parent_directories(self, parts: tuple[str, ...]) -> Path:
        self.assert_available()
        if (
            not isinstance(parts, tuple)
            or not parts
            or any(not isinstance(part, str) or not part for part in parts)
        ):
            raise WorkspacePathError("target path is invalid")
        normalized = self.normalize_relative_path("/".join(parts))
        if normalized != parts:
            raise WorkspacePathError("target path is not normalized")
        current = self.root
        created: list[Path] = []
        try:
            for part in parts[:-1]:
                current = current / part
                self._reject_link(current)
                self._check_contained(current)
                if current.exists():
                    if not current.is_dir():
                        raise WorkspacePathError("parent path is not a directory")
                else:
                    try:
                        current.mkdir()
                        created.append(current)
                    except FileExistsError:
                        if not current.is_dir():
                            raise WorkspacePathError("parent path is not a directory")
                self._reject_link(current)
                self._check_contained(current)
            return current
        except Exception:
            for directory in reversed(created):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            raise

    def verify_target(self, parts: tuple[str, ...]) -> Path:
        self.assert_available()
        target = self.root.joinpath(*parts)
        self._check_contained(target)
        self._reject_link_components(parts)
        return target

    def assert_available(self) -> None:
        """Fail closed if the workspace root disappeared, changed type, or was replaced."""

        try:
            info = self.root.lstat()
            attributes = getattr(info, "st_file_attributes", 0)
            is_reparse_point = bool(
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if (
                not stat.S_ISDIR(info.st_mode)
                or self.root.is_symlink()
                or getattr(self.root, "is_junction", lambda: False)()
                or is_reparse_point
            ):
                raise WorkspacePathError("workspace root is unavailable")
            if self._identity is not None and not self._identity.matches(self.root):
                raise WorkspacePathError(
                    "workspace root was replaced: the directory at that path is not "
                    "the one this boundary was opened on"
                )
        except (OSError, RuntimeError) as exc:
            raise WorkspacePathError("workspace root is unavailable") from exc

    def _check_contained(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspacePathError("target is outside the workspace") from exc

    def _reject_link_components(self, parts: tuple[str, ...]) -> None:
        current = self.root
        for part in parts:
            current = current / part
            self._reject_link(current)

    @staticmethod
    def _reject_link(path: Path) -> None:
        try:
            is_junction = getattr(path, "is_junction", lambda: False)()
            info = path.lstat()
            attributes = getattr(info, "st_file_attributes", 0)
            is_reparse_point = bool(
                attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            )
            if path.is_symlink() or is_junction or is_reparse_point:
                raise WorkspacePathError("filesystem links are not allowed in target path")
        except FileNotFoundError:
            return
        except OSError as exc:
            raise WorkspacePathError("target path could not be checked") from exc
