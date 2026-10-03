"""Persisted Runtime state helpers."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initial_state(run_id: str, run_dir: Path, project_root: Path) -> Dict[str, Any]:
    now = utc_now()
    return {
        "run_id": run_id,
        "status": "running",
        "phase": "created",
        "run_dir": str(run_dir),
        "project_root": str(project_root),
        "created_at": now,
        "updated_at": now,
        "tasks": {},
        "hooks": [],
        "loops": {},
        "artifacts": {},
        "warnings": [],
    }


def load_state(run_dir: Path) -> Dict[str, Any]:
    path = Path(run_dir) / "state.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_state(run_dir: Path, state: Dict[str, Any]) -> Path:
    state = dict(state)
    state["updated_at"] = utc_now()
    path = Path(run_dir) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def update_state(run_dir: Path, *, phase: Optional[str] = None, status: Optional[str] = None, **updates: Any) -> Dict[str, Any]:
    state = load_state(run_dir)
    if not state:
        state = initial_state(Path(run_dir).name, Path(run_dir), Path(""))
    if phase is not None:
        state["phase"] = phase
    if status is not None:
        state["status"] = status
    state.update(updates)
    write_state(run_dir, state)
    return state

