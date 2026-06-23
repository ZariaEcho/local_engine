"""Durable run metadata and report discovery helpers."""

from pathlib import Path
from typing import Any, Dict, Optional

from local_engine.runtime.run_index import RunIndex


def write_run_metadata(global_run_dir: Path, payload: Dict[str, Any]) -> Path:
    """Compatibility adapter that updates the canonical JSON run index."""
    data = dict(payload)
    data.setdefault("run_id", Path(global_run_dir).name)
    return RunIndex(Path(global_run_dir).parent).upsert(data)


def resolve_report(
    latest: bool = False, project_root: Optional[Path] = None, run_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Locate a completed report, using global metadata when no project is supplied."""
    if project_root is not None:
        reports_dir = project_root.expanduser().resolve() / ".local_engine" / "task_reports"
        if run_id:
            candidates = [reports_dir / run_id]
        elif latest:
            candidates = sorted(
                (path for path in reports_dir.glob("*") if path.is_dir()), key=lambda path: path.stat().st_mtime, reverse=True
            )
        else:
            return None
        for report_dir in candidates:
            final_report = report_dir / "final_report.md"
            if final_report.is_file():
                return {"run_id": report_dir.name, "report_dir": str(report_dir), "final_report": str(final_report)}
        return None

    if not (latest or run_id):
        return None
    index = RunIndex()
    candidates = [index.get(run_id)] if run_id else index.list()
    for metadata in candidates:
        if not isinstance(metadata, dict):
            continue
        final_report = Path(str(metadata.get("report_path", metadata.get("final_report", "")))).expanduser()
        if metadata.get("run_id") and final_report.is_file():
            value = dict(metadata)
            value.setdefault("final_report", str(final_report))
            value.setdefault("report_dir", str(final_report.parent))
            return value
    return None
