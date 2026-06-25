"""Durable run metadata and report discovery helpers."""

from pathlib import Path
from typing import Any, Dict, Optional

from local_engine.artifacts.recover_report import detect_status, recover_report
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.run_store import iter_run_dirs, resolve_run_dir


def write_run_metadata(global_run_dir: Path, payload: Dict[str, Any]) -> Path:
    """Compatibility adapter that updates the canonical JSON run index."""
    data = dict(payload)
    data.setdefault("run_id", Path(global_run_dir).name)
    return RunIndex(Path(global_run_dir).parent).upsert(data)


def resolve_report(
    latest: bool = False, project_root: Optional[Path] = None, run_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Locate or recover a final report, using global metadata when no project is supplied."""
    if project_root is not None:
        project_state = project_root.expanduser().resolve() / ".local_engine"
        if run_id:
            resolved = resolve_run_dir(project_state, run_id=run_id)
            candidates = [resolved] if resolved is not None else []
        elif latest:
            candidates = list(iter_run_dirs(project_state))
        else:
            return None
        for report_dir in candidates:
            metadata = _ensure_report({"run_id": report_dir.name, "report_dir": str(report_dir)})
            if metadata is not None:
                return metadata
        return None

    if not (latest or run_id):
        return None
    index = RunIndex()
    candidates = [index.get(run_id)] if run_id else [index.latest()]
    for metadata in candidates:
        if not isinstance(metadata, dict):
            continue
        recovered = _ensure_report(metadata, index=index)
        if recovered is not None:
            return recovered
    return None


def _ensure_report(metadata: Dict[str, Any], index: Optional[RunIndex] = None) -> Optional[Dict[str, Any]]:
    run_id = str(metadata.get("run_id", "")).strip()
    final_report = _report_path(metadata)
    if run_id and final_report.is_file():
        value = dict(metadata)
        value["final_report"] = str(final_report)
        value["report_path"] = str(final_report)
        value.setdefault("report_dir", str(final_report.parent))
        return value

    report_dir = _report_dir(metadata, final_report)
    if not run_id or not report_dir.is_dir():
        return None

    final_report = recover_report(report_dir)
    status = detect_status(report_dir)
    if index is not None:
        index.finalize(run_id, status=status, report_path=final_report, report_dir=str(report_dir))
    value = dict(metadata)
    value["status"] = status
    value["final_report"] = str(final_report)
    value["report_path"] = str(final_report)
    value["report_dir"] = str(report_dir)
    return value


def _report_path(metadata: Dict[str, Any]) -> Path:
    value = str(metadata.get("report_path") or metadata.get("final_report") or "").strip()
    if value:
        return Path(value).expanduser()
    report_dir = str(metadata.get("report_dir", "")).strip()
    return Path(report_dir).expanduser() / "final_report.md" if report_dir else Path()


def _report_dir(metadata: Dict[str, Any], final_report: Path) -> Path:
    value = str(metadata.get("report_dir", "")).strip()
    if value:
        return Path(value).expanduser()
    report_path_value = str(metadata.get("report_path") or metadata.get("final_report") or "").strip()
    if report_path_value:
        return final_report.expanduser().parent
    return Path("__missing_local_engine_report_dir__")
