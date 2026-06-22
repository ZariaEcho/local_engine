"""Thread-pool scheduler that honours graph dependencies without gating on SIP type."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import yaml

from local_engine.graph.dependency_resolver import ready_tasks
from local_engine.kernel.schemas import TaskResult, WorkerResult, make_error_sip
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
                self._emit(status_callback, "task_started", task)
            with ThreadPoolExecutor(max_workers=min(self.workers, len(ready))) as executor:
                futures = {
                    executor.submit(self._execute, task, prompt_builder, worker_factory, project_root, agent_outputs_dir, state.results): task
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
    ) -> TaskResult:
        dependencies = {dependency: known_results[dependency] for dependency in task.get("depends_on", [])}
        prompt = prompt_builder(task, dependencies)
        try:
            worker_result = worker_factory().run(prompt, task, project_root)
            if not isinstance(worker_result, WorkerResult):
                worker_result = WorkerResult(raw=str(worker_result))
        except Exception as exc:
            worker_result = WorkerResult(raw=str(exc), failed=True, error_message="worker raised an exception")
        raw = worker_result.raw or ""
        sip = make_error_sip(task["skill"], task["id"], raw or worker_result.error_message) if worker_result.failed else parse_sip(raw, task["skill"], task["id"])
        status = "failed_but_continued" if worker_result.failed else (
            "warning" if sip.get("type") in {"error", "parse_error", "unstructured"} else "completed"
        )
        result = TaskResult(
            task["id"], raw, sip, failed=worker_result.failed, status=status, error_message=worker_result.error_message
        )
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
    ) -> None:
        if callback is None:
            return
        payload: Dict[str, Any] = {
            "task_id": task["id"],
            "title": task.get("title", task["id"]),
            "skill": task.get("skill", "unknown"),
        }
        if result is not None:
            payload.update({"status": result.status, "failed": result.failed, "error_message": result.error_message})
        try:
            callback(event, payload)
        except Exception:
            # Terminal rendering must never affect task execution.
            return
