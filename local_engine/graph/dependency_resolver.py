"""Dependency helpers for task graph execution."""

from typing import Any, Dict, List


def ready_tasks(graph: Dict[str, Any], completed: Dict[str, Any], scheduled: List[str]) -> List[Dict[str, Any]]:
    """Return tasks whose dependencies are completed and that were not scheduled."""
    return [
        task
        for task in graph["tasks"]
        if task["id"] not in scheduled and all(dep in completed for dep in task.get("depends_on", []))
    ]
