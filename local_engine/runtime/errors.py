"""Stable per-task error evidence shared by worker and scheduler boundaries."""

import json
from pathlib import Path
from typing import Any, Dict

_STAGES = {"worker", "compiler", "quality", "context"}


def write_error_artifact(
    artifacts_dir: Path,
    task_id: str,
    stage: str,
    error_type: str,
    message: str,
    recoverable: bool,
    **extra: Any,
) -> Path:
    """Persist the standard error shape, allowing additive troubleshooting evidence."""
    normalized_stage = stage if stage in _STAGES else "worker"
    payload: Dict[str, Any] = {
        "task_id": str(task_id),
        "stage": normalized_stage,
        "error_type": str(error_type or "unknown"),
        "message": str(message or "Unknown error"),
        "recoverable": bool(recoverable),
    }
    payload.update(extra)
    target = Path(artifacts_dir) / "errors" / "{0}.json".format(task_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
