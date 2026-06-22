"""Load bounded text memory for prompt compilation."""

from pathlib import Path

from local_engine.runtime.config import ensure_engine_home


def read_text(path: Path, limit: int = 12000) -> str:
    try:
        return path.read_text(encoding="utf-8")[-limit:]
    except OSError:
        return ""


def load_project_memory(project_state: Path) -> str:
    return read_text(project_state / "memory.md")


def load_engine_memory() -> str:
    return read_text(ensure_engine_home() / "memory" / "experience_memory.md")
