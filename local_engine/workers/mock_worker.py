"""Deterministic worker for tests and local engine evaluation."""

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

import yaml

from local_engine.kernel.schemas import WorkerResult


Response = Union[str, Callable[[str, Any], str]]


class MockWorker:
    """Return configured raw outputs while retaining prompts for assertions."""

    def __init__(self, responses: Optional[Dict[str, Response]] = None) -> None:
        self.responses = responses or {}
        self.prompts: Dict[str, str] = {}

    def run(self, prompt: str, task: Any, project_root: Path) -> WorkerResult:
        task_id = task["id"]
        self.prompts[task_id] = prompt
        response = self.responses.get(task_id)
        if callable(response):
            return WorkerResult(raw=str(response(prompt, task)))
        if response is not None:
            return WorkerResult(raw=str(response))
        payload = {
            "type": "plan",
            "skill": task["skill"],
            "task_id": task_id,
            "confidence": 0.8,
            "assumptions": [],
            "unknowns": [],
            "risks": [],
            "dependencies": task.get("depends_on", []),
            "artifacts": [],
            "findings": ["Mock finding for {0}".format(task_id)],
            "recommendations": ["Mock recommendation for {0}".format(task_id)],
            "decisions": ["Mock decision for {0}".format(task_id)],
            "body": "Mock output for {0}. This deterministic fixture contains enough detail for output quality evaluation.".format(task_id),
        }
        return WorkerResult(raw=yaml.safe_dump(payload, sort_keys=False))
