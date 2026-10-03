"""Atomic JSON run index for reports produced across all projects."""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml

from local_engine.runtime.config import ensure_engine_home

_RUN_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d{3,})$")
_TERMINAL = {"completed", "completed_with_failures", "partial", "failed", "interrupted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".{0}.{1}.tmp".format(path.name, os.getpid()))
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


class RunIndex:
    """Own the canonical per-run record plus generated index and latest views."""

    def __init__(self, runs_dir: Optional[Path] = None) -> None:
        self.runs_dir = Path(runs_dir or (ensure_engine_home() / "runs")).expanduser().resolve()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.runs_dir / "index.json"
        self.latest_path = self.runs_dir / "latest.json"

    def allocate(self, project: Path) -> str:
        """Reserve the next readable UTC run ID using exclusive file creation."""
        prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        numbers = []
        for path in self.runs_dir.glob("{0}-*.json".format(prefix)):
            match = _RUN_ID.match(path.stem)
            if match:
                numbers.append(int(match.group(2)))
        number = max(numbers or [0]) + 1
        while True:
            run_id = "{0}-{1:03d}".format(prefix, number)
            path = self.record_path(run_id)
            try:
                descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                number += 1
                continue
            record = {
                "run_id": run_id,
                "project": str(Path(project).expanduser().resolve()),
                "input": "",
                "intent": "",
                "status": "running",
                "created_at": _now(),
                "completed_at": None,
                "report_path": "",
                "deliverables_path": "",
                "task_count": 0,
                "passed_count": 0,
                "failed_count": 0,
            }
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(record, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            self._refresh_views()
            return run_id

    def upsert(self, payload: Dict[str, Any]) -> Path:
        run_id = str(payload.get("run_id", "")).strip()
        if not run_id:
            raise ValueError("run index record requires run_id")
        current = _load_json(self.record_path(run_id))
        data = dict(current) if isinstance(current, dict) else {}
        data.update(payload)
        data.setdefault("created_at", _now())
        data["updated_at"] = _now()
        if data.get("status") in _TERMINAL and not data.get("completed_at"):
            data["completed_at"] = data["updated_at"]
        target = self.record_path(run_id)
        _atomic_json(target, data)
        self._refresh_views()
        return target

    def fail(self, run_id: str, error: str = "") -> Path:
        return self.upsert({"run_id": run_id, "status": "failed", "error": error})

    def finalize(
        self,
        run_id: str,
        status: str,
        report_path: Optional[Path] = None,
        completed_at: Optional[str] = None,
        **extra: Any,
    ) -> Path:
        """Mark a run terminal while preserving any existing run metadata."""
        payload: Dict[str, Any] = {"run_id": run_id, "status": status, "completed_at": completed_at or _now()}
        if report_path is not None:
            path = Path(report_path).expanduser().resolve()
            payload["report_path"] = str(path)
            payload["final_report"] = str(path)
            payload.setdefault("report_dir", str(path.parent))
        payload.update(extra)
        return self.upsert(payload)

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        self._import_legacy()
        value = _load_json(self.record_path(run_id))
        return value if isinstance(value, dict) else None

    def list(self) -> List[Dict[str, Any]]:
        self._import_legacy()
        records = list(self._records())
        return sorted(records, key=lambda item: (str(item.get("created_at", "")), str(item.get("run_id", ""))), reverse=True)

    def latest(self) -> Optional[Dict[str, Any]]:
        records = self.list()
        return records[0] if records else None

    def record_path(self, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {"index", "latest"}:
            raise ValueError("invalid run_id")
        return self.runs_dir / "{0}.json".format(run_id)

    def _records(self) -> Iterable[Dict[str, Any]]:
        for path in sorted(self.runs_dir.glob("*.json")):
            if path.name in {"index.json", "latest.json"}:
                continue
            value = _load_json(path)
            if isinstance(value, dict) and value.get("run_id"):
                yield value

    def _refresh_views(self) -> None:
        records = sorted(
            self._records(), key=lambda item: (str(item.get("created_at", "")), str(item.get("run_id", ""))), reverse=True
        )
        _atomic_json(self.index_path, records)
        _atomic_json(self.latest_path, records[0] if records else {})

    def _import_legacy(self) -> None:
        imported = False
        for metadata_path in self.runs_dir.glob("*/run_metadata.yaml"):
            try:
                payload = yaml.safe_load(metadata_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(payload, dict) or not payload.get("run_id"):
                continue
            run_id = str(payload["run_id"])
            if self.record_path(run_id).exists():
                continue
            statuses = payload.get("task_statuses") if isinstance(payload.get("task_statuses"), dict) else {}
            passed = sum(value not in {"failed", "failed_but_continued", "needs_human"} for value in statuses.values())
            failed = len(statuses) - passed
            report_dir = str(payload.get("report_dir", ""))
            record = {
                "run_id": run_id,
                "project": str(payload.get("project_root", payload.get("project", ""))),
                "input": str(payload.get("input", "")),
                "intent": str(payload.get("intent", "")),
                "status": str(payload.get("status", "completed")),
                "created_at": str(payload.get("created_at", payload.get("updated_at", ""))),
                "completed_at": str(payload.get("completed_at") or payload.get("updated_at") or ""),
                "report_path": str(payload.get("final_report", payload.get("report_path", ""))),
                "deliverables_path": str(payload.get("deliverables_path", Path(report_dir) / "deliverables" if report_dir else "")),
                "report_dir": report_dir,
                "task_count": len(statuses),
                "passed_count": passed,
                "failed_count": failed,
                "task_statuses": statuses,
                "legacy_source": str(metadata_path),
            }
            _atomic_json(self.record_path(run_id), record)
            imported = True
        if imported:
            self._refresh_views()
