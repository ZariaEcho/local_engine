"""Paths belonging to one safe, report-oriented engine run."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import uuid
from typing import Any, Dict

import yaml

from local_engine.runtime.config import ensure_engine_home


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
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    report_dir = state / "task_reports" / run_id
    global_run_dir = ensure_engine_home() / "runs" / run_id
    for directory in (
        report_dir,
        global_run_dir,
        report_dir / "prompts",
        report_dir / "agent_outputs",
        report_dir / "patches",
        report_dir / "artifacts",
        report_dir / "deliverables",
        report_dir / "internal",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return RunContext(run_id, root, state, report_dir, global_run_dir)
