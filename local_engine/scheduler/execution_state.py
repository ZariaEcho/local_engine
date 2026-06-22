"""Simple, inspectable scheduler state."""

from dataclasses import dataclass, field
from typing import Dict, List

from local_engine.kernel.schemas import TaskResult


@dataclass
class ExecutionState:
    scheduled: List[str] = field(default_factory=list)
    results: Dict[str, TaskResult] = field(default_factory=dict)
    statuses: Dict[str, str] = field(default_factory=dict)
