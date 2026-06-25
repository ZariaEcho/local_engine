"""Paths belonging to one safe, report-oriented engine run."""

from dataclasses import dataclass
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Dict

import yaml

from local_engine.runtime.config import ensure_engine_home
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.run_store import create_run_dir


_ACTIVE_RUN_ID: ContextVar[str] = ContextVar("local_engine_active_run_id", default="")


@dataclass
class RunContext:
    run_id: str
    project_root: Path
    project_state: Path
    report_dir: Path
    global_run_dir: Path

    @property
    def prompts_dir(self) -> Path:
        return self.report_dir / "prompts"

    @property
    def agent_outputs_dir(self) -> Path:
        return self.report_dir / "agent_outputs"

    @property
    def patches_dir(self) -> Path:
        return self.report_dir / "patches"

    @property
    def artifacts_dir(self) -> Path:
        return self.report_dir / "artifacts"

    @property
    def reviews_dir(self) -> Path:
        return self.report_dir / "reviews"

    @property
    def deliverables_dir(self) -> Path:
        return self.report_dir / "deliverables"

    @property
    def internal_dir(self) -> Path:
        return self.report_dir / "internal"

    def write_text(self, relative_path: str, content: str) -> Path:
        target = (self.report_dir / relative_path).resolve()
        report_root = self.report_dir.resolve()
        if target != report_root and report_root not in target.parents:
            raise ValueError("run reports cannot write outside their report directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def write_yaml(self, relative_path: str, payload: Dict[str, Any]) -> Path:
        return self.write_text(relative_path, yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))


def new_run_context(project_root: Path) -> RunContext:
    """Create the exact per-run report and global cache directories."""
    root = project_root.expanduser().resolve()
    state = root / ".local_engine"
    if not state.is_dir():
        raise FileNotFoundError("project is not initialized; run `local-engine init --project <path>` first")
    run_id = RunIndex().allocate(root)
    _ACTIVE_RUN_ID.set(run_id)
    report_dir = create_run_dir(state, run_id, root)
    global_run_dir = ensure_engine_home() / "runs" / run_id
    return RunContext(run_id, root, state, report_dir, global_run_dir)


def active_run_id() -> str:
    return _ACTIVE_RUN_ID.get()


def clear_active_run() -> None:
    _ACTIVE_RUN_ID.set("")
