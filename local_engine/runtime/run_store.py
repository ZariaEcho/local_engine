"""Project-local Runtime run storage."""

from pathlib import Path
from typing import Dict, Iterable, Optional

from local_engine.artifacts.recover_report import detect_status, recover_report
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.state import initial_state, load_state, write_state


RUN_SUBDIRS = (
    "prompts",
    "agent_outputs",
    "patches",
    "artifacts",
    "artifacts/errors",
    "artifacts/retries",
    "deliverables",
    "reviews",
    "internal",
    "errors",
)


def project_runs_dir(project_state: Path) -> Path:
    return Path(project_state) / "runs"


def legacy_reports_dir(project_state: Path) -> Path:
    return Path(project_state) / "task_reports"


def create_run_dir(project_state: Path, run_id: str, project_root: Path) -> Path:
    run_dir = project_runs_dir(project_state) / run_id
    for relative in ("", *RUN_SUBDIRS):
        (run_dir / relative).mkdir(parents=True, exist_ok=True)
    write_state(run_dir, initial_state(run_id, run_dir, project_root))
    update_latest(project_state, run_id)
    return run_dir


def update_latest(project_state: Path, run_id: str) -> Path:
    latest = Path(project_state) / "latest"
    target = project_runs_dir(project_state) / run_id
    if latest.exists() or latest.is_symlink():
        latest.unlink()
    try:
        latest.symlink_to(target, target_is_directory=True)
    except OSError:
        latest.write_text(run_id + "\n", encoding="utf-8")
    return latest


def latest_run_id(project_state: Path) -> str:
    latest = Path(project_state) / "latest"
    if latest.is_symlink():
        return latest.resolve().name
    try:
        value = latest.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value
    runs = sorted(project_runs_dir(project_state).glob("*"), key=lambda path: path.stat().st_mtime, reverse=True)
    return runs[0].name if runs else ""


def resolve_run_dir(project_state: Path, run_id: Optional[str] = None, latest: bool = False) -> Optional[Path]:
    selected = latest_run_id(project_state) if latest or not run_id else str(run_id)
    if not selected:
        return None
    run_dir = project_runs_dir(project_state) / selected
    if run_dir.is_dir():
        return run_dir
    legacy = legacy_reports_dir(project_state) / selected
    return legacy if legacy.is_dir() else None


def iter_run_dirs(project_state: Path) -> Iterable[Path]:
    for root in (project_runs_dir(project_state), legacy_reports_dir(project_state)):
        if root.is_dir():
            yield from sorted((path for path in root.iterdir() if path.is_dir()), key=lambda path: path.name, reverse=True)


class RunStore:
    """Read and recover project-local runs."""

    def __init__(self, project_state: Path) -> None:
        self.project_state = Path(project_state)

    def status(self, run_id: Optional[str] = None, latest: bool = False) -> Dict[str, object]:
        run_dir = resolve_run_dir(self.project_state, run_id, latest)
        if run_dir is None:
            raise FileNotFoundError("no matching run found")
        state = load_state(run_dir)
        status = str(state.get("status") or detect_status(run_dir))
        tasks = state.get("tasks") if isinstance(state.get("tasks"), dict) else {}
        completed = sum(1 for item in tasks.values() if isinstance(item, dict) and item.get("status") in {"completed", "passed", "skipped"})
        failed = sum(1 for item in tasks.values() if isinstance(item, dict) and item.get("status") in {"failed", "failed_but_continued", "needs_human"})
        return {
            "run_id": run_dir.name,
            "status": status,
            "phase": state.get("phase", ""),
            "run_dir": str(run_dir),
            "task_count": len(tasks),
            "completed_count": completed,
            "failed_count": failed,
            "final_report": str(run_dir / "final_report.md") if (run_dir / "final_report.md").is_file() else "",
        }

    def resume(self, run_id: Optional[str] = None, latest: bool = False) -> Dict[str, object]:
        run_dir = resolve_run_dir(self.project_state, run_id, latest)
        if run_dir is None:
            raise FileNotFoundError("no matching run found")
        final = recover_report(run_dir) if not (run_dir / "final_report.md").is_file() else run_dir / "final_report.md"
        status = detect_status(run_dir)
        state = load_state(run_dir)
        state["status"] = status
        state["phase"] = "resumed"
        state.setdefault("artifacts", {})["final_report"] = "final_report.md"
        write_state(run_dir, state)
        RunIndex().finalize(run_dir.name, status=status, report_path=final, report_dir=str(run_dir))
        return self.status(run_id=run_dir.name)

