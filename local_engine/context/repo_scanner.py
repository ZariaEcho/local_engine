"""Bounded, dependency-free repository structure scanning."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Set

_LANGUAGES = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
}
_SUPPORTED_SUFFIXES = set(_LANGUAGES) | {".md", ".yaml", ".yml", ".json", ".toml"}
_CONTEXT_FILENAMES = {
    "requirements.txt",
    "setup.py",
    "package.json",
    "pnpm-lock.yaml",
    "uv.lock",
    "app.json",
    "project.config.json",
    "go.mod",
    "cargo.lock",
    "cargo.toml",
    "pom.xml",
}
_IGNORED_PARTS = {".git", ".local_engine", ".venv", "venv", "node_modules", "__pycache__", "target", "build", "dist"}


@dataclass(frozen=True)
class RepoInfo:
    """A serializable repository map with only stable, project-relative data."""

    languages: List[str]
    files: List[str]
    directories: List[str]
    dependencies: List[str]

    def to_dict(self) -> Dict[str, List[str]]:
        return {
            "languages": self.languages,
            "files": self.files,
            "directories": self.directories,
            "dependencies": self.dependencies,
        }


def scan_project(project_path: Path) -> RepoInfo:
    """Scan supported files and dependency manifests, then persist ``repo_map.json``."""
    root = Path(project_path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("project path does not exist: {0}".format(root))

    languages: Set[str] = set()
    files: List[str] = []
    directories: Set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if _ignored(relative.parts):
            continue
        if path.is_dir():
            if relative.parts:
                directories.add(relative.as_posix())
            continue
        suffix = path.suffix.lower()
        if suffix in _SUPPORTED_SUFFIXES or relative.name.lower() in _CONTEXT_FILENAMES:
            files.append(relative.as_posix())
            if suffix in _LANGUAGES:
                languages.add(_LANGUAGES[suffix])
            if relative.parent != Path("."):
                directories.add(relative.parent.as_posix())

    dependencies = _scan_dependencies(root)
    info = RepoInfo(sorted(languages), sorted(files), sorted(directories), sorted(dependencies))
    cache_path = root / ".local_engine" / "cache" / "repo_map.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(info.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return info


def _ignored(parts: Iterable[str]) -> bool:
    return any(part in _IGNORED_PARTS for part in parts)


def _scan_dependencies(root: Path) -> Set[str]:
    dependencies: Set[str] = set()
    _read_requirements(root / "requirements.txt", dependencies)
    _read_pyproject(root / "pyproject.toml", dependencies)
    _read_package_json(root / "package.json", dependencies)
    _read_go_mod(root / "go.mod", dependencies)
    _read_cargo(root / "Cargo.toml", dependencies)
    _read_pom(root / "pom.xml", dependencies)
    return dependencies


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _dependency_name(value: str) -> str:
    return re.split(r"[<>=!~;\[\s]", value.strip(), maxsplit=1)[0]


def _read_requirements(path: Path, dependencies: Set[str]) -> None:
    for line in _read_text(path).splitlines():
        value = line.split("#", 1)[0].strip()
        if value and not value.startswith(("-", "http://", "https://")):
            dependencies.add(_dependency_name(value))


def _read_pyproject(path: Path, dependencies: Set[str]) -> None:
    text = _read_text(path)
    for block in re.findall(r"(?ms)^\s*dependencies\s*=\s*\[(.*?)\]", text):
        for value in re.findall(r"[\"']([^\"']+)[\"']", block):
            dependencies.add(_dependency_name(value))


def _read_package_json(path: Path, dependencies: Set[str]) -> None:
    try:
        payload = json.loads(_read_text(path) or "{}")
    except json.JSONDecodeError:
        return
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = payload.get(key, {})
        if isinstance(values, dict):
            dependencies.update(str(name) for name in values)


def _read_go_mod(path: Path, dependencies: Set[str]) -> None:
    text = _read_text(path)
    for line in text.splitlines():
        value = line.strip()
        if value and not value.startswith(("module ", "go ", "require", "replace", "(")):
            candidate = value.split()[0]
            if "/" in candidate or "." in candidate:
                dependencies.add(candidate)


def _read_cargo(path: Path, dependencies: Set[str]) -> None:
    in_dependencies = False
    for line in _read_text(path).splitlines():
        value = line.strip()
        if value.startswith("["):
            in_dependencies = value in {"[dependencies]", "[dev-dependencies]"}
        elif in_dependencies and "=" in value and not value.startswith("#"):
            dependencies.add(value.split("=", 1)[0].strip())


def _read_pom(path: Path, dependencies: Set[str]) -> None:
    for value in re.findall(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", _read_text(path)):
        dependencies.add(value)
