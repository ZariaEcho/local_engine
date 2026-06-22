"""Append durable, small run summaries to approved memory locations."""

from pathlib import Path
from typing import Iterable

from local_engine.runtime.config import ensure_engine_home


def write_memory_update(project_state: Path, run_id: str, warnings: Iterable[str]) -> str:
    warning_list = list(warnings)
    summary = "\n## Run {0}\n- Warnings: {1}\n".format(run_id, "; ".join(warning_list) if warning_list else "none")
    project_memory = project_state / "memory.md"
    with project_memory.open("a", encoding="utf-8") as handle:
        handle.write(summary)
    global_memory = ensure_engine_home() / "memory"
    with (global_memory / "experience_memory.md").open("a", encoding="utf-8") as handle:
        handle.write(summary)
    with (global_memory / "decision_memory.md").open("a", encoding="utf-8") as handle:
        handle.write("\n- Run {0}: preserve SIP warnings for follow-up.\n".format(run_id))
    return summary.strip() + "\n"
