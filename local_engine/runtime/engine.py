"""Compatibility facade around the Task Graph Runtime."""

from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from local_engine.context.context_quality import ContextQualityReport, assess_context_quality
from local_engine.context.repo_scanner import RepoInfo
from local_engine.intake.input_loader import load_input
from local_engine.intake.requirement_normalizer import normalize_requirement
from local_engine.intents.classification import ClassificationResult
from local_engine.intents.registry import IntentRegistry
from local_engine.memory.memory_loader import read_text
from local_engine.runtime.config import ensure_engine_home
from local_engine.runtime.contracts import RuntimeInput
from local_engine.runtime.outcome import RunOutcome
from local_engine.runtime.phases.finalize import apply_existing_run
from local_engine.runtime.phases.plan import classify_requirement, preview_planned_graph, refresh_repo_scan
from local_engine.safety.permission_guard import validate_project_root


class Engine:
    """Compatibility adapter. New code should call Runtime.execute."""

    def initialize(self, project_root: Path) -> Path:
        root = validate_project_root(project_root)
        ensure_engine_home()
        state = root / ".local_engine"
        (state / "task_reports").mkdir(parents=True, exist_ok=True)
        (state / "runs").mkdir(parents=True, exist_ok=True)
        (state / "artifacts").mkdir(parents=True, exist_ok=True)
        project_file = state / "project.yaml"
        if not project_file.exists():
            project_file.write_text(
                yaml.safe_dump({"name": root.name, "created_by": "local_engine", "version": 1}, sort_keys=False),
                encoding="utf-8",
            )
        refresh_repo_scan(root)
        memory_file = state / "memory.md"
        if not memory_file.exists():
            memory_file.write_text("# Project Memory\n", encoding="utf-8")
        return state

    def scan(self, project_root: Path) -> RepoInfo:
        """Refresh the repository map and canonical project context without a worker run."""
        return refresh_repo_scan(project_root)

    def context_quality(self, project_root: Path, repo_info: Optional[RepoInfo] = None) -> ContextQualityReport:
        """Refresh or assess repository context for CLI inspection without a worker run."""
        root = validate_project_root(project_root)
        info = repo_info if repo_info is not None else self.scan(root)
        context = read_text(root / ".local_engine" / "PROJECT_CONTEXT.md")
        return assess_context_quality(root, info, context)

    def classify_request(
        self,
        task_text: str,
        input_file: Optional[Path] = None,
        intent_override: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> ClassificationResult:
        """Classify an input before allocating a run or constructing a graph."""
        loaded = load_input(task_text, input_file)
        normalized = normalize_requirement(loaded)
        return classify_requirement(
            IntentRegistry.load(), normalized["raw_requirement"], intent_override=intent_override, skill=skill
        )

    def preview_graph(
        self, project_root: Path, task_text: str = "", intent_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return a validated intent graph without invoking any Claude worker."""
        return preview_planned_graph(project_root, task_text, intent_override=intent_override)

    def run(
        self,
        project_root: Path,
        task_text: str,
        input_file: Optional[Path] = None,
        mode: str = "plan",
        workers: Optional[int] = None,
        worker_factory: Optional[Callable[[], Any]] = None,
        apply_approved: bool = False,
        event_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        skill: Optional[str] = None,
        intent_override: Optional[str] = None,
    ) -> RunOutcome:
        from local_engine.runtime.runtime import Runtime

        return Runtime(self).execute(
            RuntimeInput(
                project_root=project_root,
                raw_input=task_text,
                input_file=input_file,
                mode=mode,
                workers=workers,
                worker_factory=worker_factory,
                apply_approved=apply_approved,
                event_callback=event_callback,
                skill=skill,
                intent_override=intent_override,
            )
        )

    def apply_run(
        self,
        project_root: Path,
        run_id: str,
        apply_approved: bool = False,
        verify: bool = True,
    ):
        """Apply generated artifacts from an existing project-local run."""
        return apply_existing_run(project_root, run_id, apply_approved=apply_approved, verify=verify)
