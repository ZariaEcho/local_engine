"""Built-in Runtime hook events."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from local_engine.runtime.state import load_state, utc_now, write_state


def emit_optional(callback: Optional[Callable[[str, Dict[str, Any]], None]], event: str, payload: Dict[str, Any]) -> None:
    """Notify a terminal observer without letting UI failures abort the run."""
    if callback is None:
        return
    try:
        callback(event, payload)
    except Exception:
        return


SUPPORTED_EVENTS = {
    "before_run",
    "after_plan",
    "before_task",
    "after_task",
    "on_task_fail",
    "on_quality_fail",
    "before_report",
    "after_report",
}


@dataclass(frozen=True)
class HookEvent:
    event_type: str
    run_id: str
    run_dir: str
    task_id: str = ""
    state: Dict[str, Any] = field(default_factory=dict)
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HookResult:
    continue_run: bool = True
    mutations: Dict[str, Any] = field(default_factory=dict)
    messages: List[str] = field(default_factory=list)


class RuntimeEventRecorder:
    """Record built-in hook events without allowing graph mutation."""

    def __init__(self, run_id: str, run_dir: Path) -> None:
        self.run_id = run_id
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "artifacts" / "hooks.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event_type: str, payload: Optional[Dict[str, Any]] = None, task_id: str = "") -> HookResult:
        if event_type not in SUPPORTED_EVENTS:
            return HookResult(messages=["unsupported hook event: {0}".format(event_type)])
        state = load_state(self.run_dir)
        event = HookEvent(
            event_type=event_type,
            run_id=self.run_id,
            run_dir=str(self.run_dir),
            task_id=task_id,
            state={"status": state.get("status"), "phase": state.get("phase")},
            payload=dict(payload or {}),
        )
        record = {
            "timestamp": utc_now(),
            "event_type": event.event_type,
            "run_id": event.run_id,
            "run_dir": event.run_dir,
            "task_id": event.task_id,
            "state": event.state,
            "payload": event.payload,
            "result": {"continue_run": True, "mutations": {}, "messages": []},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        hooks = list(state.get("hooks", [])) if isinstance(state.get("hooks"), list) else []
        hooks.append(record)
        state["hooks"] = hooks
        write_state(self.run_dir, state)
        return HookResult()

