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
    "findings": [],
    "recommendations": [],
    "decisions": [],
    "body": "",
    "warnings": [],
    "failure_type": "",
    "artifact_protocol": "",
}


class FailureType(str, Enum):
    NETWORK = "network"
    FORMAT = "format"
    TIMEOUT = "timeout"
    LOGIC = "logic"
    PERMISSION_REQUEST = "permission_request"
    CLARIFICATION_REQUEST = "clarification_request"
    TOOL_REQUEST = "tool_request"
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
    task_summary: Dict[str, List[str]] = None
    output_quality: Dict[str, Any] = None
    loop_rounds: int = 0
    loop_status: str = "skipped"
    loop_history: List[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if self.retry_history is None:
            self.retry_history = []
        if self.unresolved_issues is None:
            self.unresolved_issues = []
        if self.warnings is None:
            self.warnings = []
        if self.quality_reasons is None:
            self.quality_reasons = []
        if self.task_summary is None:
            self.task_summary = {}
        if self.output_quality is None:
            self.output_quality = {}
        if self.loop_history is None:
            self.loop_history = []
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
        "findings": [],
        "recommendations": [],
        "decisions": [],
        "body": message or "",
        "warnings": [],
        "failure_type": FailureType.UNKNOWN.value,
        "artifact_protocol": "",
    }


def task_status(sip: Dict[str, Any]) -> str:
    """Map a SIP type to a concise report status."""
    value = str(sip.get("type", "unstructured"))
    return "warning" if value in {"error", "parse_error", "unstructured"} else "complete"
