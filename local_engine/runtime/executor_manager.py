"""Executor adapters for the Runtime-centered contract."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Protocol

from local_engine.kernel.schemas import WorkerResult
from local_engine.kernel.sip_parser import parse_sip
from local_engine.runtime.contracts import ExecutorHealth, ExecutorRequest, ExecutorResult
from local_engine.workers.mock_worker import MockWorker


class BaseExecutor(Protocol):
    executor_type: str

    def run(self, request: ExecutorRequest) -> ExecutorResult:
        """Execute exactly one request."""

    def healthcheck(self) -> ExecutorHealth:
        """Return executor readiness."""


def _worker_result_to_executor_result(task_id: str, result: WorkerResult) -> ExecutorResult:
    parsed: Dict[str, Any] = {}
    if not result.failed:
        parsed = parse_sip(result.raw or "", "executor", task_id)
    status = "failed" if result.failed else "completed"
    return ExecutorResult(
        task_id=task_id,
        status=status,
        stdout=result.raw or "",
        stderr=result.error_message,
        raw_output=result.raw or "",
        parsed_output=parsed,
        error=result.error_message,
    )


def _executor_result_to_worker_result(result: ExecutorResult) -> WorkerResult:
    failed = result.status not in {"completed", "ok", "success"}
    raw = result.raw_output or result.stdout or result.stderr or ""
    failure_type = "timeout" if result.status == "timeout" else ""
    return WorkerResult(
        raw=raw,
        failed=failed,
        error_message=result.error if failed else "",
        failure_type=failure_type,
    )


class ShellExecutor:
    executor_type = "shell"
    label = "Executor"

    def __init__(self, command: Iterable[str]) -> None:
        self.command = list(command)

    def run(self, request: ExecutorRequest) -> ExecutorResult:
        try:
            completed = subprocess.run(
                self.command,
                input=request.prompt,
                text=True,
                capture_output=True,
                cwd=str(request.project_root),
                timeout=request.timeout_seconds,
                check=False,
                env={**os.environ, **request.env} if request.env else None,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutorResult(
                request.task_id,
                "timeout",
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                raw_output=str(exc.stderr or exc.stdout or ""),
                error="{0} subprocess timed out".format(self.label),
            )
        except OSError as exc:
            return ExecutorResult(
                request.task_id,
                "failed",
                stderr=str(exc),
                raw_output=str(exc),
                error="{0} subprocess failed".format(self.label),
            )
        status = "completed" if completed.returncode == 0 else "failed"
        raw = completed.stdout or completed.stderr or ""
        return ExecutorResult(
            request.task_id,
            status,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            raw_output=raw,
            error="" if status == "completed" else "{0} exited with code {1}".format(self.label, completed.returncode),
        )

    def healthcheck(self) -> ExecutorHealth:
        return ExecutorHealth(self.executor_type, "ok" if self.command else "error", " ".join(self.command))


class ClaudeExecutor(ShellExecutor):
    executor_type = "claude"
    label = "Claude CLI"

    def __init__(self, command: Optional[Iterable[str]] = None) -> None:
        super().__init__(command or ["claude"])


class PythonExecutor(ShellExecutor):
    executor_type = "python"

    def __init__(self, script: Optional[Path] = None) -> None:
        command = [sys.executable]
        if script is not None:
            command.append(str(script))
        super().__init__(command)


class MockExecutor:
    executor_type = "mock"

    def __init__(self, responses: Optional[Dict[str, Any]] = None) -> None:
        self.worker = MockWorker(responses)

    def run(self, request: ExecutorRequest) -> ExecutorResult:
        task = dict(request.metadata.get("task") or {"id": request.task_id, "skill": "mock"})
        return _worker_result_to_executor_result(request.task_id, self.worker.run(request.prompt, task, request.project_root))

    def healthcheck(self) -> ExecutorHealth:
        return ExecutorHealth(self.executor_type, "ok", "deterministic mock executor")


class ExecutorWorkerAdapter:
    """Compatibility worker whose only execution path is ExecutorManager."""

    def __init__(self, manager: "ExecutorManager", executor_id: str, timeout_seconds: int = 300) -> None:
        self.manager = manager
        self.executor_id = executor_id
        self.timeout_seconds = int(timeout_seconds)

    def run(self, prompt: str, task: Any, project_root: Path) -> WorkerResult:
        task_mapping = dict(task or {})
        task_id = str(task_mapping.get("id") or task_mapping.get("task_id") or "task")
        request = ExecutorRequest(
            task_id=task_id,
            prompt=prompt,
            project_root=project_root,
            run_dir=Path(task_mapping.get("run_dir") or project_root),
            timeout_seconds=self.timeout_seconds,
            metadata={"task": task_mapping},
        )
        return _executor_result_to_worker_result(self.manager.execute(self.executor_id, request))


class ExecutorManager:
    """Create executors from runtime configuration."""

    def __init__(self, config: Dict[str, Any]) -> None:
        self.config = config

    def command_for(self, executor_id: str) -> list[str]:
        if executor_id == "claude" and self.config.get("claude_command") is not None:
            command = self.config.get("claude_command")
            if isinstance(command, str):
                return [command]
            return [str(value) for value in command or ["claude"]]
        executors = self.config.get("executors", {}) if isinstance(self.config.get("executors"), dict) else {}
        definition = executors.get(executor_id, {}) if isinstance(executors.get(executor_id), dict) else {}
        command = definition.get("command")
        if command is None and executor_id == "claude":
            command = self.config.get("claude_command", ["claude"])
        if command is None:
            commands = self.config.get("model_commands", {}) if isinstance(self.config.get("model_commands"), dict) else {}
            command = commands.get(executor_id, [executor_id])
        if isinstance(command, str):
            return [command]
        return [str(value) for value in command or [executor_id]]

    def create(self, executor_id: str) -> BaseExecutor:
        if executor_id == "mock":
            return MockExecutor()
        if executor_id == "python":
            return PythonExecutor()
        command = self.command_for(executor_id)
        if executor_id == "shell":
            return ShellExecutor(command)
        return ClaudeExecutor(command=command)

    def execute(self, executor_id: str, request: ExecutorRequest) -> ExecutorResult:
        """Run one executor request through the configured adapter."""
        return self.create(executor_id).run(request)

    def healthcheck(self, executor_id: str) -> ExecutorHealth:
        return self.create(executor_id).healthcheck()

    def worker_factory(self, default_executor: Optional[str] = None) -> Callable[[str], ExecutorWorkerAdapter]:
        execution = self.config.get("execution", {}) if isinstance(self.config.get("execution"), dict) else {}
        configured_default = default_executor or str(execution.get("default_executor") or "claude")
        timeout_seconds = int(self.config.get("timeout_seconds", 300))

        def factory(model: str = "") -> ExecutorWorkerAdapter:
            return ExecutorWorkerAdapter(self, model or configured_default, timeout_seconds)

        return factory
