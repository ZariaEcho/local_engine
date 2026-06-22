"""Durable run metadata and report discovery helpers."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from local_engine.runtime.config import ensure_engine_home, load_yaml


def write_run_metadata(global_run_dir: Path, payload: Dict[str, Any]) -> Path:
    """Atomically refresh the small global index record for one run."""
    global_run_dir.mkdir(parents=True, exist_ok=True)
    data = dict(payload)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    target = global_run_dir / "run_metadata.yaml"
    temporary = target.with_suffix(".yaml.tmp")
    temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(target)
    return target


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
    runs_dir = ensure_engine_home() / "runs"
    candidates = [runs_dir / run_id / "run_metadata.yaml"] if run_id else sorted(
        runs_dir.glob("*/run_metadata.yaml"), key=lambda path: path.stat().st_mtime, reverse=True
    )
    for metadata_path in candidates:
        metadata = load_yaml(metadata_path)
        final_report = Path(str(metadata.get("final_report", ""))).expanduser()
        if metadata.get("run_id") and final_report.is_file():
            return metadata
    return None
