"""Worker protocol shared by Claude CLI and mock implementations."""

from pathlib import Path
from typing import Any, Protocol

from local_engine.kernel.schemas import WorkerResult


class Worker(Protocol):
    def run(self, prompt: str, task: Any, project_root: Path) -> WorkerResult:
        """Execute exactly one task and return raw output without SIP parsing."""
