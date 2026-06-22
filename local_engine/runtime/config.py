"""Configuration and global runtime-home management."""

import os
from pathlib import Path
from typing import Any, Dict

import yaml


DEFAULT_CONFIG: Dict[str, Any] = {
    "workers": 4,
    "claude_command": ["claude"],
    "timeout_seconds": 300,
}
DEFAULT_PREFERENCES: Dict[str, Any] = {"default_mode": "plan", "language": "en"}


def engine_home() -> Path:
    """Return the only approved global write location.

    LOCAL_ENGINE_HOME makes CLI and tests easy to isolate without changing user data.
    """
    configured = os.environ.get("LOCAL_ENGINE_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".local_engine"


def _write_yaml_if_missing(path: Path, payload: Dict[str, Any]) -> None:
    if not path.exists():
        path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def ensure_engine_home() -> Path:
    """Create global config, preferences, and memory documents idempotently."""
    home = engine_home()
    (home / "memory").mkdir(parents=True, exist_ok=True)
    (home / "runs").mkdir(parents=True, exist_ok=True)
    (home / "cache").mkdir(parents=True, exist_ok=True)
    _write_yaml_if_missing(home / "config.yaml", DEFAULT_CONFIG)
    _write_yaml_if_missing(home / "preferences.yaml", DEFAULT_PREFERENCES)
    for filename, title in (
        ("global_memory.md", "# Global Memory\n"),
        ("decision_memory.md", "# Decision Memory\n"),
        ("experience_memory.md", "# Experience Memory\n"),
    ):
        path = home / "memory" / filename
        if not path.exists():
            path.write_text(title, encoding="utf-8")
    return home


def load_yaml(path: Path, defaults: Dict[str, Any] = None) -> Dict[str, Any]:
    """Load a mapping safely, using a copy of defaults for absent/bad values."""
    fallback = dict(defaults or {})
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return fallback
    if not isinstance(value, dict):
        return fallback
    merged = dict(fallback)
    merged.update(value)
    return merged


def load_engine_config() -> Dict[str, Any]:
    home = ensure_engine_home()
    return load_yaml(home / "config.yaml", DEFAULT_CONFIG)


def load_preferences() -> Dict[str, Any]:
    home = ensure_engine_home()
    return load_yaml(home / "preferences.yaml", DEFAULT_PREFERENCES)
