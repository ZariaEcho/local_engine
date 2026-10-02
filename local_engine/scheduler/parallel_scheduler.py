"""Thread-pool scheduler that honours graph dependencies without gating on SIP type."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import time
from typing import Any, Callable, Dict, Optional

import yaml

from local_engine.graph.dependency_resolver import ready_tasks
from local_engine.kernel.schemas import FailureType, TaskResult, make_error_sip
from local_engine.runtime.fallback import FallbackPolicy
from local_engine.runtime.errors import write_error_artifact
from local_engine.runtime.retry import RetryPolicy, invoke_worker, run_with_recovery, write_recovery_artifacts
from local_engine.kernel.sip_parser import parse_sip
from local_engine.scheduler.execution_state import ExecutionState


class ParallelScheduler:
    def __init__(self, workers: int = 4) -> None:
        self.workers = max(1, int(workers))

    def run(
        self,
        graph: Dict[str, Any],
        prompt_builder: Callable[[Dict[str, Any], Dict[str, TaskResult]], str],
        worker_factory: Callable[[], Any],
        project_root: Path,
        agent_outputs_dir: Path,
        status_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        artifact_dir: Optional[Path] = None,
        agent_resolver: Optional[Callable[[Dict[str, Any]], Any]] = None,
        execution_config: Optional[Dict[str, Any]] = None,
        result_finalizer: Optional[
            Callable[[Dict[str, Any], str, Dict[str, TaskResult], TaskResult], TaskResult]
        ] = None,
        result_loader: Optional[Callable[[Dict[str, Any], Dict[str, TaskResult]], Optional[TaskResult]]] = None,
    ) -> Dict[str, TaskResult]:
        """Run ready tasks in batches; every task result is persisted before release."""
        state = ExecutionState()
        task_index = {task["id"]: task for task in graph["tasks"]}
        state.statuses = {task_id: "pending" for task_id in task_index}
        while len(state.results) < len(graph["tasks"]):
            ready = ready_tasks(graph, state.results, state.scheduled)
            if not ready:
                raise RuntimeError("no runnable task found; task graph may be invalid")
            state.scheduled.extend(task["id"] for task in ready)
            for task in ready:
                state.statuses[task["id"]] = "running"
                agent = agent_resolver(task) if agent_resolver is not None else None
                retry_policy = RetryPolicy.from_config(execution_config or {}, agent)
                self._emit(
                    status_callback,
                    "task_started",
                    task,
                    extra={
                        "task_type": task.get("task_type", ""),
                        "attempt": 1,
                        "max_attempts": max(1, retry_policy.max_retries + 1),
                        "worker": getattr(getattr(agent, "model", None), "primary", "claude"),
                        "started_at": time.time(),
                    },
                )
            with ThreadPoolExecutor(max_workers=min(self.workers, len(ready))) as executor:
                futures = {
                    executor.submit(
                        self._execute,
                        task,
                        prompt_builder,
                        worker_factory,
                        project_root,
                        agent_outputs_dir,
                        state.results,
                        artifact_dir or (agent_outputs_dir.parent / "artifacts"),
                        agent_resolver,
                        execution_config or {},
                        status_callback,
                        result_finalizer,
                        result_loader,
                    ): task
                    for task in ready
                }
                for future in as_completed(futures):
                    task = futures[future]
                    try:
                        state.results[task["id"]] = future.result()
                    except Exception as exc:  # belt-and-braces: one worker must not halt the graph
                        raw = str(exc)
                        sip = make_error_sip(task["skill"], task["id"], raw)
                        state.results[task["id"]] = TaskResult(
                            task["id"], raw, sip, failed=True, status="failed_but_continued", error_message=raw
                        )
                        write_error_artifact(
                            artifact_dir or (agent_outputs_dir.parent / "artifacts"),
                            task["id"],
                            "quality",
                            type(exc).__name__,
                            raw,
                            True,
                        )
                        self._persist(agent_outputs_dir, task, state.results[task["id"]])
                    state.statuses[task["id"]] = state.results[task["id"]].status
                    self._emit(status_callback, "task_finished", task, state.results[task["id"]])
        return {task_id: state.results[task_id] for task_id in task_index}

    def _execute(
        self,
        task: Dict[str, Any],
        prompt_builder: Callable[[Dict[str, Any], Dict[str, TaskResult]], str],
        worker_factory: Callable[[], Any],
        project_root: Path,
        agent_outputs_dir: Path,
        known_results: Dict[str, TaskResult],
        artifact_dir: Path,
        agent_resolver: Optional[Callable[[Dict[str, Any]], Any]],
        execution_config: Dict[str, Any],
        status_callback: Optional[Callable[[str, Dict[str, Any]], None]],
        result_finalizer: Optional[
            Callable[[Dict[str, Any], str, Dict[str, TaskResult], TaskResult], TaskResult]
        ],
        result_loader: Optional[Callable[[Dict[str, Any], Dict[str, TaskResult]], Optional[TaskResult]]],
    ) -> TaskResult:
        dependencies = {dependency: known_results[dependency] for dependency in task.get("depends_on", [])}
        if result_loader is not None:
            cached = result_loader(task, dependencies)
            if cached is not None:
                self._persist(agent_outputs_dir, task, cached)
                return cached
        prompt = prompt_builder(task, dependencies)
        agent = agent_resolver(task) if agent_resolver is not None else None
        retry_policy = RetryPolicy.from_config(execution_config, agent)
        fallback_policy = FallbackPolicy.from_config(execution_config, agent)
        primary_model = getattr(getattr(agent, "model", None), "primary", "claude")
        recovery = run_with_recovery(
            lambda model, call_prompt, timeout=None: invoke_worker(worker_factory, model, call_prompt, task, project_root, timeout),
            prompt,
            task["id"],
            primary_model,
            retry_policy,
            fallback_policy,
            skill=task["skill"],
            timeout_seconds=execution_config.get("timeout_seconds"),
            apply_approved=bool(execution_config.get("apply_approved", False)),
            execution_contract=str(execution_config.get("execution_contract", "")),
        )
        for attempt in recovery.attempts:
            if attempt.failed:
                self._emit_attempt_failure(status_callback, task, attempt)
        write_recovery_artifacts(artifact_dir, task["id"], recovery)
        worker_result = recovery.worker_result
        raw = worker_result.raw or ""
        sip = make_error_sip(task["skill"], task["id"], raw or worker_result.error_message) if worker_result.failed else parse_sip(raw, task["skill"], task["id"])
        status = "failed_but_continued" if worker_result.failed else (
            "warning" if sip.get("type") in {"error", "parse_error", "unstructured"} else "completed"
        )
        result = TaskResult(
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
                if recovery.failure_type in _NEEDS_HUMAN_FAILURES
                else ("failed" if worker_result.failed else "completed")
            ),
            failure_type=recovery.failure_type or worker_result.failure_type,
            warnings=[str(value) for value in sip.get("warnings", [])],
        )
        if result_finalizer is not None:
            result = result_finalizer(task, prompt, dependencies, result)
        self._persist(agent_outputs_dir, task, result)
        return result

    @staticmethod
    def _persist(output_dir: Path, task: Dict[str, Any], result: TaskResult) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        task_id = task["id"]
        (output_dir / "{0}.raw.txt".format(task_id)).write_text(result.raw, encoding="utf-8")
        sip_text = yaml.safe_dump(result.sip, sort_keys=False, allow_unicode=True)
        (output_dir / "{0}.sip.yaml".format(task_id)).write_text(
            sip_text, encoding="utf-8"
        )
        markdown = """# Task Output: {task_id}

- Title: {title}
- Skill: {skill}
- Status: {status}
- Claude CLI failure: {failed}
{error_section}

## Claude Response

{raw}

## Structured Result

````yaml
{sip}
````
""".format(
            task_id=task_id,
            title=task.get("title", task_id),
            skill=task.get("skill", "unknown"),
            status=result.status,
            failed="yes" if result.failed else "no",
            error_section=("- Error: {0}".format(result.error_message) if result.error_message else ""),
            raw=result.raw or "(empty response)",
            sip=sip_text.rstrip(),
        )
        (output_dir / "{0}.md".format(task_id)).write_text(markdown, encoding="utf-8")

    @staticmethod
    def _emit(
        callback: Optional[Callable[[str, Dict[str, Any]], None]],
        event: str,
        task: Dict[str, Any],
        result: Optional[TaskResult] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if callback is None:
            return
        payload: Dict[str, Any] = {
            "task_id": task["id"],
            "title": task.get("title", task["id"]),
            "skill": task.get("skill", "unknown"),
        }
        if result is not None:
            payload.update(
                {
                    "status": result.status,
                    "lifecycle_status": result.lifecycle_status,
                    "cache_action": result.cache_action,
                    "failed": result.failed,
                    "error_message": result.error_message,
                }
            )
            if result.failed:
                payload["error_output"] = result.raw
        if extra:
            payload.update(extra)
        try:
            callback(event, payload)
        except Exception:
            # Terminal rendering must never affect task execution.
            return

    @staticmethod
    def _emit_attempt_failure(callback: Optional[Callable[[str, Dict[str, Any]], None]], task: Dict[str, Any], attempt: Any) -> None:
        if callback is None:
            return
        try:
            callback(
                "task_attempt_failed",
                {
                    "task_id": task["id"],
                    "title": task.get("title", task["id"]),
                    "stage": getattr(attempt, "stage", "attempt"),
                    "attempt": getattr(attempt, "number", 1),
                    "model": getattr(attempt, "model", ""),
                    "worker": getattr(attempt, "model", ""),
                    "error_message": getattr(attempt, "error_message", ""),
                    "error_output": getattr(attempt, "raw", ""),
                    "failure_type": getattr(attempt, "failure_type", ""),
                    "action": getattr(attempt, "action", ""),
                    "lifecycle_status": _attempt_lifecycle(getattr(attempt, "failure_type", "")),
                },
            )
        except Exception:
            return


_NEEDS_HUMAN_FAILURES = {
    FailureType.LOGIC.value,
    FailureType.PERMISSION_REQUEST.value,
    FailureType.CLARIFICATION_REQUEST.value,
    FailureType.TOOL_REQUEST.value,
}


def _attempt_lifecycle(failure_type: str) -> str:
    if failure_type == FailureType.FORMAT.value:
        return "format_retrying"
    if failure_type in _NEEDS_HUMAN_FAILURES:
        return "needs_human"
    return "running"
