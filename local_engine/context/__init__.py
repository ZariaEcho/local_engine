"""Repository discovery and context construction for Context Engine V1."""

from local_engine.context.context_builder import build_context, list_modules, write_project_context
from local_engine.context.context_quality import ContextQualityReport, assess_context_quality, evaluate_context_quality, write_context_quality_report
from local_engine.context.repo_scanner import RepoInfo, scan_project

__all__ = [
    "ContextQualityReport",
    "RepoInfo",
    "assess_context_quality",
    "build_context",
    "evaluate_context_quality",
    "list_modules",
    "scan_project",
    "write_context_quality_report",
    "write_project_context",
]
