"""Explicit filesystem boundary for workspace-scoped tools."""

import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath


class WorkspacePathError(ValueError):
    """A requested path is invalid or escapes its explicit workspace."""


@dataclass(frozen=True)
class Workspace:
    """An existing absolute directory explicitly supplied for one Run."""

    root: Path | str

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
        object.__setattr__(self, "root", root)

    @staticmethod
    def normalize_relative_path(value: object) -> tuple[str, ...]:
        if (
            not isinstance(value, str)
            or not value
            or value.endswith(("/", "\\"))
            or "\x00" in value
        ):
            raise WorkspacePathError("path must be a non-empty relative path")
        # Treat either slash as a separator on every platform so Windows-style
        # traversal cannot hide inside a POSIX path (or vice versa).
        normalized = value.replace("\\", "/")
        windows_path = PureWindowsPath(value)
        posix_path = PurePosixPath(normalized)
        if (
            windows_path.drive
            or windows_path.root
            or posix_path.is_absolute()
            or re.match(r"^[A-Za-z]:", normalized)
        ):
            raise WorkspacePathError("absolute paths are not allowed")
        parts = posix_path.parts
        if not parts or any(part in ("..", "") for part in parts):
            raise WorkspacePathError("path traversal is not allowed")
        if any(part.endswith((".", " ")) for part in parts):
            raise WorkspacePathError("Windows-normalized path components are not allowed")
        if any(":" in part for part in parts):
            raise WorkspacePathError("drive and stream paths are not allowed")
        windows_devices = {"CON", "PRN", "AUX", "NUL"}
        windows_devices.update(f"COM{number}" for number in range(1, 10))
        windows_devices.update(f"LPT{number}" for number in range(1, 10))
        if any(part.split(".", 1)[0].upper() in windows_devices for part in parts):
            raise WorkspacePathError("reserved device names are not allowed")
        if parts[-1] in (".", ".."):
            raise WorkspacePathError("path must name a file")
        return parts

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
        """Fail closed if the supplied workspace root disappeared or changed type."""
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
