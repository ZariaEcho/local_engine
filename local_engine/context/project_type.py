"""Deterministic project-type detection for graph selection."""

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from local_engine.context.repo_scanner import RepoInfo


_PROJECT_TYPE_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "algorithm_repository": (
        "algorithm",
        "algorithms",
        "算法",
        "graph",
        "graphs",
        "bfs",
        "dfs",
        "dijkstra",
        "union_find",
        "mst",
        "shortest_path",
    ),
    "web_app": (
        "frontend",
        "backend",
        "api",
        "pages",
        "components",
        "server",
        "database",
    ),
    "cli_tool": (
        "cli.py",
        "typer",
        "click",
        "argparse",
    ),
    "python_library": (
        "pyproject.toml",
        "src",
        "tests",
    ),
}

_WEIGHTS = {
    "algorithm": 2.0,
    "algorithms": 2.0,
    "算法": 2.0,
    "graph": 1.0,
    "graphs": 1.0,
    "bfs": 2.5,
    "dfs": 2.5,
    "dijkstra": 2.5,
    "union_find": 2.5,
    "mst": 2.5,
    "shortest_path": 2.5,
}


@dataclass(frozen=True)
class ProjectTypeReport:
    project_type: str
    confidence: float
    matched_indicators: List[str]
    scores: Dict[str, float]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_type": self.project_type,
            "confidence": round(self.confidence, 2),
            "matched_indicators": list(self.matched_indicators),
            "scores": {name: round(score, 2) for name, score in self.scores.items()},
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"


def detect_project_type(
    project_root: Path,
    repo_info: RepoInfo,
    requirement: Dict[str, Any] | None = None,
) -> ProjectTypeReport:
    """Infer a coarse project type from paths, dependencies, and the request text."""
    haystack = _haystack(project_root, repo_info, requirement or {})
    matches: Dict[str, List[str]] = {}
    scores: Dict[str, float] = {}
    for project_type, keywords in _PROJECT_TYPE_KEYWORDS.items():
        indicators = [keyword for keyword in keywords if _contains_indicator(haystack, keyword)]
        matches[project_type] = indicators
        scores[project_type] = sum(_weight(keyword) for keyword in indicators)

    project_type = max(scores, key=lambda name: (scores[name], _priority(name)))
    if scores[project_type] <= 0:
        project_type = "python_library" if "Python" in repo_info.languages else "unknown"
    confidence = _confidence(project_type, scores)
    return ProjectTypeReport(project_type, confidence, matches.get(project_type, []), scores)


def _haystack(project_root: Path, repo_info: RepoInfo, requirement: Dict[str, Any]) -> str:
    values: List[str] = []
    values.extend(project_root.expanduser().resolve().parts)
    values.extend(repo_info.files)
    values.extend(repo_info.directories)
    values.extend(repo_info.dependencies)
    for key in ("raw_requirement", "raw_summary", "user_goal", "real_goal", "success_definition"):
        value = requirement.get(key)
        if value:
            values.append(str(value))
    return _normalize(" ".join(values))


def _normalize(value: str) -> str:
    text = value.casefold().replace("-", "_")
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", text)
    text = re.sub(r"[^0-9a-zA-Z_\u4e00-\u9fff.]+", " ", text)
    return " {0} ".format(text.casefold())


def _contains_indicator(haystack: str, keyword: str) -> bool:
    normalized = _normalize(keyword).strip()
    if "." in normalized:
        return " {0} ".format(normalized) in haystack
    return re.search(r"(?<![0-9a-zA-Z_]){0}(?![0-9a-zA-Z_])".format(re.escape(normalized)), haystack) is not None


def _weight(keyword: str) -> float:
    return float(_WEIGHTS.get(keyword, 1.0))


def _priority(project_type: str) -> int:
    order = ["algorithm_repository", "web_app", "cli_tool", "python_library", "unknown"]
    return -order.index(project_type) if project_type in order else -len(order)


def _confidence(project_type: str, scores: Dict[str, float]) -> float:
    score = scores.get(project_type, 0.0)
    if project_type == "unknown" or score <= 0:
        return 0.2
    second = max((value for name, value in scores.items() if name != project_type), default=0.0)
    margin = max(0.0, score - second)
    return min(0.95, 0.45 + min(score, 5.0) * 0.08 + min(margin, 4.0) * 0.04)

