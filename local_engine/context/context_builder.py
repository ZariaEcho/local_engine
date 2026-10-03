"""Human-readable project context built from a repository map."""

from pathlib import Path
from typing import Any, Dict, Iterable, List

from local_engine.context.repo_scanner import RepoInfo

_DEPENDENCY_MANIFESTS = {
    "pyproject.toml",
    "requirements.txt",
    "setup.py",
    "package.json",
    "pnpm-lock.yaml",
    "uv.lock",
    "go.mod",
    "cargo.toml",
    "pom.xml",
}


def build_context(repo_info: Any) -> str:
    """Render a bounded, useful ``PROJECT_CONTEXT.md`` document."""
    data = _as_dict(repo_info)
    languages = _lines(data.get("languages", []))
    directories = _lines(data.get("directories", []))
    modules = _lines(list_modules(repo_info))
    dependencies = _lines(data.get("dependencies", []))
    files = _lines(data.get("files", []))
    manifests = _lines(
        filename for filename in data.get("files", []) if Path(filename).name.lower() in _DEPENDENCY_MANIFESTS
    )
    return """# Project Summary

## Languages
{languages}

## Directories
{directories}

## Modules
{modules}

## Dependencies
{dependencies}

## Dependency Manifests
{manifests}

## Repository Files
{files}
""".format(
        languages=languages,
        directories=directories,
        modules=modules,
        dependencies=dependencies,
        manifests=manifests,
        files=files,
    )


def write_project_context(repo_info: Any, project_state: Path) -> Path:
    """Persist the canonical project context beside repository-local state."""
    path = Path(project_state) / "PROJECT_CONTEXT.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_context(repo_info), encoding="utf-8")
    return path


def repo_summary(repo_info: Any) -> str:
    data = _as_dict(repo_info)
    return "Languages: {0}\nFiles: {1}\nDirectories: {2}\nDependencies: {3}".format(
        ", ".join(data.get("languages", [])) or "none",
        len(data.get("files", [])),
        len(data.get("directories", [])),
        ", ".join(data.get("dependencies", [])) or "none",
    )


def list_modules(repo_info: Any) -> List[str]:
    """Return source-module labels suitable for ``local-engine inspect``."""
    return _modules(_as_dict(repo_info).get("files", []))


def _as_dict(repo_info: Any) -> Dict[str, List[str]]:
    if isinstance(repo_info, RepoInfo):
        return repo_info.to_dict()
    return repo_info if isinstance(repo_info, dict) else {}


def _lines(values: Iterable[str]) -> str:
    values = list(values)
    return "\n".join("- {0}".format(value) for value in values[:100]) or "- None"


def _modules(files: Iterable[str]) -> List[str]:
    modules = []
    for filename in files:
        path = Path(filename)
        if path.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".go", ".rs", ".java"}:
            continue
        if path.stem in {"__init__", "index", "main"}:
            continue
        modules.append(_module_label(path.stem))
    return sorted(set(modules))


def _module_label(stem: str) -> str:
    known_acronyms = {"bfs", "dfs", "api", "cli", "sql", "ui", "ux"}
    if stem.lower() in known_acronyms:
        return stem.upper()
    return stem.replace("_", " ").replace("-", " ").title()
