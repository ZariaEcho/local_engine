"""Schedule phase: run the DAG with quality/review finalizers and cache."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from local_engine.compiler.prompt_compiler import compile_task_prompt, context_quality_warnings_section
from local_engine.context.context_builder import repo_summary
from local_engine.kernel.schemas import TaskResult
from local_engine.runtime.events import emit_optional
from local_engine.runtime.session import RunSession
from local_engine.runtime.task_lifecycle import finalize_task_result
from local_engine.runtime.task_summary import build_task_summary
from local_engine.scheduler.parallel_scheduler import ParallelScheduler


def schedule_run(session: RunSession) -> None:
    """Execute the planned graph and persist per-task cache entries."""
    inp = session.inp
    context = session.context
    hook_recorder = session.hook_recorder

    def runtime_event_callback(event: str, payload: Dict[str, Any]) -> None:
        task_id = str(payload.get("task_id", ""))
        if event == "task_started":
            hook_recorder.emit("before_task", payload, task_id=task_id)
        elif event == "task_finished":
            hook_recorder.emit("after_task", payload, task_id=task_id)
            if payload.get("failed") or payload.get("lifecycle_status") in {"failed", "needs_human", "logic_failed"}:
                hook_recorder.emit("on_task_fail", payload, task_id=task_id)
        emit_optional(inp.event_callback, event, payload)

    def prompt_for_task(task: Dict[str, Any], dependencies: Dict[str, Any]) -> str:
        prompt = compile_task_prompt(
            task,
            session.normalized,
            session.project_context,
            session.project_memory,
            session.engine_memory,
            dependencies,
            inp.mode,
            session.graph.get("metadata", {}),
            repo_summary(session.repo_info),
            agent=session.resolved_agents[task["id"]],
            skill_definition=session.resolved_skills[task["id"]],
            context_quality_warnings=session.context_quality.warnings,
            execution_context=session.execution_context,
        )
        context.write_text("prompts/{0}.prompt.md".format(task["id"]), prompt)
        return prompt

    def finalize_task(
        task: Dict[str, Any], prompt: str, dependencies: Dict[str, TaskResult], result: TaskResult
    ) -> TaskResult:
        return finalize_task_result(
            task,
            result,
            context,
            session.normalized,
            session.project_context,
            session.project_memory,
            session.engine_memory,
            inp.mode,
            session.repo_info,
            session.worker_factory,
            session.resolved_agents,
            session.resolved_skills,
            session.config,
            session.context_quality.warnings,
            session.execution_context,
        )

    def load_cached(task: Dict[str, Any], dependencies: Dict[str, TaskResult]) -> Optional[TaskResult]:
        definition = session.resolved_skills[task["id"]]
        if not definition.cache.enabled:
            return None
        cached = session.task_cache.lookup(
            task,
            dependencies,
            session.input_hash,
            session.definition_hashes[task["id"]],
            definition.cache.watched_paths,
        )
        if cached is None:
            return None
        context.write_text(
            "prompts/{0}.prompt.md".format(task["id"]),
            "# Cache Reuse\n\nReused verified task `{0}` from run `{1}`.\n\n{2}\n".format(
                task["id"], cached.source_run_id, context_quality_warnings_section(session.context_quality.warnings)
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

    scheduler = ParallelScheduler(inp.workers if inp.workers is not None else session.config.get("workers", 4))
    session.results = scheduler.run(
        session.graph,
        prompt_for_task,
        session.worker_factory,
        session.root,
        context.agent_outputs_dir,
        status_callback=runtime_event_callback,
        artifact_dir=context.artifacts_dir,
        agent_resolver=lambda task: session.resolved_agents[task["id"]],
        execution_config={
            **(session.config.get("execution", {}) if isinstance(session.config.get("execution"), dict) else {}),
            "timeout_seconds": session.config.get("timeout_seconds", 300),
            "apply_approved": inp.apply_approved,
            "execution_contract": session.execution_context.to_prompt_section(),
        },
        result_finalizer=finalize_task,
        result_loader=load_cached,
    )
    session.quality_failures = {}
    for task_id, result in session.results.items():
        if (
            result.lifecycle_status in {"failed", "needs_human", "logic_failed"}
            or result.review_status == "failed"
            or result.warnings
        ):
            payload = {
                "task_id": task_id,
                "lifecycle_status": result.lifecycle_status,
                "review_status": result.review_status,
                "warnings": result.warnings,
                "quality_score": result.quality_score,
            }
            session.quality_failures[task_id] = payload
            hook_recorder.emit("on_quality_fail", payload, task_id=task_id)
    write_run_state(
        context,
        session.graph,
        session.results,
        "tasks_completed",
        status="running",
        quality_failures=session.quality_failures,
    )
    for task in session.graph["tasks"]:
        definition = session.resolved_skills[task["id"]]
        session.task_cache.store(
            context.run_id,
            task,
            session.results[task["id"]],
            session.input_hash,
            session.definition_hashes[task["id"]],
            definition.cache.watched_paths,
        )
    session.task_cache.flush()


def write_run_state(
    context,
    graph: Dict[str, Any],
    results: Dict[str, TaskResult],
    phase: str,
    status: str = "running",
    quality_failures: Optional[Dict[str, Any]] = None,
    final_report=None,
    duration_seconds: Optional[float] = None,
    delivery: Optional[Dict[str, Any]] = None,
) -> None:
    from local_engine.runtime.state import load_state, write_state

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
            "delivery": delivery or state.get("delivery", {}),
        }
    )
    if duration_seconds is not None:
        state["duration_seconds"] = round(float(duration_seconds), 3)
    write_state(context.report_dir, state)
