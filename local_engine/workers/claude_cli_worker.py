"""Isolated local Claude CLI worker."""

import subprocess
from pathlib import Path
from typing import Any, Iterable

from local_engine.kernel.schemas import WorkerResult


class ClaudeCLIWorker:
    """Run one Claude CLI subprocess per graph task."""

    def __init__(self, command: Iterable[str] = None, timeout_seconds: int = 300) -> None:
        self.command = list(command or ["claude"])
        self.timeout_seconds = int(timeout_seconds)

    def run(self, prompt: str, task: Any, project_root: Path) -> WorkerResult:
        try:
            completed = subprocess.run(
                self.command,
                input=prompt,
                text=True,
                capture_output=True,
                cwd=str(project_root),
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stderr = exc.stderr or "Claude CLI timed out after {0} seconds".format(self.timeout_seconds)
            return WorkerResult(raw=str(stderr), failed=True, error_message="Claude CLI subprocess timed out")
        except OSError as exc:
            return WorkerResult(raw=str(exc), failed=True, error_message="Claude CLI subprocess failed")
        if completed.returncode != 0:
            raw = completed.stderr or completed.stdout or "Claude CLI exited with code {0}".format(completed.returncode)
            return WorkerResult(raw=raw, failed=True, error_message="Claude CLI exited with code {0}".format(completed.returncode))
        return WorkerResult(raw=completed.stdout or "")
