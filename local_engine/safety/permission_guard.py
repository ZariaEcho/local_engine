"""Boundaries shared by initialization, run reports, and patch application."""

from pathlib import Path, PurePosixPath, PureWindowsPath


def validate_project_root(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("project root does not exist: {0}".format(root))
    return root


def is_safe_project_relative(path_value: str) -> bool:
    """Return True only for a plain relative path that stays inside the project and avoids `.git`."""
    if not isinstance(path_value, str) or not path_value.strip() or "\x00" in path_value:
        return False
    normalized = path_value.strip().replace("\\", "/")
    if normalized.startswith(("/", "~")):
        return False
    windows = PureWindowsPath(normalized)
    if windows.drive or windows.is_absolute():
        return False
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    if not parts:
        return False
    return all(part != ".." and part.lower() != ".git" for part in parts)
