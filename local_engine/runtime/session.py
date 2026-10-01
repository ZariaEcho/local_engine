"""Mutable working memory for one Runtime run."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from local_engine.context.context_quality import ContextQualityReport
from local_engine.context.repo_scanner import RepoInfo
from local_engine.intents.classification import ClassificationResult
from local_engine.kernel.schemas import TaskResult
from local_engine.runtime.contracts import RuntimeInput
from local_engine.runtime.execution_context import RunExecutionContext
from local_engine.runtime.events import RuntimeEventRecorder
from local_engine.runtime.pipeline import RunRegistries
from local_engine.runtime.run_context import RunContext
from local_engine.runtime.task_cache import TaskCache


@dataclass
class RunSession:
    inp: RuntimeInput
    started_at: float = 0.0
    root: Optional[Path] = None
    registries: Optional[RunRegistries] = None
    loaded: Dict[str, Any] = field(default_factory=dict)
    normalized: Dict[str, Any] = field(default_factory=dict)
    classification: Optional[ClassificationResult] = None
    context: Optional[RunContext] = None
    hook_recorder: Optional[RuntimeEventRecorder] = None
    config: Dict[str, Any] = field(default_factory=dict)
    repo_info: Optional[RepoInfo] = None
    repository_fingerprint: Any = None
    project_context: str = ""
    context_quality: Optional[ContextQualityReport] = None
    project_type: Any = None
    execution_context: Optional[RunExecutionContext] = None
    project_memory: str = ""
    engine_memory: str = ""
    intent: str = ""
    graph: Dict[str, Any] = field(default_factory=dict)
    resolved_agents: Dict[str, Any] = field(default_factory=dict)
    resolved_skills: Dict[str, Any] = field(default_factory=dict)
    graph_quality: Any = None
    input_hash: str = ""
    task_cache: Optional[TaskCache] = None
    definition_hashes: Dict[str, str] = field(default_factory=dict)
    run_metadata: Dict[str, Any] = field(default_factory=dict)
    worker_factory: Optional[Callable[..., Any]] = None
    context_quality_json: Optional[Path] = None
    context_quality_markdown: Optional[Path] = None
    project_type_json: Optional[Path] = None
    results: Dict[str, TaskResult] = field(default_factory=dict)
    quality_failures: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    patch_paths: List[Path] = field(default_factory=list)
    artifact_paths: List[Path] = field(default_factory=list)
    deliverable_paths: List[Path] = field(default_factory=list)
    error_log: Optional[Path] = None
    apply_result: Any = None
    task_graph_status: str = ""
    delivery_status: Dict[str, Any] = field(default_factory=dict)
    final_report: Optional[Path] = None
    run_status: str = ""
