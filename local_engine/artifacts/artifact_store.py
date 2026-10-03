"""Durable project-local artifact storage outside individual run reports."""

import re
from pathlib import Path
from typing import Optional

_DIRECTORIES = {"audit": "audit", "plan": "plan", "review": "review", "test": "test", "docs": "docs"}


class ArtifactStore:
    """Store curated artifacts at ``<project>/.local_engine/artifacts``."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.root = self.project_root / ".local_engine" / "artifacts"

    def save_artifact(self, artifact_type: str, content: str, filename: Optional[str] = None) -> Path:
        category = _category(artifact_type)
        directory = self.root / category
        directory.mkdir(parents=True, exist_ok=True)
        name = _safe_filename(filename or "{0}_ARTIFACT.md".format(category.upper()))
        target = (directory / name).resolve()
        if directory.resolve() not in target.parents:
            raise ValueError("artifact filename must remain inside its artifact directory")
        target.write_text(str(content), encoding="utf-8")
        return target


def save_artifact(
    artifact_type: str, content: str, project_root: Optional[Path] = None, filename: Optional[str] = None
) -> Path:
    """Convenience API; callers may provide a project root or use the current directory."""
    return ArtifactStore(project_root or Path.cwd()).save_artifact(artifact_type, content, filename)


def _category(value: str) -> str:
    normalized = str(value or "docs").lower()
    aliases = {"documentation": "docs", "document": "docs", "analysis": "review", "research": "review"}
    normalized = aliases.get(normalized, normalized)
    return _DIRECTORIES.get(normalized, "docs")


def _safe_filename(value: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "ARTIFACT"
    return name if name.lower().endswith(".md") else name + ".md"
