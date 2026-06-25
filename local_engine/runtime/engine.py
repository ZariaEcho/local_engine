"""The Task Graph Runtime: initialization, execution, persistence, and integration."""

from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from local_engine.agents.registry import AgentRegistry
from local_engine.agents.schema import validate_agent_skill_references
from local_engine.artifacts.recover_report import detect_status, recover_report
from local_engine.artifacts.artifact_store import ArtifactStore
from local_engine.artifacts.patch_collector import collect_patches
from local_engine.artifacts.report_builder import build_final_report
from local_engine.compiler.prompt_compiler import compile_task_prompt, context_quality_warnings_section
from local_engine.context.context_builder import build_context, repo_summary, write_project_context
from local_engine.context.context_quality import ContextQualityReport, assess_context_quality
from local_engine.context.project_type import detect_project_type
from local_engine.context.repo_scanner import RepoInfo, scan_project
from local_engine.eval.eval_runner import build_eval_report
from local_engine.graph.dynamic_builder import build_graph, build_skill_graph
from local_engine.graph.graph_quality import GraphQualityError, graph_quality_check
from local_engine.graph.graph_validator import validate_graph
from local_engine.intake.input_loader import load_input
from local_engine.intake.requirement_normalizer import normalize_requirement
from local_engine.integrator.integrator import build_integration_review
from local_engine.intents.registry import IntentRegistry
from local_engine.intents.classification import ClarificationRequired, ClassificationResult
from local_engine.memory.memory_loader import load_engine_memory, load_project_memory, read_text
from local_engine.memory.memory_writer import write_memory_update
from local_engine.runtime.config import ensure_engine_home, load_engine_config, load_preferences, load_yaml
from local_engine.runtime.errors import write_error_artifact
from local_engine.runtime.execution_context import RunExecutionContext
from local_engine.runtime.events import RuntimeEventRecorder
from local_engine.runtime.fallback import FallbackPolicy
from local_engine.runtime.reporting import write_run_metadata
from local_engine.runtime.retry import RetryPolicy, invoke_worker, run_with_recovery, write_recovery_artifacts
from local_engine.runtime.run_context import RunContext, active_run_id, clear_active_run, new_run_context
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.run_store import RunStore
from local_engine.runtime.state import load_state, update_state, write_state
from local_engine.runtime.task_cache import TaskCache, fingerprint_repository, stable_hash
from local_engine.runtime.telemetry import write_telemetry_event
from local_engine.runtime.executor_manager import ExecutorManager
from local_engine.runtime.quality import QualityEvaluator
from local_engine.runtime.task_summary import build_task_summary
from local_engine.safety.approval_gate import require_approval
from local_engine.safety.patch_validator import validate_patch_set
from local_engine.safety.permission_guard import validate_project_root
from local_engine.scheduler.parallel_scheduler import ParallelScheduler
from local_engine.kernel.schemas import FailureType, TaskResult, make_error_sip
from local_engine.kernel.sip_parser import parse_sip
from local_engine.skills.registry import SkillRegistry
from local_engine.task_templates.registry import TaskTemplateRegistry
from local_engine.workers.claude_cli_worker import ClaudeCLIWorker


@dataclass
class RunOutcome:
    run_id: str
    report_dir: Path
    final_report: Path
    warnings: List[str]
    task_statuses: Dict[str, str]
    error_log: Optional[Path] = None

    @property
    def has_failures(self) -> bool:
        return any(status in {"failed_but_continued", "failed", "needs_human", "logic_failed"} for status in self.task_statuses.values())


_NEEDS_HUMAN_FAILURES = {
    FailureType.LOGIC.value,
    FailureType.PERMISSION_REQUEST.value,
    FailureType.CLARIFICATION_REQUEST.value,
    FailureType.TOOL_REQUEST.value,
}


def _indexed_run(method: Callable[..., RunOutcome]) -> Callable[..., RunOutcome]:
    """Finalize a reserved run record if execution raises unexpectedly."""
    def wrapped(*args: Any, **kwargs: Any) -> RunOutcome:
        clear_active_run()
        try:
            outcome = method(*args, **kwargs)
            clear_active_run()
            return outcome
        except KeyboardInterrupt:
            run_id = active_run_id()
            if run_id:
                try:
                    _finalize_recoverable_run(run_id, "KeyboardInterrupt", forced_status="interrupted")
                finally:
                    clear_active_run()
            raise
        except Exception as exc:
            run_id = active_run_id()
            if run_id:
                try:
                    _finalize_recoverable_run(run_id, str(exc))
                finally:
                    clear_active_run()
            raise

    return wrapped


def _finalize_recoverable_run(run_id: str, error: str = "", forced_status: Optional[str] = None) -> None:
    """Best-effort report recovery used when the runtime exits before normal finalization."""
    index = RunIndex()
    record = index.get(run_id) or {}
    report_dir_text = str(record.get("report_dir", "")).strip()
    report_dir = Path(report_dir_text).expanduser() if report_dir_text else None
    if report_dir is None or not report_dir.is_dir():
        index.finalize(run_id, status=forced_status or "failed", error=error)
        return

    final_report = report_dir / "final_report.md"
    if forced_status == "interrupted":
        write_error_artifact(
            report_dir / "artifacts",
            "interrupted",
            "context",
            "KeyboardInterrupt",
            "Run interrupted by KeyboardInterrupt",
            False,
        )
    elif error and not final_report.is_file():
        write_error_artifact(report_dir / "artifacts", "run", "context", "RuntimeError", error, False)

    if not final_report.is_file():
        final_report = recover_report(report_dir)
    status = forced_status or detect_status(report_dir)
    index.finalize(run_id, status=status, report_path=final_report, report_dir=str(report_dir), error=error)


class Engine:
    """Coordinates the specified Task Graph Runtime without a Codex adapter."""

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
        repo_info = scan_project(root)
        context_path = write_project_context(repo_info, state)
        context_file = state / "context.md"
        context_file.write_text(context_path.read_text(encoding="utf-8"), encoding="utf-8")
        memory_file = state / "memory.md"
        if not memory_file.exists():
            memory_file.write_text("# Project Memory\n", encoding="utf-8")
        return state

    def scan(self, project_root: Path) -> RepoInfo:
        """Refresh the repository map and canonical project context without a worker run."""
        root = validate_project_root(project_root)
        repo_info = scan_project(root)
        state = root / ".local_engine"
        context_path = write_project_context(repo_info, state)
        (state / "context.md").write_text(context_path.read_text(encoding="utf-8"), encoding="utf-8")
        return repo_info

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
        return self._classify_requirement(
            IntentRegistry.load(), normalized["raw_requirement"], intent_override=intent_override, skill=skill
        )

    def preview_graph(
        self, project_root: Path, task_text: str = "", intent_override: Optional[str] = None
    ) -> Dict[str, Any]:
        """Return a validated intent graph without invoking any Claude worker."""
        repo_info = self.scan(project_root)
        text = task_text or "Create a project plan"
        normalized = normalize_requirement({"raw": text, "task_text": text, "source_type": "text"})
        project_type = detect_project_type(Path(project_root).expanduser().resolve(), repo_info, normalized)
        intents = IntentRegistry.load()
        templates = TaskTemplateRegistry.load()
        skills = SkillRegistry.load()
        agents = AgentRegistry.load()
        classification = self._classify_requirement(intents, normalized["raw_requirement"], intent_override=intent_override)
        graph = build_graph(
            classification.intent,
            build_context(repo_info),
            "preview",
            normalized,
            intents,
            templates,
            skills,
            agents,
            project_type=project_type.to_dict(),
        )
        graph.setdefault("metadata", {})["classification"] = classification.to_dict()
        validate_graph(graph, self._capabilities())
        quality = graph_quality_check(graph, classification.intent, intents, templates)
        graph["metadata"]["graph_quality"] = quality.to_dict()
        if not quality.passed:
            raise GraphQualityError(quality)
        return graph

    @_indexed_run
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
        started_at = time.monotonic()
        if mode not in {"plan", "apply"}:
            raise ValueError("mode must be 'plan' or 'apply'")
        root = validate_project_root(project_root)
        agents = AgentRegistry.load()
        skills = SkillRegistry.load()
        intents = IntentRegistry.load()
        templates = TaskTemplateRegistry.load()
        loaded = load_input(task_text, input_file)
        normalized = normalize_requirement(loaded)
        classification = self._classify_requirement(
            intents, normalized["raw_requirement"], intent_override=intent_override, skill=skill
        )
        context = new_run_context(root)
        hook_recorder = RuntimeEventRecorder(context.run_id, context.report_dir)
        hook_recorder.emit("before_run", {"mode": mode, "skill": skill or "", "intent_override": intent_override or ""})
        write_run_metadata(
            context.global_run_dir,
            {
                "run_id": context.run_id,
                "project": str(root),
                "project_root": str(root),
                "input": normalized["raw_requirement"],
                "report_dir": str(context.report_dir),
                "report_path": "",
                "deliverables_path": str(context.deliverables_dir),
                "status": "running",
            },
        )
        config = load_engine_config()
        update_state(
            context.report_dir,
            phase="initializing",
            status="running",
            config={"hooks_enabled": bool(config.get("hooks", {}).get("enabled", True)), "loop_enabled": bool(config.get("loop", {}).get("enabled", False))},
        )
        for agent in agents:
            validate_agent_skill_references(agent, skills.names)
        _preferences = load_preferences()  # loaded intentionally; preferences are part of the run contract
        project_config = load_yaml(context.project_state / "project.yaml")
        repo_info = self.scan(root)
        repository_fingerprint = fingerprint_repository(root, context.project_state / "cache")
        project_context = read_text(context.project_state / "PROJECT_CONTEXT.md")
        context_quality = assess_context_quality(root, repo_info, project_context)
        project_type = detect_project_type(root, repo_info, normalized)
        execution_context = RunExecutionContext(
            mode=mode,
            apply_approved=apply_approved,
            project_root=root,
            allowed_write_root=root,
        )
        if project_config:
            project_context = "{0}\n\n# Project Config\n{1}".format(
                project_context, yaml.safe_dump(project_config, sort_keys=False)
            )
        project_memory = load_project_memory(context.project_state)
        engine_memory = load_engine_memory()

        context.write_text("raw_input.md", loaded["raw"])
        context.write_yaml("normalized_requirement.yaml", normalized)
        context.write_text("repo_context.json", json.dumps(repo_info.to_dict(), ensure_ascii=False, indent=2) + "\n")
        project_type_json = context.write_text("artifacts/project_type.json", project_type.to_json())
        context.write_yaml("internal/repo_map.yaml", repo_info.to_dict())
        context.write_text("internal/PROJECT_CONTEXT.md", project_context)
        context_quality_json = context.write_text(
            "artifacts/context_quality.json", json.dumps(context_quality.to_dict(), ensure_ascii=False, indent=2) + "\n"
        )
        context_quality_markdown = context.write_text("artifacts/context_quality.md", context_quality.to_markdown())
        intent = classification.intent
        context.write_text("internal/intent.txt", intent + "\n")
        context.write_yaml("internal/classification.yaml", classification.to_dict())

        if worker_factory is None:
            worker_factory = self._configured_worker_factory(config)

        if skill:
            selected = skills.get(skill)
            graph = build_skill_graph(context.run_id, normalized, selected.name, selected.default_agent)
            intent = "SKILL"
        else:
            graph = build_graph(
                intent,
                project_context,
                context.run_id,
                normalized,
                intents,
                templates,
                skills,
                agents,
                project_type=project_type.to_dict(),
            )
        graph.setdefault("metadata", {})["classification"] = classification.to_dict()
        resolved_agents, resolved_skills = self._resolve_task_capabilities(graph, agents, skills)
        validate_graph(graph, set(agents.names) | set(skills.names))
        graph_quality = graph_quality_check(graph, intent, intents, templates)
        graph["metadata"]["graph_quality"] = graph_quality.to_dict()
        context.write_yaml("task_graph.yaml", graph)
        context.write_text(
            "artifacts/graph_quality.json", json.dumps(graph_quality.to_dict(), ensure_ascii=False, indent=2) + "\n"
        )
        context.write_text("internal/graph_quality.md", graph_quality.to_markdown())
        if not graph_quality.passed:
            raise GraphQualityError(graph_quality)
        hook_recorder.emit("after_plan", {"intent": intent, "task_count": len(graph["tasks"]), "graph_quality": graph_quality.to_dict()})
        update_state(
            context.report_dir,
            phase="planned",
            tasks={
                task["id"]: {
                    "status": "pending",
                    "lifecycle_status": "pending",
                    "skill": task["skill"],
                    "agent": task.get("agent", ""),
                    "depends_on": task.get("depends_on", []),
                }
                for task in graph["tasks"]
            },
            artifacts={"task_graph": "task_graph.yaml", "state": "state.json"},
        )
        input_hash = stable_hash(
            {
                "requirement": normalized,
                "intent": intent,
                "classification": classification.to_dict(),
                "project_type": project_type.to_dict(),
                "intent_definition": intents.get(intent).to_dict() if intent != "SKILL" else {},
                "mode": mode,
                "selected_skill": skill or "",
            }
        )
        task_cache = TaskCache(context.project_state / "cache", repository_fingerprint)
        definition_hashes = {
            task["id"]: stable_hash(
                {
                    "task": task,
                    "skill": resolved_skills[task["id"]].to_dict(),
                    "skill_prompt": resolved_skills[task["id"]].prompt_path.read_text(encoding="utf-8"),
                    "agent": resolved_agents[task["id"]].to_dict(),
                    "execution": config.get("execution", {}),
                    "review": config.get("review", {}),
                }
            )
            for task in graph["tasks"]
        }
        run_metadata = {
            "run_id": context.run_id,
            "project": str(root),
            "project_root": str(root),
            "input": normalized["raw_requirement"],
            "report_dir": str(context.report_dir),
            "report_path": "",
            "deliverables_path": str(context.deliverables_dir),
            "mode": mode,
            "graph_source": graph.get("metadata", {}).get("graph_source"),
            "intent": intent,
            "classification": classification.to_dict(),
            "project_type": project_type.to_dict(),
            "status": "running",
            "task_count": len(graph["tasks"]),
            "passed_count": 0,
            "failed_count": 0,
        }
        write_run_metadata(context.global_run_dir, run_metadata)
        self._emit(event_callback, "graph_ready", {"run_id": context.run_id, "tasks": graph["tasks"]})

        def runtime_event_callback(event: str, payload: Dict[str, Any]) -> None:
            task_id = str(payload.get("task_id", ""))
            if event == "task_started":
                hook_recorder.emit("before_task", payload, task_id=task_id)
            elif event == "task_finished":
                hook_recorder.emit("after_task", payload, task_id=task_id)
                if payload.get("failed") or payload.get("lifecycle_status") in {"failed", "needs_human", "logic_failed"}:
                    hook_recorder.emit("on_task_fail", payload, task_id=task_id)
            self._emit(event_callback, event, payload)

        def prompt_for_task(task: Dict[str, Any], dependencies: Dict[str, Any]) -> str:
            prompt = compile_task_prompt(
                task,
                normalized,
                project_context,
                project_memory,
                engine_memory,
                dependencies,
                mode,
                graph.get("metadata", {}),
                repo_summary(repo_info),
                agent=resolved_agents[task["id"]],
                skill_definition=resolved_skills[task["id"]],
                context_quality_warnings=context_quality.warnings,
                execution_context=execution_context,
            )
            context.write_text("prompts/{0}.prompt.md".format(task["id"]), prompt)
            return prompt

        def finalize_task(
            task: Dict[str, Any], prompt: str, dependencies: Dict[str, TaskResult], result: TaskResult
        ) -> TaskResult:
            return self._finalize_task_result(
                task,
                result,
                context,
                normalized,
                project_context,
                project_memory,
                engine_memory,
                mode,
                repo_info,
                worker_factory,
                resolved_agents,
                resolved_skills,
                config,
                context_quality.warnings,
                execution_context,
            )

        def load_cached(task: Dict[str, Any], dependencies: Dict[str, TaskResult]) -> Optional[TaskResult]:
            definition = resolved_skills[task["id"]]
            if not definition.cache.enabled:
                return None
            cached = task_cache.lookup(
                task,
                dependencies,
                input_hash,
                definition_hashes[task["id"]],
                definition.cache.watched_paths,
            )
            if cached is None:
                return None
            context.write_text(
                "prompts/{0}.prompt.md".format(task["id"]),
                "# Cache Reuse\n\nReused verified task `{0}` from run `{1}`.\n\n{2}\n".format(
                    task["id"], cached.source_run_id, context_quality_warnings_section(context_quality.warnings)
                ),
            )
            if not cached.task_summary:
                cached.task_summary = build_task_summary(cached)
            context.write_yaml("artifacts/task_summaries/{0}.yaml".format(task["id"]), cached.task_summary)
            quality = dict(cached.output_quality)
            quality.update(
                {
                    "task_id": task["id"],
                    "triggers": cached.quality_reasons,
                    "review_status": cached.review_status,
                    "lifecycle_status": "skipped",
                    "cache_action": "reuse",
                    "source_run_id": cached.source_run_id,
                }
            )
            context.write_text("artifacts/quality/{0}.json".format(task["id"]), json.dumps(quality, ensure_ascii=False, indent=2) + "\n")
            if cached.context_patch:
                context.write_text("artifacts/context_patches/{0}.md".format(task["id"]), cached.context_patch)
            return cached

        scheduler = ParallelScheduler(workers if workers is not None else config.get("workers", 4))
        results = scheduler.run(
            graph,
            prompt_for_task,
            worker_factory,
            root,
            context.agent_outputs_dir,
            status_callback=runtime_event_callback,
            artifact_dir=context.artifacts_dir,
            agent_resolver=lambda task: resolved_agents[task["id"]],
            execution_config={
                **(config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}),
                "timeout_seconds": config.get("timeout_seconds", 300),
                "apply_approved": apply_approved,
                "execution_contract": execution_context.to_prompt_section(),
            },
            result_finalizer=finalize_task,
            result_loader=load_cached,
        )
        quality_failures = {}
        for task_id, result in results.items():
            if result.lifecycle_status in {"failed", "needs_human", "logic_failed"} or result.review_status == "failed" or result.warnings:
                payload = {
                    "task_id": task_id,
                    "lifecycle_status": result.lifecycle_status,
                    "review_status": result.review_status,
                    "warnings": result.warnings,
                    "quality_score": result.quality_score,
                }
                quality_failures[task_id] = payload
                hook_recorder.emit("on_quality_fail", payload, task_id=task_id)
        self._write_run_state(context, graph, results, "tasks_completed", status="running", quality_failures=quality_failures)
        for task in graph["tasks"]:
            definition = resolved_skills[task["id"]]
            task_cache.store(
                context.run_id,
                task,
                results[task["id"]],
                input_hash,
                definition_hashes[task["id"]],
                definition.cache.watched_paths,
            )
        task_cache.flush()
        review_summary = self._write_review_summary(graph, results, context)
        patch_paths = collect_patches(results, context.patches_dir, graph)
        artifact_paths, deliverable_paths = self._write_task_outputs(graph, results, context)
        artifact_paths.extend([context_quality_json, context_quality_markdown, project_type_json])
        artifact_paths.append(review_summary)
        artifact_paths.extend(self._write_execution_artifacts(graph, results, context))
        self._store_durable_artifacts(graph, results, root, intent)
        integration_review, integration_warnings = build_integration_review(results, patch_paths, graph)
        warnings = self._collect_run_warnings(
            graph, graph_quality, context_quality, classification, results, integration_warnings
        )
        context.write_text("integration_review.md", integration_review)
        context.write_text("eval_report.md", build_eval_report(graph, results, context.report_dir, mode))
        memory_update = write_memory_update(context.project_state, context.run_id, warnings)
        context.write_text("memory_update.md", memory_update)
        error_log = self._write_error_log(results, context)
        delivery = context.write_text("deliverables/FINAL_DELIVERY.md", self._build_delivery_summary(context.run_id, graph, results, deliverable_paths))
        deliverable_paths.append(delivery)

        if mode == "apply":
            require_approval(apply_approved)
            self._apply_patches(root, patch_paths)

        hook_recorder.emit("before_report", {"warnings": warnings, "deliverable_count": len(deliverable_paths)})
        final = context.write_text(
            "final_report.md",
            build_final_report(
                context.run_id,
                graph,
                results,
                warnings,
                patch_paths,
                artifact_paths,
                deliverable_paths,
                error_log=error_log,
                context_quality=context_quality.to_dict(),
            ),
        )
        hook_recorder.emit("after_report", {"final_report": str(final)})
        task_statuses = {
            task_id: result.status
            for task_id, result in results.items()
        }
        lifecycle_statuses = {task_id: result.lifecycle_status for task_id, result in results.items()}
        failed_states = {"failed", "failed_but_continued", "needs_human", "logic_failed"}
        passed_count = sum(
            task_statuses[task_id] not in failed_states and lifecycle_statuses[task_id] not in failed_states
            for task_id in task_statuses
        )
        failed_count = len(task_statuses) - passed_count
        run_status = detect_status(context.report_dir)
        self._write_run_state(
            context,
            graph,
            results,
            "finished",
            status=run_status,
            quality_failures=quality_failures,
            final_report=final,
            duration_seconds=time.monotonic() - started_at,
        )
        run_metadata.update(
            {
                "status": run_status,
                "final_report": str(final),
                "report_path": str(final),
                "error_log": str(error_log) if error_log else None,
                "task_statuses": task_statuses,
                "lifecycle_statuses": lifecycle_statuses,
                "passed_count": passed_count,
                "failed_count": failed_count,
            }
        )
        write_run_metadata(context.global_run_dir, run_metadata)
        self._emit(
            event_callback,
            "run_finished",
            {
                "run_id": context.run_id,
                "task_statuses": task_statuses,
                "has_failures": failed_count > 0,
            },
        )
        write_telemetry_event(
            context.project_state,
            config,
            {
                "command_type": "run",
                "run_status": run_status,
                "task_count": len(task_statuses),
                "success_count": passed_count,
                "failure_count": failed_count,
                "duration_seconds": round(time.monotonic() - started_at, 3),
                "executor_type": config.get("execution", {}).get("default_executor", "claude") if isinstance(config.get("execution"), dict) else "claude",
                "error_type": "task_failure" if failed_count else "",
            },
        )
        return RunOutcome(context.run_id, context.report_dir, final, warnings, task_statuses, error_log)

    @staticmethod
    def _configured_worker_factory(config: Dict[str, Any]) -> Callable[[str], ClaudeCLIWorker]:
        """Create workers by model name while retaining legacy ``claude_command`` config."""
        manager = ExecutorManager(config)
        execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}

        def factory(model: str = "claude") -> ClaudeCLIWorker:
            selected = model or str(execution.get("default_executor") or "claude")
            command = manager.command_for(selected)
            return ClaudeCLIWorker(command=command, timeout_seconds=config.get("timeout_seconds", 300))

        return factory

    @staticmethod
    def _write_run_state(
        context: RunContext,
        graph: Dict[str, Any],
        results: Dict[str, TaskResult],
        phase: str,
        status: str = "running",
        quality_failures: Optional[Dict[str, Any]] = None,
        final_report: Optional[Path] = None,
        duration_seconds: Optional[float] = None,
    ) -> None:
        state = load_state(context.report_dir)
        tasks = {}
        task_index = {task["id"]: task for task in graph.get("tasks", [])}
        for task_id, result in results.items():
            task = task_index.get(task_id, {})
            tasks[task_id] = {
                "status": result.status,
                "lifecycle_status": result.lifecycle_status,
                "review_status": result.review_status,
                "review_rounds": result.review_rounds,
                "loop_status": result.loop_status,
                "loop_rounds": result.loop_rounds,
                "loop_history": result.loop_history,
                "cache_action": result.cache_action,
                "failed": result.failed,
                "failure_type": result.failure_type,
                "warnings": result.warnings,
                "quality_score": result.quality_score,
                "agent": task.get("agent", ""),
                "skill": task.get("skill", ""),
            }
        loops = {
            task_id: {
                "status": item["loop_status"],
                "rounds": item["loop_rounds"],
                "history": item["loop_history"],
            }
            for task_id, item in tasks.items()
            if item.get("loop_rounds") or item.get("loop_status") not in {"", "skipped"}
        }
        state.update(
            {
                "status": status,
                "phase": phase,
                "tasks": tasks or state.get("tasks", {}),
                "loops": loops,
                "quality_failures": quality_failures or {},
                "artifacts": {
                    **(state.get("artifacts", {}) if isinstance(state.get("artifacts"), dict) else {}),
                    "task_graph": "task_graph.yaml",
                    "state": "state.json",
                    "final_report": "final_report.md" if final_report else state.get("artifacts", {}).get("final_report", ""),
                },
            }
        )
        if duration_seconds is not None:
            state["duration_seconds"] = round(float(duration_seconds), 3)
        write_state(context.report_dir, state)

    @staticmethod
    def _capabilities() -> set[str]:
        return set(AgentRegistry.load().names) | set(SkillRegistry.load().names)

    @staticmethod
    def _classify_requirement(
        intents: IntentRegistry,
        requirement: str,
        intent_override: Optional[str] = None,
        skill: Optional[str] = None,
    ) -> ClassificationResult:
        """Apply the shared clarification gate to every graph-building path."""
        if skill:
            return ClassificationResult.explicit("SKILL", "explicit reusable skill selection: {0}".format(skill))
        if intent_override:
            definition = intents.get(intent_override)
            return ClassificationResult.explicit(definition.name, "explicit --intent override")
        result = intents.classify_result(requirement)
        if result.needs_clarification:
            raise ClarificationRequired(result)
        return result

    @staticmethod
    def _resolve_task_capabilities(
        graph: Dict[str, Any], agents: AgentRegistry, skills: SkillRegistry
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Bind each graph task to definitions loaded from YAML, not role constants."""
        resolved_agents: Dict[str, Any] = {}
        resolved_skills: Dict[str, Any] = {}
        for task in graph["tasks"]:
            skill = skills.get(task["skill"])
            agent_name = task.get("agent") or skill.default_agent
            agent = agents.get(agent_name)
            if task["skill"] not in agent.skills:
                raise ValueError(
                    "agent `{0}` is not configured for skill `{1}` (task `{2}`)".format(agent.name, task["skill"], task["id"])
                )
            task["agent"] = agent.name
            resolved_agents[task["id"]] = agent
            resolved_skills[task["id"]] = skill
        return resolved_agents, resolved_skills

    def _run_review_loop(
        self,
        graph: Dict[str, Any],
        results: Dict[str, TaskResult],
        context: RunContext,
        normalized: Dict[str, Any],
        project_context: str,
        project_memory: str,
        engine_memory: str,
        mode: str,
        repo_info: RepoInfo,
        worker_factory: Callable[..., Any],
        resolved_agents: Dict[str, Any],
        resolved_skills: Dict[str, Any],
        config: Dict[str, Any],
        context_quality_warnings: Optional[List[str]] = None,
        execution_context: Optional[RunExecutionContext] = None,
    ) -> None:
        """Review executable task outputs and revise them until pass or the configured limit."""
        review_config = config.get("review", {}) if isinstance(config.get("review"), dict) else {}
        loop_config = config.get("loop", {}) if isinstance(config.get("loop"), dict) else {}
        loop_enabled = bool(loop_config.get("enabled", False))
        enabled = bool(review_config.get("enabled", True))
        max_rounds = review_config.get("max_rounds", 2)
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            max_rounds = 2
        agents = AgentRegistry.load()
        skills = SkillRegistry.load()
        reviewer_agent = agents.get(str(review_config.get("reviewer_agent", "reviewer")))
        reviewer_skill = skills.get(str(review_config.get("review_skill", "review_code")))
        if reviewer_skill.name not in reviewer_agent.skills:
            raise ValueError("reviewer agent `{0}` is not configured for `{1}`".format(reviewer_agent.name, reviewer_skill.name))

        for task in graph["tasks"]:
            task_id = task["id"]
            current = results[task_id]
            if task["expected_output"]["type"] in {"review", "memory_update"}:
                self._write_review(context, task, 1, "skipped", "Task type does not enter the execution review loop.", [])
                current.review_status = "skipped"
                continue
            if not enabled:
                self._write_review(context, task, 1, "skipped", "Review loop is disabled by configuration.", [])
                current.review_status = "skipped"
                continue
            if current.failed:
                issues = ["Task execution failed before review: {0}".format(current.error_message or "worker failure")]
                self._write_review(context, task, 1, "skipped", "Task could not be reviewed because execution failed.", issues)
                current.review_status = "skipped"
                current.unresolved_issues = issues
                current.lifecycle_status = "failed"
                continue

            for round_number in range(1, max_rounds + 1):
                current.lifecycle_status = "reviewing"
                review_task = {
                    "id": task_id,
                    "title": "Review {0} (round {1})".format(task["title"], round_number),
                    "skill": reviewer_skill.name,
                    "agent": reviewer_agent.name,
                    "depends_on": [task_id],
                    "expected_output": {"type": "review", "path": "reviews/{0}.round{1}.md".format(task_id, round_number)},
                    "constraints": {
                        "must": ["Return VERDICT: PASS or VERDICT: FAIL with concrete issues."],
                        "must_not": ["Do not modify the project."],
                    },
                }
                review_prompt = compile_task_prompt(
                    review_task,
                    normalized,
                    project_context,
                    project_memory,
                    engine_memory,
                    {task_id: current},
                    mode,
                    graph.get("metadata", {}),
                    repo_summary(repo_info),
                    agent=reviewer_agent,
                    skill_definition=reviewer_skill,
                    context_quality_warnings=context_quality_warnings or [],
                    execution_context=execution_context,
                )
                review_recovery = self._recover(
                    worker_factory,
                    review_task,
                    review_prompt,
                    context,
                    "{0}.review{1}".format(task_id, round_number),
                    reviewer_agent,
                    config,
                    execution_context,
                )
                review_raw = review_recovery.worker_result.raw or ""
                review_sip = (
                    make_error_sip(reviewer_skill.name, task_id, review_raw or review_recovery.worker_result.error_message)
                    if review_recovery.worker_result.failed
                    else parse_sip(review_raw, reviewer_skill.name, task_id)
                )
                passed, issues = self._review_verdict(review_raw, review_sip, review_recovery.worker_result.failed)
                self._write_review(context, task, round_number, "passed" if passed else "failed", review_raw, issues)
                current.review_rounds = round_number
                current.review_status = "passed" if passed else "failed"
                current.unresolved_issues = [] if passed else issues
                if passed:
                    current.lifecycle_status = "passed"
                    break
                if round_number == max_rounds:
                    current.lifecycle_status = "failed"
                    break

                current.lifecycle_status = "revising"
                revision_prompt = self._revision_prompt(
                    task,
                    normalized,
                    project_context,
                    project_memory,
                    engine_memory,
                    mode,
                    graph.get("metadata", {}),
                    repo_info,
                    current,
                    issues,
                    resolved_agents[task_id],
                    resolved_skills[task_id],
                    context_quality_warnings,
                    execution_context,
                )
                revision_recovery = self._recover(
                    worker_factory,
                    task,
                    revision_prompt,
                    context,
                    "{0}.revise{1}".format(task_id, round_number),
                    resolved_agents[task_id],
                    config,
                    execution_context,
                )
                current = self._task_result_from_recovery(task, revision_recovery)
                current.review_rounds = round_number
                current.review_status = "revising"
                results[task_id] = current
                ParallelScheduler._persist(context.agent_outputs_dir, task, current)

    def _finalize_task_result(
        self,
        task: Dict[str, Any],
        current: TaskResult,
        context: RunContext,
        normalized: Dict[str, Any],
        project_context: str,
        project_memory: str,
        engine_memory: str,
        mode: str,
        repo_info: RepoInfo,
        worker_factory: Callable[..., Any],
        resolved_agents: Dict[str, Any],
        resolved_skills: Dict[str, Any],
        config: Dict[str, Any],
        context_quality_warnings: List[str],
        execution_context: Optional[RunExecutionContext] = None,
    ) -> TaskResult:
        """Apply quality policy and optional review before releasing dependencies."""
        review_config = config.get("review", {}) if isinstance(config.get("review"), dict) else {}
        loop_config = config.get("loop", {}) if isinstance(config.get("loop"), dict) else {}
        loop_enabled = bool(loop_config.get("enabled", False))
        enabled = bool(review_config.get("enabled", True))
        threshold = review_config.get("threshold", 0.75)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            threshold = 0.75
        threshold = max(0.0, min(1.0, float(threshold)))
        max_rounds = review_config.get("max_rounds", 2)
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            max_rounds = 2
        if loop_enabled:
            loop_rounds = loop_config.get("max_rounds", max_rounds)
            if not isinstance(loop_rounds, bool) and isinstance(loop_rounds, int) and loop_rounds >= 1:
                max_rounds = loop_rounds
        skill = resolved_skills[task["id"]]
        triggers = self._quality_triggers(task, current, skill, review_config, threshold)
        current.quality_score = self._confidence(current)
        current.quality_reasons = list(triggers)
        current.warnings = [str(value) for value in current.sip.get("warnings", [])]

        excluded = task["expected_output"]["type"] in {"review", "memory_update"}
        if current.failed:
            current.review_status = "skipped"
            current.loop_status = "needs_human" if loop_enabled and current.failure_type in _NEEDS_HUMAN_FAILURES else "skipped"
            current.lifecycle_status = "needs_human" if current.failure_type in _NEEDS_HUMAN_FAILURES else "failed"
            current.unresolved_issues = [current.error_message or "Task execution failed"]
        elif excluded or not enabled or not triggers:
            current.review_status = "skipped"
            current.loop_status = "skipped"
            current.lifecycle_status = "passed"
            self._write_review(
                context,
                task,
                1,
                "skipped",
                "Review was not required by the quality policy.",
                [],
            )
        else:
            agents = AgentRegistry.load()
            skills = SkillRegistry.load()
            reviewer_agent = agents.get(str(review_config.get("reviewer_agent", "reviewer")))
            reviewer_skill = skills.get(str(review_config.get("review_skill", "review_code")))
            if reviewer_skill.name not in reviewer_agent.skills:
                raise ValueError("reviewer agent `{0}` is not configured for `{1}`".format(reviewer_agent.name, reviewer_skill.name))
            for round_number in range(1, max_rounds + 1):
                current.lifecycle_status = "reviewing"
                review_task = {
                    "id": task["id"],
                    "title": "Review {0} (round {1})".format(task["title"], round_number),
                    "skill": reviewer_skill.name,
                    "agent": reviewer_agent.name,
                    "depends_on": [task["id"]],
                    "expected_output": {"type": "review", "path": "reviews/{0}.round{1}.md".format(task["id"], round_number)},
                    "constraints": {
                        "must": ["Return VERDICT: PASS or VERDICT: FAIL with concrete issues."],
                        "must_not": ["Do not modify the project."],
                    },
                }
                review_prompt = compile_task_prompt(
                    review_task,
                    normalized,
                    project_context,
                    project_memory,
                    engine_memory,
                    {task["id"]: current},
                    mode,
                    {"quality_triggers": triggers},
                    repo_summary(repo_info),
                    agent=reviewer_agent,
                    skill_definition=reviewer_skill,
                    context_quality_warnings=context_quality_warnings,
                    execution_context=execution_context,
                )
                review_recovery = self._recover(
                    worker_factory,
                    review_task,
                    review_prompt,
                    context,
                    "{0}.review{1}".format(task["id"], round_number),
                    reviewer_agent,
                    config,
                    execution_context,
                )
                review_raw = review_recovery.worker_result.raw or ""
                review_sip = (
                    make_error_sip(reviewer_skill.name, task["id"], review_raw or review_recovery.worker_result.error_message)
                    if review_recovery.worker_result.failed
                    else parse_sip(review_raw, reviewer_skill.name, task["id"])
                )
                passed, issues = self._review_verdict(review_raw, review_sip, review_recovery.worker_result.failed)
                self._write_review(context, task, round_number, "passed" if passed else "failed", review_raw, issues)
                current.review_rounds = round_number
                current.review_status = "passed" if passed else "failed"
                current.unresolved_issues = [] if passed else issues
                if passed:
                    if current.loop_rounds:
                        current.loop_status = "passed"
                    current.lifecycle_status = "passed"
                    break
                if round_number == max_rounds:
                    if loop_enabled:
                        current.loop_status = "needs_human"
                    current.lifecycle_status = "failed"
                    break
                current.lifecycle_status = "revising"
                if loop_enabled:
                    current.loop_rounds += 1
                    current.loop_status = "retrying"
                    current.loop_history.append(
                        {
                            "round": round_number,
                            "trigger": "review_failed",
                            "action": "generate_fix_task",
                            "issues": list(issues),
                        }
                    )
                revision_prompt = self._revision_prompt(
                    task,
                    normalized,
                    project_context,
                    project_memory,
                    engine_memory,
                    mode,
                    {"quality_triggers": triggers},
                    repo_info,
                    current,
                    issues,
                    resolved_agents[task["id"]],
                    resolved_skills[task["id"]],
                    context_quality_warnings,
                    execution_context,
                )
                revision_recovery = self._recover(
                    worker_factory,
                    task,
                    revision_prompt,
                    context,
                    "{0}.revise{1}".format(task["id"], round_number),
                    resolved_agents[task["id"]],
                    config,
                    execution_context,
                )
                current = self._task_result_from_recovery(task, revision_recovery)
                current.review_rounds = round_number
                current.review_status = "revising"
                if loop_enabled:
                    current.loop_rounds = round_number
                    current.loop_status = "retrying"
                    current.loop_history.append(
                        {
                            "round": round_number,
                            "trigger": "review_failed",
                            "action": "retry",
                            "failed": current.failed,
                        }
                    )
                if current.failed:
                    if loop_enabled:
                        current.loop_status = "needs_human" if current.failure_type in _NEEDS_HUMAN_FAILURES else "failed"
                    current.lifecycle_status = "needs_human" if current.failure_type in _NEEDS_HUMAN_FAILURES else "failed"
                    break

        current.quality_score = self._confidence(current)
        output_quality = QualityEvaluator(config.get("quality")).evaluate(task, current)
        current.output_quality = output_quality.to_dict()
        current.warnings = list(
            dict.fromkeys([str(value) for value in current.sip.get("warnings", [])] + output_quality.warnings)
        )
        current.task_summary = build_task_summary(current)
        context.write_yaml("artifacts/task_summaries/{0}.yaml".format(task["id"]), current.task_summary)
        current.context_patch = self._write_quality_evidence(context, task, current, triggers, threshold, enabled and bool(triggers))
        return current

    @staticmethod
    def _confidence(result: TaskResult) -> float:
        try:
            return max(0.0, min(1.0, float(result.sip.get("confidence", 0.0))))
        except (TypeError, ValueError):
            return 0.0

    @classmethod
    def _quality_triggers(
        cls, task: Dict[str, Any], result: TaskResult, skill: Any, review_config: Dict[str, Any], threshold: float
    ) -> List[str]:
        triggers: List[str] = []
        if bool(task.get("review_required")) or bool(getattr(skill, "review_required", False)):
            triggers.append("review_required")
        if cls._confidence(result) < threshold:
            triggers.append("low_confidence")
        risky_types = {str(value) for value in review_config.get("risky_task_types", [])}
        if str(task.get("task_type", "")) in risky_types:
            triggers.append("risky_task_type")
        risky_tags = {str(value) for value in review_config.get("risky_tags", ["code_change", "architecture", "security"])}
        if risky_tags.intersection(str(value) for value in task.get("risk_tags", [])):
            triggers.append("risky_change")
        if task.get("expected_output", {}).get("type") == "patch":
            triggers.append("code_change")
        if result.sip.get("warnings"):
            triggers.append("worker_warning")
        return list(dict.fromkeys(triggers))

    @staticmethod
    def _write_quality_evidence(
        context: RunContext,
        task: Dict[str, Any],
        result: TaskResult,
        triggers: List[str],
        threshold: float,
        review_required: bool,
    ) -> str:
        payload = dict(result.output_quality)
        payload.update(
            {
                "task_id": task["id"],
                "failure_type": result.failure_type,
                # These top-level fields preserve the P1 Revised quality-artifact
                # interface while the evaluator owns quality/completeness fields.
                "triggers": triggers,
                "review_required": review_required,
                "review_status": result.review_status,
                "lifecycle_status": result.lifecycle_status,
                "threshold": threshold,
                "review": {
                    "confidence": result.quality_score,
                    "triggers": triggers,
                    "required": review_required,
                    "status": result.review_status,
                    "threshold": threshold,
                },
            }
        )
        context.write_text("artifacts/quality/{0}.json".format(task["id"]), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        needs_patch = (
            result.quality_score < threshold
            or bool(result.warnings)
            or result.lifecycle_status in {"failed", "needs_human", "logic_failed"}
            or bool(result.unresolved_issues)
        )
        if not needs_patch:
            return ""
        lines = [
            "# Context Patch: {0}".format(task["id"]),
            "",
            "The upstream result requires independent verification before reuse.",
            "- Confidence: {0:.2f} (threshold {1:.2f})".format(result.quality_score, threshold),
            "- Output quality: {0:.2f}".format(float(result.output_quality.get("quality", 0.0))),
            "- Lifecycle: {0}".format(result.lifecycle_status),
        ]
        if triggers:
            lines.append("- Quality triggers: {0}".format(", ".join(triggers)))
        for warning in result.warnings:
            lines.append("- Warning: {0}".format(warning))
        for issue in result.unresolved_issues:
            lines.append("- Unresolved: {0}".format(issue))
        content = "\n".join(lines) + "\n"
        context.write_text("artifacts/context_patches/{0}.md".format(task["id"]), content)
        return content

    @staticmethod
    def _recover(
        worker_factory: Callable[..., Any],
        task: Dict[str, Any],
        prompt: str,
        context: RunContext,
        artifact_id: str,
        agent: Any,
        config: Dict[str, Any],
        execution_context: Optional[RunExecutionContext] = None,
    ) -> Any:
        execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}
        recovery = run_with_recovery(
            lambda model, call_prompt, timeout=None: invoke_worker(worker_factory, model, call_prompt, task, context.project_root, timeout),
            prompt,
            task["id"],
            getattr(agent.model, "primary", "claude"),
            RetryPolicy.from_config(execution, agent),
            FallbackPolicy.from_config(execution, agent),
            skill=task["skill"],
            timeout_seconds=config.get("timeout_seconds", 300),
            apply_approved=bool(execution_context.apply_approved) if execution_context is not None else False,
            execution_contract=execution_context.to_prompt_section() if execution_context is not None else "",
        )
        write_recovery_artifacts(context.artifacts_dir, artifact_id, recovery)
        return recovery

    @staticmethod
    def _task_result_from_recovery(task: Dict[str, Any], recovery: Any) -> TaskResult:
        worker_result = recovery.worker_result
        raw = worker_result.raw or ""
        sip = make_error_sip(task["skill"], task["id"], raw or worker_result.error_message) if worker_result.failed else parse_sip(raw, task["skill"], task["id"])
        status = "failed_but_continued" if worker_result.failed else (
            "warning" if sip.get("type") in {"error", "parse_error", "unstructured"} else "completed"
        )
        return TaskResult(
            task["id"],
            raw,
            sip,
            failed=worker_result.failed,
            status=status,
            error_message=worker_result.error_message,
            model=recovery.model,
            retry_history=[attempt.to_dict() for attempt in recovery.attempts],
            lifecycle_status=(
                "needs_human"
                if (recovery.failure_type or worker_result.failure_type) in _NEEDS_HUMAN_FAILURES
                else ("failed" if worker_result.failed else "completed")
            ),
            failure_type=recovery.failure_type or worker_result.failure_type,
            warnings=[str(value) for value in sip.get("warnings", [])],
        )

    @staticmethod
    def _review_verdict(raw: str, sip: Dict[str, Any], failed: bool) -> Tuple[bool, List[str]]:
        if failed or sip.get("type") == "error":
            return False, [str(sip.get("body") or "Review worker failed")]
        text = "{0}\n{1}".format(raw, sip.get("body", ""))
        verdict = re.search(r"\bVERDICT\s*:\s*(PASS|FAIL)\b", text, flags=re.IGNORECASE)
        if verdict and verdict.group(1).upper() == "PASS":
            return True, []
        if verdict and verdict.group(1).upper() == "FAIL":
            return False, Engine._review_issues(text, sip)
        # Existing worker implementations may not know the new review contract; a
        # non-error response remains a pass until they emit an explicit fail verdict.
        return True, []

    @staticmethod
    def _review_issues(text: str, sip: Dict[str, Any]) -> List[str]:
        issues = [str(value) for value in sip.get("risks", []) + sip.get("unknowns", []) if str(value).strip()]
        body = str(sip.get("body", "")).strip()
        if body:
            issues.append(body)
        if not issues:
            issues.append(text.strip() or "Reviewer did not provide a specific issue.")
        return issues

    @staticmethod
    def _write_review(
        context: RunContext, task: Dict[str, Any], round_number: int, status: str, raw: str, issues: List[str]
    ) -> Path:
        lines = [
            "# Task Review",
            "",
            "- Task: `{0}`".format(task["id"]),
            "- Round: {0}".format(round_number),
            "- Status: {0}".format(status),
            "",
            "## Unresolved Issues",
        ]
        lines.extend("- {0}".format(issue) for issue in issues) if issues else lines.append("- None")
        lines.extend(["", "## Review Output", "", raw or "(no review output)", ""])
        return context.write_text("reviews/{0}.round{1}.md".format(task["id"], round_number), "\n".join(lines))

    @staticmethod
    def _revision_prompt(
        task: Dict[str, Any],
        normalized: Dict[str, Any],
        project_context: str,
        project_memory: str,
        engine_memory: str,
        mode: str,
        graph_metadata: Dict[str, Any],
        repo_info: RepoInfo,
        current: TaskResult,
        issues: List[str],
        agent: Any,
        skill: Any,
        context_quality_warnings: Optional[List[str]] = None,
        execution_context: Optional[RunExecutionContext] = None,
    ) -> str:
        prompt = compile_task_prompt(
            task,
            normalized,
            project_context,
            project_memory,
            engine_memory,
            {task["id"]: current},
            mode,
            graph_metadata,
            repo_summary(repo_info),
            agent=agent,
            skill_definition=skill,
            context_quality_warnings=context_quality_warnings or [],
            execution_context=execution_context,
        )
        return "{0}\n\n# Required Revision\nThe review did not pass. Address every issue below before returning the updated SIP output.\n{1}\n".format(
            prompt, "\n".join("- {0}".format(issue) for issue in issues)
        )

    @staticmethod
    def _collect_run_warnings(
        graph: Dict[str, Any],
        graph_quality: Any,
        context_quality: ContextQualityReport,
        classification: ClassificationResult,
        results: Dict[str, TaskResult],
        integration_warnings: List[str],
    ) -> List[str]:
        """Make every non-fatal control signal visible in the final report."""
        metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata"), dict) else {}
        warnings: List[str] = [str(value) for value in metadata.get("warnings", [])]
        warnings.extend("graph quality: {0}".format(value) for value in getattr(graph_quality, "warnings", []))
        warnings.extend("context quality: {0}".format(value) for value in context_quality.warnings)
        if classification.confidence < 0.6:
            warnings.append("classification low confidence: {0:.2f}".format(classification.confidence))
        warnings.extend(str(value) for value in integration_warnings)
        for task_id, result in results.items():
            warnings.extend("{0}: {1}".format(task_id, value) for value in result.warnings)
            warnings.extend(
                "{0}: {1}".format(task_id, value)
                for value in result.output_quality.get("warnings", [])
                if str(value).strip()
            )
            for attempt in result.retry_history:
                if attempt.get("failed"):
                    warnings.append(
                        "{0}: retry warning ({1}): {2}".format(
                            task_id,
                            attempt.get("failure_type") or attempt.get("stage") or "unknown",
                            attempt.get("error_message") or "worker/output failure",
                        )
                    )
            if result.cache_action == "reuse" or result.lifecycle_status == "skipped":
                warnings.append(
                    "{0}: skipped task; reused verified cache{1}.".format(
                        task_id, " from {0}".format(result.source_run_id) if result.source_run_id else ""
                    )
                )
            if result.failed or result.lifecycle_status in {"failed", "needs_human", "logic_failed"}:
                warnings.append("{0}: failed task: {1}".format(task_id, result.error_message or result.lifecycle_status))
        return list(dict.fromkeys(warnings))

    @staticmethod
    def _write_task_outputs(
        graph: Dict[str, Any], results: Dict[str, Any], context: RunContext
    ) -> tuple:
        artifacts: List[Path] = []
        deliverables: List[Path] = []
        for task in graph["tasks"]:
            expected = task["expected_output"]
            if expected["type"] in {"patch", "memory_update"} or expected["path"] == "integration_review.md":
                continue
            content = "# {0}\n\n{1}\n".format(task["title"], results[task["id"]].sip.get("body", ""))
            path = context.write_text(expected["path"], content)
            if expected["path"].startswith("deliverables/"):
                deliverables.append(path)
            else:
                artifacts.append(path)
        return artifacts, deliverables

    @staticmethod
    def _write_review_summary(graph: Dict[str, Any], results: Dict[str, TaskResult], context: RunContext) -> Path:
        """Persist review outcomes independently of the human-readable final report."""
        payload = {
            "run_id": context.run_id,
            "tasks": [
                {
                    "task_id": task["id"],
                    "execution_status": results[task["id"]].status,
                    "lifecycle_status": results[task["id"]].lifecycle_status,
                    "review_rounds": results[task["id"]].review_rounds,
                    "review_status": results[task["id"]].review_status,
                    "unresolved_issues": results[task["id"]].unresolved_issues,
                }
                for task in graph["tasks"]
            ],
        }
        return context.write_text("artifacts/review_summary.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    @staticmethod
    def _write_execution_artifacts(graph: Dict[str, Any], results: Dict[str, Any], context: RunContext) -> List[Path]:
        """Persist structured, machine-readable intermediate execution evidence."""
        task_index = {task["id"]: task for task in graph["tasks"]}
        task_records = []
        for task_id, result in results.items():
            task = task_index[task_id]
            task_records.append(
                {
                    "task_id": task_id,
                    "skill": task["skill"],
                    "expected_output": task["expected_output"],
                    "status": result.status,
                    "lifecycle_status": result.lifecycle_status,
                    "worker_failed": result.failed,
                    "error_message": result.error_message,
                    "agent": task.get("agent"),
                    "model": result.model,
                    "retry_history": result.retry_history,
                    "review_rounds": result.review_rounds,
                    "review_status": result.review_status,
                    "loop_rounds": result.loop_rounds,
                    "loop_status": result.loop_status,
                    "loop_history": result.loop_history,
                    "unresolved_issues": result.unresolved_issues,
                    "failure_type": result.failure_type,
                    "warnings": result.warnings,
                    "quality_score": result.quality_score,
                    "quality_reasons": result.quality_reasons,
                    "output_quality": result.output_quality,
                    "task_summary": result.task_summary,
                    "cache_action": result.cache_action,
                    "source_run_id": result.source_run_id,
                    "sip_type": result.sip.get("type"),
                    "prompt": "prompts/{0}.prompt.md".format(task_id),
                    "task_output": "agent_outputs/{0}.md".format(task_id),
                    "raw_output": "agent_outputs/{0}.raw.txt".format(task_id),
                    "structured_output": "agent_outputs/{0}.sip.yaml".format(task_id),
                }
            )
        manifest = {
            "run_id": context.run_id,
            "graph_source": graph.get("metadata", {}).get("graph_source", "unknown"),
            "intent": graph.get("metadata", {}).get("intent", "unknown"),
            "task_count": len(task_records),
            "tasks": task_records,
        }
        return [
            context.write_yaml("artifacts/task_results.yaml", manifest),
            context.write_yaml(
                "artifacts/execution_manifest.yaml",
                {
                    "run_id": context.run_id,
                    "task_graph": "task_graph.yaml",
                    "task_results": "artifacts/task_results.yaml",
                    "deliverables_dir": "deliverables/",
                    "agent_outputs_dir": "agent_outputs/",
                },
            ),
        ]

    @staticmethod
    def _write_error_log(results: Dict[str, Any], context: RunContext) -> Optional[Path]:
        failures = [(task_id, result) for task_id, result in results.items() if result.failed]
        if not failures:
            return None
        sections = ["# local_engine Execution Errors", "", "This run completed its graph, but one or more Claude CLI calls failed.", ""]
        for task_id, result in failures:
            sections.extend(
                [
                    "## {0}".format(task_id),
                    "- Status: {0}".format(result.status),
                    "- Error: {0}".format(result.error_message or "Claude CLI failed"),
                    "- Output: `agent_outputs/{0}.md`".format(task_id),
                    "",
                    "### Captured Error Output",
                    "",
                    result.raw or "(empty response)",
                    "",
                ]
            )
        return context.write_text("error.log", "\n".join(sections))

    @staticmethod
    def _build_delivery_summary(
        run_id: str, graph: Dict[str, Any], results: Dict[str, Any], deliverables: List[Path]
    ) -> str:
        declared = [task for task in graph["tasks"] if task["expected_output"]["path"].startswith("deliverables/")]
        lines = ["# Final Delivery", "", "Run ID: `{0}`".format(run_id), "", "## Included Deliverables"]
        lines.extend("- `{0}`".format(path.name) for path in deliverables if path.name != "FINAL_DELIVERY.md")
        if len(lines) == 5:
            lines.append("- No task-declared document; review the run report and generated patches.")
        lines.extend(["", "## Declared Deliverable Status"])
        if declared:
            lines.extend(
                "- `{0}` — {1}".format(task["expected_output"]["path"], results[task["id"]].status) for task in declared
            )
        else:
            lines.append("- No task declared a standalone deliverable.")
        lines.extend(["", "## Review Notes", "- See `../final_report.md` for full task state, warnings, and failures.", "- See `../agent_outputs/` for the complete Claude response from every task."])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _emit(callback: Optional[Callable[[str, Dict[str, Any]], None]], event: str, payload: Dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            callback(event, payload)
        except Exception:
            # A terminal/UI observer is optional and must never interrupt a run.
            return

    @staticmethod
    def _store_durable_artifacts(
        graph: Dict[str, Any], results: Dict[str, Any], project_root: Path, intent: str
    ) -> None:
        store = ArtifactStore(project_root)
        for task in graph["tasks"]:
            expected = task["expected_output"]
            if expected["type"] in {"patch", "memory_update"} or expected["path"] == "integration_review.md":
                continue
            content = "# {0}\n\n{1}\n".format(task["title"], results[task["id"]].sip.get("body", ""))
            store.save_artifact(
                Engine._artifact_category(intent, expected["path"], expected["type"]), content, Path(expected["path"]).name
            )

    @staticmethod
    def _artifact_category(intent: str, expected_path: str, output_type: str) -> str:
        parts = Path(expected_path).parts
        if len(parts) > 1 and parts[0] == "artifacts" and parts[1] in {"audit", "plan", "review", "test", "docs"}:
            return parts[1]
        return {
            "AUDIT": "audit",
            "PLAN": "plan",
            "LEARN": "plan",
            "BUILD": "plan",
            "TEST": "test",
            "DOCUMENT": "docs",
            "REFACTOR": "review",
            "RESEARCH": "review",
        }.get(intent, "docs")

    @staticmethod
    def _apply_patches(project_root: Path, patch_paths: List[Path]) -> None:
        if not patch_paths:
            return
        contents = [path.read_text(encoding="utf-8") for path in patch_paths]
        validate_patch_set(contents)
        for patch_path in patch_paths:
            checked = subprocess.run(
                ["git", "apply", "--check", str(patch_path)], cwd=str(project_root), text=True, capture_output=True, check=False
            )
            if checked.returncode != 0:
                raise ValueError("patch failed safety check {0}: {1}".format(patch_path.name, checked.stderr.strip()))
        for patch_path in patch_paths:
            applied = subprocess.run(
                ["git", "apply", str(patch_path)], cwd=str(project_root), text=True, capture_output=True, check=False
            )
            if applied.returncode != 0:
                raise RuntimeError("patch application failed {0}: {1}".format(patch_path.name, applied.stderr.strip()))

    @staticmethod
    def _scan_project_context(project_root: Path) -> str:
        entries = []
        for path in sorted(project_root.rglob("*")):
            relative = path.relative_to(project_root)
            if any(part in {".git", ".local_engine", ".venv", "__pycache__"} for part in relative.parts):
                continue
            if len(entries) >= 200:
                entries.append("... truncated after 200 entries")
                break
            entries.append("- {0}{1}".format(relative.as_posix(), "/" if path.is_dir() else ""))
        return "# Project Context\n\nProject root: `{0}`\n\n## Structure\n{1}\n".format(
            project_root, "\n".join(entries) if entries else "- (empty project)"
        )
