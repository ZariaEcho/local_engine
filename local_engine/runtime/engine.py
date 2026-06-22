"""The Task Graph Runtime: initialization, execution, persistence, and integration."""

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any, Callable, Dict, List, Optional

import yaml

from local_engine.artifacts.artifact_store import ArtifactStore
from local_engine.artifacts.patch_collector import collect_patches
from local_engine.artifacts.report_builder import build_final_report
from local_engine.compiler.prompt_compiler import compile_task_prompt
from local_engine.context.context_builder import build_context, repo_summary, write_project_context
from local_engine.context.repo_scanner import RepoInfo, scan_project
from local_engine.eval.eval_runner import build_eval_report
from local_engine.graph.dynamic_builder import build_graph
from local_engine.graph.graph_validator import validate_graph
from local_engine.intake.input_loader import load_input
from local_engine.intake.requirement_normalizer import normalize_requirement
from local_engine.integrator.integrator import build_integration_review
from local_engine.memory.memory_loader import load_engine_memory, load_project_memory, read_text
from local_engine.memory.memory_writer import write_memory_update
from local_engine.runtime.config import ensure_engine_home, load_engine_config, load_preferences, load_yaml
from local_engine.runtime.reporting import write_run_metadata
from local_engine.runtime.run_context import RunContext, new_run_context
from local_engine.safety.approval_gate import require_approval
from local_engine.safety.patch_validator import validate_patch_set
from local_engine.safety.permission_guard import validate_project_root
from local_engine.scheduler.parallel_scheduler import ParallelScheduler
from local_engine.planner.intent_classifier import classify
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
        return any(status == "failed_but_continued" for status in self.task_statuses.values())


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
        graph = build_graph(classify(text), build_context(repo_info), "preview", normalized)
        validate_graph(graph)
        return graph

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
    ) -> RunOutcome:
        if mode not in {"plan", "apply"}:
            raise ValueError("mode must be 'plan' or 'apply'")
        root = validate_project_root(project_root)
        context = new_run_context(root)
        config = load_engine_config()
        _preferences = load_preferences()  # loaded intentionally; preferences are part of the run contract
        project_config = load_yaml(context.project_state / "project.yaml")
        repo_info = self.scan(root)
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
        context.write_yaml("internal/repo_map.yaml", repo_info.to_dict())
        context.write_text("internal/PROJECT_CONTEXT.md", project_context)
        intent = classify(normalized["raw_requirement"])
        context.write_text("internal/intent.txt", intent + "\n")

        command = config.get("claude_command", ["claude"])
        if isinstance(command, str):
            command = [command]
        if worker_factory is None:
            worker_factory = lambda: ClaudeCLIWorker(command=command, timeout_seconds=config.get("timeout_seconds", 300))

        graph = build_graph(intent, project_context, context.run_id, normalized)
        validate_graph(graph)
        context.write_yaml("task_graph.yaml", graph)
        run_metadata = {
            "run_id": context.run_id,
            "project_root": str(root),
            "report_dir": str(context.report_dir),
            "mode": mode,
            "graph_source": graph.get("metadata", {}).get("graph_source"),
            "intent": intent,
            "status": "running",
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
            )
            context.write_text("prompts/{0}.prompt.md".format(task["id"]), prompt)
            return prompt

        scheduler = ParallelScheduler(workers if workers is not None else config.get("workers", 4))
        results = scheduler.run(
            graph,
            prompt_for_task,
            worker_factory,
            root,
            context.agent_outputs_dir,
            status_callback=event_callback,
        )
        patch_paths = collect_patches(results, context.patches_dir, graph)
        artifact_paths, deliverable_paths = self._write_task_outputs(graph, results, context)
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
        task_statuses = {task_id: result.status for task_id, result in results.items()}
        run_metadata.update(
            {
                "status": "completed_with_failures" if any(result.failed for result in results.values()) else "completed",
                "final_report": str(final),
                "error_log": str(error_log) if error_log else None,
                "task_statuses": task_statuses,
            }
        )
        write_run_metadata(context.global_run_dir, run_metadata)
        self._emit(
            event_callback,
            "run_finished",
            {"run_id": context.run_id, "task_statuses": task_statuses, "has_failures": any(result.failed for result in results.values())},
        )
        return RunOutcome(context.run_id, context.report_dir, final, warnings, task_statuses, error_log)

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
                    "worker_failed": result.failed,
                    "error_message": result.error_message,
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
