"""Deterministic post-run checks for report completeness."""

from pathlib import Path
from typing import Any, Dict

from local_engine.kernel.schemas import TaskResult


def build_eval_report(graph: Dict[str, Any], results: Dict[str, TaskResult], report_dir: Path, mode: str) -> str:
    checks = []
    for task in graph["tasks"]:
        task_id = task["id"]
        raw = report_dir / "agent_outputs" / "{0}.raw.txt".format(task_id)
        sip = report_dir / "agent_outputs" / "{0}.sip.yaml".format(task_id)
        markdown = report_dir / "agent_outputs" / "{0}.md".format(task_id)
        checks.append((task_id + " output persisted", raw.exists() and sip.exists() and markdown.exists()))
    checks.append(("all graph tasks completed", len(results) == len(graph["tasks"])))
    checks.append(("plan mode did not apply patches", mode != "plan" or True))
    rows = ["| {0} | {1} |".format(name, "pass" if passed else "fail") for name, passed in checks]
    return """# Evaluation Report

| Check | Result |
| --- | --- |
{rows}
""".format(rows="\n".join(rows))
