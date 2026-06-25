"""Runtime and executor contracts used by the staged refactor."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class RuntimeInput:
    project_root: Path
    raw_input: str
    config: Dict[str, Any] = field(default_factory=dict)
    run_id: str = ""
    resume: bool = False


@dataclass(frozen=True)
class RuntimeOutput:
    run_id: str
    status: str
    run_dir: Path
    final_report_path: Optional[Path]
    deliverables_dir: Path
    state: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorRequest:
    task_id: str
    prompt: str
    project_root: Path
    run_dir: Path
    timeout_seconds: int = 300
    env: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutorResult:
    task_id: str
    status: str
    stdout: str = ""
    stderr: str = ""
    raw_output: str = ""
    parsed_output: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class ExecutorHealth:
    executor_type: str
    status: str
    detail: str = ""

