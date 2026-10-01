"""Packaged built-in registry resources."""

from __future__ import annotations

from importlib import resources
from pathlib import Path
import shutil
import tempfile
from typing import Dict


_MATERIALIZED: Dict[str, Path] = {}


def resource_directory(name: str) -> Path:
    """Return a concrete directory path for a bundled resource tree."""
    if name in _MATERIALIZED:
        return _MATERIALIZED[name]
    traversable = resources.files(__name__).joinpath(name)
    if isinstance(traversable, Path):
        return traversable
    target = Path(tempfile.mkdtemp(prefix="local_engine_resources_")) / name
    _copy_tree(traversable, target)
    _MATERIALIZED[name] = target
    return target


def _copy_tree(source, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            _copy_tree(item, destination)
        else:
            with item.open("rb") as src, destination.open("wb") as dst:
                shutil.copyfileobj(src, dst)
