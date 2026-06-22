"""Create a stable, intentionally modest MVP requirement representation."""

from typing import Any, Dict


def normalize_requirement(loaded: Dict[str, Any]) -> Dict[str, Any]:
    raw = str(loaded["raw"]).strip()
    command = str(loaded.get("task_text") or "").strip()
    first_line = next((line.strip("# ").strip() for line in raw.splitlines() if line.strip()), "Requested work")
    goal = command or first_line
    return {
        "source_type": loaded["source_type"],
        "raw_summary": raw[:800],
        "user_goal": goal,
        "real_goal": goal,
        "success_definition": "Produce a reviewable task-graph run with preserved worker outputs, artifacts, warnings, and next steps.",
        "raw_requirement": raw,
    }
