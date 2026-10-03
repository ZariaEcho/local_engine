"""Extract optional unified-diff blocks without treating their absence as failure."""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from local_engine.kernel.schemas import TaskResult

_DIFF_BLOCK = re.compile(r"```(?:diff|patch)\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)


def extract_diffs(body: str) -> List[str]:
    return [block.strip() + "\n" for block in _DIFF_BLOCK.findall(body or "") if block.strip()]


def collect_patches(results: Dict[str, TaskResult], patches_dir: Path, graph: Optional[Dict[str, Any]] = None) -> List[Path]:
    """Persist diff blocks from dynamically-declared patch tasks only."""
    patches_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    task_index = {task["id"]: task for task in graph.get("tasks", [])} if graph else {}
    for task_id, result in results.items():
        task = task_index.get(task_id)
        if task and task.get("expected_output", {}).get("type") != "patch":
            continue
        diffs = extract_diffs(str(result.sip.get("body", "")))
        if not diffs:
            continue
        expected_path = str(task.get("expected_output", {}).get("path", "")) if task else ""
        relative = Path(expected_path).relative_to("patches") if expected_path.startswith("patches/") else Path("{0}.patch".format(task_id))
        path = patches_dir / relative
        if path.exists():
            path = patches_dir / "{0}.patch".format(task_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(diffs), encoding="utf-8")
        paths.append(path)
    return paths
