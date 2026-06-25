"""Executor adapters for the Runtime-centered contract."""

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Protocol

from local_engine.kernel.sip_parser import parse_sip
from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.contracts import ExecutorHealth, ExecutorRequest, ExecutorResult
from local_engine.workers.claude_cli_worker import ClaudeCLIWorker
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


class ClaudeExecutor:
    executor_type = "claude"

    def __init__(self, command: Iterable[str] = None, timeout_seconds: int = 300) -> None:
        self.worker = ClaudeCLIWorker(command=command, timeout_seconds=timeout_seconds)
        self.command = list(command or ["claude"])

    def run(self, request: ExecutorRequest) -> ExecutorResult:
        task = dict(request.metadata.get("task") or {"id": request.task_id, "skill": "executor"})
        return _worker_result_to_executor_result(request.task_id, self.worker.run(request.prompt, task, request.project_root))

    def healthcheck(self) -> ExecutorHealth:
        return ExecutorHealth(self.executor_type, "ok" if self.command else "error", " ".join(self.command))


class ShellExecutor:
    executor_type = "shell"

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
                env={**request.env} if request.env else None,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecutorResult(
                request.task_id,
                "timeout",
                stdout=str(exc.stdout or ""),
                stderr=str(exc.stderr or ""),
                raw_output=str(exc.stderr or exc.stdout or ""),
                error="executor timed out",
            )
        except OSError as exc:
            return ExecutorResult(request.task_id, "failed", stderr=str(exc), raw_output=str(exc), error=str(exc))
        status = "completed" if completed.returncode == 0 else "failed"
        raw = completed.stdout or completed.stderr or ""
        return ExecutorResult(
            request.task_id,
            status,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
            raw_output=raw,
            error="" if status == "completed" else "exit code {0}".format(completed.returncode),
        )

    def healthcheck(self) -> ExecutorHealth:
        return ExecutorHealth(self.executor_type, "ok" if self.command else "error", " ".join(self.command))


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

    def create(self, executor_id: str):
        if executor_id == "mock":
            return MockExecutor()
        if executor_id == "python":
            return PythonExecutor()
        command = self.command_for(executor_id)
        if executor_id == "shell":
            return ShellExecutor(command)
        return ClaudeExecutor(command=command, timeout_seconds=int(self.config.get("timeout_seconds", 300)))
