"""Non-blocking evidence for whether project context is complete enough to trust."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from local_engine.context.repo_scanner import RepoInfo

_IGNORED_PARTS = {".git", ".local_engine", ".venv", "venv", "node_modules", "__pycache__", "target", "build", "dist"}
_DEPENDENCY_FILES = ("pyproject.toml", "requirements.txt", "setup.py", "package.json", "pnpm-lock.yaml", "uv.lock")
_ENTRY_POINTS = ("cli.py", "main.py", "app.py", "index.ts", "app.ts", "app.js")
_GENERIC_CORE_PATHS = ("README.md", "docs/", "config/", "scripts/", "tests/")
_NODE_CORE_PATHS = ("src/", "pages/", "components/", "package.json", "app.json", "project.config.json")
_PYTHON_CORE_PATHS = ("src/", "app/", "pyproject.toml", "requirements.txt", "setup.py")


@dataclass(frozen=True)
class ContextQualityReport:
    """A transparent, conservative context-completeness assessment."""

    coverage: float
    complete: bool
    warnings: List[str]
    missing_core_paths: List[str]
    detected_core_paths: List[str]
    language_confidence: float
    dependency_confidence: float
    file_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "coverage": self.coverage,
            "complete": self.complete,
            "warnings": list(self.warnings),
            "missing_core_paths": list(self.missing_core_paths),
            "detected_core_paths": list(self.detected_core_paths),
            "language_confidence": self.language_confidence,
            "dependency_confidence": self.dependency_confidence,
            "file_count": self.file_count,
        }

    def to_markdown(self) -> str:
        return """# Context Quality Report

## Summary

- Coverage: {coverage:.2f}
- Complete: {complete}
- Language confidence: {language_confidence:.2f}
- Dependency confidence: {dependency_confidence:.2f}
- Project files scanned: {file_count}

## Detected Core Paths
{detected}

## Missing Core Paths
{missing}

## Warnings
{warnings}
""".format(
            coverage=self.coverage,
            complete="yes" if self.complete else "no",
            language_confidence=self.language_confidence,
            dependency_confidence=self.dependency_confidence,
            file_count=self.file_count,
            detected=_markdown_list(self.detected_core_paths),
            missing=_markdown_list(self.missing_core_paths),
            warnings=_markdown_list(self.warnings),
        )


def assess_context_quality(project_root: Path, repo_info: Any, project_context: str) -> ContextQualityReport:
    """Check the generated context against project structure without blocking a run.

    Coverage measures how many detected core paths are named in ``PROJECT_CONTEXT``.
    A missing test signal is deliberately counted as a core-path miss for Python and
    JavaScript/TypeScript projects: it is material uncertainty for an audit, but it
    remains a warning rather than a run blocker.
    """
    root = Path(project_root).expanduser().resolve()
    data = _as_dict(repo_info)
    context = str(project_context or "")
    project_files = _project_files(root)
    file_names = {path.name.lower() for path in project_files}
    relative_files = {path.relative_to(root).as_posix() for path in project_files}
    languages = {str(value).lower() for value in data.get("languages", [])}
    python_project = "python" in languages or bool(file_names.intersection({"pyproject.toml", "requirements.txt", "setup.py"}))
    node_project = bool(languages.intersection({"typescript", "javascript"})) or "package.json" in file_names

    candidates: List[str] = []
    if python_project:
        candidates.extend(_PYTHON_CORE_PATHS)
    if node_project:
        candidates.extend(_NODE_CORE_PATHS)
    candidates.extend(_GENERIC_CORE_PATHS)
    candidates.extend(_ENTRY_POINTS)
    candidates.extend(_DEPENDENCY_FILES)

    detected = [candidate for candidate in _unique(candidates) if _path_exists(candidate, root, relative_files)]
    missing: List[str] = []
    has_test_signal, test_label = _test_signal(root, relative_files)
    if (python_project or node_project) and not has_test_signal:
        missing.append("tests/")

    warnings: List[str] = []
    summarized = [candidate for candidate in detected if _is_summarized(candidate, context)]
    for candidate in detected:
        if candidate not in summarized:
            warnings.append("{0} exists but was not summarized in PROJECT_CONTEXT.md.".format(candidate))
    if has_test_signal and test_label not in detected and not _is_summarized(test_label, context):
        warnings.append("{0} exists but was not summarized in PROJECT_CONTEXT.md.".format(test_label))
    if missing:
        warnings.append("No test directory or test files were detected; conclusions about test coverage are limited.")

    coverage_denominator = len(detected) + len(missing)
    coverage = 1.0 if coverage_denominator == 0 else len(summarized) / coverage_denominator
    language_confidence = _language_confidence(python_project, node_project, languages, detected)
    manifests = [filename for filename in _DEPENDENCY_FILES if filename in file_names]
    dependency_confidence = 1.0 if not manifests else sum(
        1 for filename in manifests if _is_summarized(filename, context)
    ) / len(manifests)
    if manifests and dependency_confidence < 1.0:
        warnings.append("Dependency manifests exist but are not fully summarized; dependency conclusions may be incomplete.")
    if len(project_files) > 1000:
        warnings.append("Large project detected. Context may be incomplete.")
    if coverage < 0.7:
        warnings.append("Context coverage is below the 0.70 completeness threshold.")
    if language_confidence < 0.6:
        warnings.append("Language detection confidence is below the 0.60 completeness threshold.")

    return ContextQualityReport(
        coverage=round(coverage, 4),
        complete=coverage >= 0.7 and language_confidence >= 0.6,
        warnings=_unique(warnings),
        missing_core_paths=_unique(missing),
        detected_core_paths=_unique(detected),
        language_confidence=round(language_confidence, 4),
        dependency_confidence=round(dependency_confidence, 4),
        file_count=len(project_files),
    )


def evaluate_context_quality(project_root: Path, repo_info: Any, project_context: str) -> ContextQualityReport:
    """Compatibility-friendly alias for callers that use evaluator terminology."""
    return assess_context_quality(project_root, repo_info, project_context)


def write_context_quality_report(report: ContextQualityReport, artifacts_dir: Path) -> Tuple[Path, Path]:
    """Write the stable JSON and human-readable artifacts for a run."""
    target = Path(artifacts_dir)
    target.mkdir(parents=True, exist_ok=True)
    json_path = target / "context_quality.json"
    markdown_path = target / "context_quality.md"
    json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    return json_path, markdown_path


def _as_dict(repo_info: Any) -> Dict[str, List[str]]:
    if isinstance(repo_info, RepoInfo):
        return repo_info.to_dict()
    return repo_info if isinstance(repo_info, dict) else {}


def _project_files(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    return [path for path in root.rglob("*") if path.is_file() and not _ignored(path.relative_to(root).parts)]


def _ignored(parts: Iterable[str]) -> bool:
    return any(part in _IGNORED_PARTS for part in parts)


def _path_exists(candidate: str, root: Path, relative_files: Sequence[str]) -> bool:
    path = root / candidate.rstrip("/")
    if candidate.endswith("/"):
        return path.is_dir()
    return candidate in relative_files


def _test_signal(root: Path, relative_files: Sequence[str]) -> Tuple[bool, str]:
    if (root / "tests").is_dir():
        return True, "tests/"
    for filename in relative_files:
        name = Path(filename).name
        if name.startswith("test_") and name.endswith(".py"):
            return True, filename
        if name.endswith((".spec.ts", ".test.ts", ".spec.tsx", ".test.tsx", ".spec.js", ".test.js")):
            return True, filename
    return False, "tests/"


def _is_summarized(candidate: str, context: str) -> bool:
    normalized = candidate.rstrip("/")
    return candidate in context or normalized in context


def _language_confidence(python_project: bool, node_project: bool, languages: Sequence[str], detected: Sequence[str]) -> float:
    if python_project:
        score = 0.7
        if any(path in detected for path in ("pyproject.toml", "requirements.txt", "setup.py")):
            score += 0.2
        if any(path in detected for path in ("src/", "app/", "cli.py", "main.py", "app.py")):
            score += 0.1
        return min(score, 1.0)
    if node_project:
        score = 0.7
        if "package.json" in detected:
            score += 0.2
        if any(path in detected for path in ("src/", "pages/", "components/", "index.ts", "app.ts", "app.js")):
            score += 0.1
        return min(score, 1.0)
    return 0.7 if languages else 0.0


def _unique(values: Iterable[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value))


def _markdown_list(values: Iterable[str]) -> str:
    values = list(values)
    return "\n".join("- {0}".format(value) for value in values) or "- None"
