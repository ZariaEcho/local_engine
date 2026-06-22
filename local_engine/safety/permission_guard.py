"""Boundaries shared by initialization, run reports, and patch application."""

from pathlib import Path


def validate_project_root(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("project root does not exist: {0}".format(root))
    return root


def is_safe_project_relative(path_value: str) -> bool:
    normalized = path_value.replace("\\", "/").lstrip("./")
    parts = [part for part in normalized.split("/") if part]
    return bool(parts) and ".." not in parts and ".git" not in parts and not Path(path_value).is_absolute()
