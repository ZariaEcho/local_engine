"""Small, dependency-free internal data contracts."""

from dataclasses import dataclass
from enum import Enum
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
    "warnings": [],
    "failure_type": "",
}


class FailureType(str, Enum):
    NETWORK = "network"
    FORMAT = "format"
    LOGIC = "logic"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class WorkerResult:
    """Raw result returned by a worker before SIP normalization."""

    raw: str
    failed: bool = False
    error_message: str = ""
    failure_type: str = ""


@dataclass
class TaskResult:
    """Persisted worker result used by downstream prompt compilation."""

    task_id: str
    raw: str
    sip: Dict[str, Any]
    failed: bool = False
    status: str = "completed"
    error_message: str = ""
    model: str = ""
    retry_history: List[Dict[str, Any]] = None
    lifecycle_status: str = "pending"
    review_rounds: int = 0
    review_status: str = "skipped"
    unresolved_issues: List[str] = None
    failure_type: str = ""
    warnings: List[str] = None
    quality_score: float = 0.0
    quality_reasons: List[str] = None
    context_patch: str = ""
    cache_action: str = "execute"
    source_run_id: str = ""

    def __post_init__(self) -> None:
        if self.retry_history is None:
            self.retry_history = []
        if self.unresolved_issues is None:
            self.unresolved_issues = []
        if self.warnings is None:
            self.warnings = []
        if self.quality_reasons is None:
            self.quality_reasons = []
        if self.lifecycle_status == "pending":
            self.lifecycle_status = "failed" if self.failed else "completed"


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
        "warnings": [],
        "failure_type": FailureType.UNKNOWN.value,
    }


def task_status(sip: Dict[str, Any]) -> str:
    """Map a SIP type to a concise report status."""
    value = str(sip.get("type", "unstructured"))
    return "warning" if value in {"error", "parse_error", "unstructured"} else "complete"
