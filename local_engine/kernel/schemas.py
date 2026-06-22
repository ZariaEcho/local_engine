"""Small, dependency-free internal data contracts."""

from dataclasses import dataclass
from typing import Any, Dict, List


SIP_DEFAULTS = {
    "type": "unstructured",
    "confidence": 0.0,
    "assumptions": [],
    "unknowns": [],
    "risks": [],
    "dependencies": [],
    "artifacts": [],
    "body": "",
}


@dataclass
class WorkerResult:
    """Raw result returned by a worker before SIP normalization."""

    raw: str
    failed: bool = False
    error_message: str = ""


@dataclass
class TaskResult:
    """Persisted worker result used by downstream prompt compilation."""

    task_id: str
    raw: str
    sip: Dict[str, Any]
    failed: bool = False
    status: str = "completed"
    error_message: str = ""


def make_error_sip(skill: str, task_id: str, message: str) -> Dict[str, Any]:
    """Return an error SIP object without ever raising while handling failures."""
    return {
        "type": "error",
        "skill": skill,
        "task_id": task_id,
        "confidence": 0.0,
        "assumptions": [],
        "unknowns": ["Claude CLI subprocess failed"],
        "risks": ["Task execution failed"],
        "dependencies": [],
        "artifacts": [],
        "body": message or "",
    }


def task_status(sip: Dict[str, Any]) -> str:
    """Map a SIP type to a concise report status."""
    value = str(sip.get("type", "unstructured"))
    return "warning" if value in {"error", "parse_error", "unstructured"} else "complete"
