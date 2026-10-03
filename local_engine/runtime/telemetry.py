"""Default-disabled local telemetry writer."""

import json
import platform
from pathlib import Path
from typing import Any, Dict

from local_engine.__version__ import __version__
from local_engine.runtime.state import utc_now

ALLOWED_FIELDS = {
    "timestamp",
    "local_engine_version",
    "os_type",
    "python_version",
    "command_type",
    "run_status",
    "task_count",
    "success_count",
    "failure_count",
    "duration_seconds",
    "executor_type",
    "error_type",
}


def telemetry_enabled(config: Dict[str, Any]) -> bool:
    telemetry = config.get("telemetry", {}) if isinstance(config.get("telemetry"), dict) else {}
    return bool(telemetry.get("enabled", False))


def write_telemetry_event(project_state: Path, config: Dict[str, Any], event: Dict[str, Any]) -> Path:
    """Write one sanitized local-only telemetry event when explicitly enabled."""
    if not telemetry_enabled(config):
        return Path()
    telemetry = config.get("telemetry", {}) if isinstance(config.get("telemetry"), dict) else {}
    if telemetry.get("mode", "local_only") != "local_only":
        return Path()
    payload = {
        "timestamp": utc_now(),
        "local_engine_version": __version__,
        "os_type": platform.system(),
        "python_version": platform.python_version(),
    }
    payload.update({key: value for key, value in event.items() if key in ALLOWED_FIELDS})
    payload = {key: value for key, value in payload.items() if key in ALLOWED_FIELDS}
    target = Path(project_state) / "telemetry" / "events.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return target

