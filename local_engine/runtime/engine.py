"""The Task Graph Runtime: initialization, execution, persistence, and integration."""

from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from local_engine.agents.registry import AgentRegistry
from local_engine.agents.schema import validate_agent_skill_references
from local_engine.artifacts.artifact_store import ArtifactStore
from local_engine.artifacts.patch_collector import collect_patches
from local_engine.artifacts.report_builder import build_final_report
from local_engine.compiler.prompt_compiler import compile_task_prompt
from local_engine.context.context_builder import build_context, repo_summary, write_project_context
from local_engine.context.repo_scanner import RepoInfo, scan_project
from local_engine.eval.eval_runner import build_eval_report
from local_engine.graph.dynamic_builder import build_graph, build_skill_graph
from local_engine.graph.graph_validator import validate_graph
from local_engine.intake.input_loader import load_input
from local_engine.intake.requirement_normalizer import normalize_requirement
from local_engine.integrator.integrator import build_integration_review
from local_engine.intents.registry import IntentRegistry
from local_engine.memory.memory_loader import load_engine_memory, load_project_memory, read_text
from local_engine.memory.memory_writer import write_memory_update
from local_engine.runtime.config import ensure_engine_home, load_engine_config, load_preferences, load_yaml
from local_engine.runtime.fallback import FallbackPolicy
from local_engine.runtime.reporting import write_run_metadata
from local_engine.runtime.retry import RetryPolicy, invoke_worker, run_with_recovery, write_recovery_artifacts
from local_engine.runtime.run_context import RunContext, active_run_id, clear_active_run, new_run_context
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.task_cache import TaskCache, fingerprint_repository, stable_hash
from local_engine.safety.approval_gate import require_approval
from local_engine.safety.patch_validator import validate_patch_set
from local_engine.safety.permission_guard import validate_project_root
from local_engine.scheduler.parallel_scheduler import ParallelScheduler
from local_engine.kernel.schemas import TaskResult, make_error_sip
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


def _indexed_run(method: Callable[..., RunOutcome]) -> Callable[..., RunOutcome]:
    """Finalize a reserved run record if execution raises unexpectedly."""
    def wrapped(*args: Any, **kwargs: Any) -> RunOutcome:
        clear_active_run()
        try:
            outcome = method(*args, **kwargs)
            clear_active_run()
            return outcome
        except Exception as exc:
            run_id = active_run_id()
            if run_id:
                try:
                    RunIndex().fail(run_id, str(exc))
                finally:
                    clear_active_run()
            raise

    return wrapped


class Engine:
    """Coordinates the specified Task Graph Runtime without a Codex adapter."""

    def initialize(self, project_root: Path) -> Path:
        root = validate_project_root(project_root)
        ensure_engine_home()
        state = root / ".local_engine"
        (state / "task_reports").mkdir(parents=True, exist_ok=True)
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

    def preview_graph(self, project_root: Path, task_text: str = "") -> Dict[str, Any]:
        """Return a validated intent graph without invoking any Claude worker."""
        repo_info = self.scan(project_root)
        text = task_text or "Create a project plan"
        normalized = normalize_requirement({"raw": text, "task_text": text, "source_type": "text"})
        intents = IntentRegistry.load()
        templates = TaskTemplateRegistry.load()
        skills = SkillRegistry.load()
        agents = AgentRegistry.load()
        intent = intents.classify(text)
        graph = build_graph(intent, build_context(repo_info), "preview", normalized, intents, templates, skills, agents)
        validate_graph(graph, self._capabilities())
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
    ) -> RunOutcome:
        if mode not in {"plan", "apply"}:
            raise ValueError("mode must be 'plan' or 'apply'")
        root = validate_project_root(project_root)
        context = new_run_context(root)
        config = load_engine_config()
        agents = AgentRegistry.load()
        skills = SkillRegistry.load()
        intents = IntentRegistry.load()
        templates = TaskTemplateRegistry.load()
        for agent in agents:
            validate_agent_skill_references(agent, skills.names)
        _preferences = load_preferences()  # loaded intentionally; preferences are part of the run contract
        project_config = load_yaml(context.project_state / "project.yaml")
        repo_info = self.scan(root)
        repository_fingerprint = fingerprint_repository(root, context.project_state / "cache")
        project_context = read_text(context.project_state / "PROJECT_CONTEXT.md")
        if project_config:
            project_context = "{0}\n\n# Project Config\n{1}".format(
                project_context, yaml.safe_dump(project_config, sort_keys=False)
            )
        project_memory = load_project_memory(context.project_state)
        engine_memory = load_engine_memory()

        loaded = load_input(task_text, input_file)
        normalized = normalize_requirement(loaded)
        context.write_text("raw_input.md", loaded["raw"])
        context.write_yaml("normalized_requirement.yaml", normalized)
        context.write_text("repo_context.json", json.dumps(repo_info.to_dict(), ensure_ascii=False, indent=2) + "\n")
        context.write_yaml("internal/repo_map.yaml", repo_info.to_dict())
        context.write_text("internal/PROJECT_CONTEXT.md", project_context)
        intent = intents.classify(normalized["raw_requirement"])
        context.write_text("internal/intent.txt", intent + "\n")

        if worker_factory is None:
            worker_factory = self._configured_worker_factory(config)

        if skill:
            selected = skills.get(skill)
            graph = build_skill_graph(context.run_id, normalized, selected.name, selected.default_agent)
            intent = "SKILL"
        else:
            graph = build_graph(intent, project_context, context.run_id, normalized, intents, templates, skills, agents)
        resolved_agents, resolved_skills = self._resolve_task_capabilities(graph, agents, skills)
        validate_graph(graph, set(agents.names) | set(skills.names))
        context.write_yaml("task_graph.yaml", graph)
        input_hash = stable_hash(
            {
                "requirement": normalized,
                "intent": intent,
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
            "status": "running",
            "task_count": len(graph["tasks"]),
            "passed_count": 0,
            "failed_count": 0,
        }
        write_run_metadata(context.global_run_dir, run_metadata)
        self._emit(event_callback, "graph_ready", {"run_id": context.run_id, "tasks": graph["tasks"]})

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
                "# Cache Reuse\n\nReused verified task `{0}` from run `{1}`.\n".format(task["id"], cached.source_run_id),
            )
            quality = {
                "task_id": task["id"],
                "confidence": cached.quality_score,
                "warnings": cached.warnings,
                "triggers": cached.quality_reasons,
                "review_status": cached.review_status,
                "lifecycle_status": "skipped",
                "cache_action": "reuse",
                "source_run_id": cached.source_run_id,
            }
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
            status_callback=event_callback,
            artifact_dir=context.artifacts_dir,
            agent_resolver=lambda task: resolved_agents[task["id"]],
            execution_config={
                **(config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}),
                "timeout_seconds": config.get("timeout_seconds", 300),
            },
            result_finalizer=finalize_task,
            result_loader=load_cached,
        )
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
        artifact_paths.append(review_summary)
        artifact_paths.extend(self._write_execution_artifacts(graph, results, context))
        self._store_durable_artifacts(graph, results, root, intent)
        integration_review, integration_warnings = build_integration_review(results, patch_paths, graph)
        warnings = list(graph.get("metadata", {}).get("warnings", [])) + integration_warnings
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

        final = context.write_text(
            "final_report.md",
            build_final_report(
                context.run_id, graph, results, warnings, patch_paths, artifact_paths, deliverable_paths, error_log=error_log
            ),
        )
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
        run_metadata.update(
            {
                "status": "completed_with_failures" if failed_count else "completed",
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
        return RunOutcome(context.run_id, context.report_dir, final, warnings, task_statuses, error_log)

    @staticmethod
    def _configured_worker_factory(config: Dict[str, Any]) -> Callable[[str], ClaudeCLIWorker]:
        """Create workers by model name while retaining legacy ``claude_command`` config."""
        commands = config.get("model_commands", {}) if isinstance(config.get("model_commands"), dict) else {}

        def factory(model: str = "claude") -> ClaudeCLIWorker:
            # ``claude_command`` predates model routing and remains the canonical
            # override for the primary worker, including existing project config.
            command = config.get("claude_command") if model == "claude" else commands.get(model)
            if command is None and model == "claude":
                command = commands.get("claude", ["claude"])
            if command is None:
                command = [model]
            if isinstance(command, str):
                command = [command]
            return ClaudeCLIWorker(command=command, timeout_seconds=config.get("timeout_seconds", 300))

        return factory

    @staticmethod
    def _capabilities() -> set[str]:
        return set(AgentRegistry.load().names) | set(SkillRegistry.load().names)

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
    ) -> None:
        """Review executable task outputs and revise them until pass or the configured limit."""
        review_config = config.get("review", {}) if isinstance(config.get("review"), dict) else {}
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
                )
                review_recovery = self._recover(
                    worker_factory,
                    review_task,
                    review_prompt,
                    context,
                    "{0}.review{1}".format(task_id, round_number),
                    reviewer_agent,
                    config,
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
                )
                revision_recovery = self._recover(
                    worker_factory,
                    task,
                    revision_prompt,
                    context,
                    "{0}.revise{1}".format(task_id, round_number),
                    resolved_agents[task_id],
                    config,
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
    ) -> TaskResult:
        """Apply quality policy and optional review before releasing dependencies."""
        review_config = config.get("review", {}) if isinstance(config.get("review"), dict) else {}
        enabled = bool(review_config.get("enabled", True))
        threshold = review_config.get("threshold", 0.75)
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            threshold = 0.75
        threshold = max(0.0, min(1.0, float(threshold)))
        max_rounds = review_config.get("max_rounds", 2)
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            max_rounds = 2
        skill = resolved_skills[task["id"]]
        triggers = self._quality_triggers(task, current, skill, review_config, threshold)
        current.quality_score = self._confidence(current)
        current.quality_reasons = list(triggers)
        current.warnings = [str(value) for value in current.sip.get("warnings", [])]

        excluded = task["expected_output"]["type"] in {"review", "memory_update"}
        if current.failed:
            current.review_status = "skipped"
            current.lifecycle_status = "needs_human" if current.failure_type == "logic" else "failed"
            current.unresolved_issues = [current.error_message or "Task execution failed"]
        elif excluded or not enabled or not triggers:
            current.review_status = "skipped"
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
                )
                review_recovery = self._recover(
                    worker_factory,
                    review_task,
                    review_prompt,
                    context,
                    "{0}.review{1}".format(task["id"], round_number),
                    reviewer_agent,
                    config,
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
                    {"quality_triggers": triggers},
                    repo_info,
                    current,
                    issues,
                    resolved_agents[task["id"]],
                    resolved_skills[task["id"]],
                )
                revision_recovery = self._recover(
                    worker_factory,
                    task,
                    revision_prompt,
                    context,
                    "{0}.revise{1}".format(task["id"], round_number),
                    resolved_agents[task["id"]],
                    config,
                )
                current = self._task_result_from_recovery(task, revision_recovery)
                current.review_rounds = round_number
                current.review_status = "revising"
                if current.failed:
                    current.lifecycle_status = "needs_human" if current.failure_type == "logic" else "failed"
                    break

        current.quality_score = self._confidence(current)
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
        payload = {
            "task_id": task["id"],
            "confidence": result.quality_score,
            "warnings": result.warnings,
            "failure_type": result.failure_type,
            "triggers": triggers,
            "review_required": review_required,
            "review_status": result.review_status,
            "lifecycle_status": result.lifecycle_status,
            "threshold": threshold,
        }
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
            lifecycle_status="failed" if worker_result.failed else "completed",
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
        )
        return "{0}\n\n# Required Revision\nThe review did not pass. Address every issue below before returning the updated SIP output.\n{1}\n".format(
            prompt, "\n".join("- {0}".format(issue) for issue in issues)
        )

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
                    "unresolved_issues": result.unresolved_issues,
                    "failure_type": result.failure_type,
                    "warnings": result.warnings,
                    "quality_score": result.quality_score,
                    "quality_reasons": result.quality_reasons,
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
