"""Human-readable dynamic-graph final report rendering."""

from pathlib import Path
from typing import Dict, Iterable, Optional

from local_engine.kernel.schemas import TaskResult, task_status


def build_final_report(
    run_id: str,
    graph: Dict,
    results: Dict[str, TaskResult],
    warnings: Iterable[str],
    patches: Iterable[Path],
    artifacts: Iterable[Path],
    deliverables: Optional[Iterable[Path]] = None,
    error_log: Optional[Path] = None,
) -> str:
    task_lines = []
    for task in graph["tasks"]:
        result = results[task["id"]]
        status = getattr(result, "status", task_status(result.sip))
        task_lines.append("| {0} | {1} | {2} | {3} |".format(task["id"], task["skill"], task["expected_output"]["type"], status))
    warning_lines = ["- {0}".format(warning) for warning in warnings] or ["- None"]
    patch_lines = ["- {0}".format(path.name) for path in patches] or ["- None"]
    artifact_lines = ["- {0}".format(path.name) for path in artifacts] or ["- None"]
    deliverable_lines = ["- {0}".format(path.name) for path in (deliverables or [])] or ["- None"]
    metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata"), dict) else {}
    errors = [
        (task_id, result)
        for task_id, result in results.items()
        if getattr(result, "status", "") == "failed_but_continued"
    ]
    error_lines = [
        "- `{0}`: {1} (see `agent_outputs/{0}.md`)".format(task_id, result.error_message or result.raw or "Claude CLI failed")
        for task_id, result in errors
    ] or ["- None"]
    if error_log is not None:
        error_lines.append("- Full failure evidence: `error.log`")
    return """# local_engine Final Report

## Run ID
{run_id}

## Requirement Summary
{summary}

## Graph Source
{graph_source}

Planner confidence: {planner_confidence}

## Task Graph Overview
{count} tasks; independent tasks were scheduled in parallel where worker capacity allowed.

## Task Status
| Task | Skill | Expected Output | Status |
| --- | --- | --- | --- |
{task_lines}

## Deliverables
{deliverable_lines}

## Artifacts
{artifact_lines}

## Patches
{patch_lines}

## Memory Update
- memory_update.md

## Warnings
{warning_lines}

## Errors
{errors}

## Next Steps
- Review `integration_review.md` and the graph-planning evidence in `internal/`.
- Review user-facing items in `deliverables/` before sharing them.
- Validate generated patches against the project test suite before applying them.
- Re-run in apply mode only after approving the collected patch set.
""".format(
        run_id=run_id,
        summary=graph["requirement"]["raw_summary"],
        graph_source=metadata.get("graph_source", "unknown"),
        planner_confidence=metadata.get("planner_confidence", 0.0),
        count=len(graph["tasks"]),
        task_lines="\n".join(task_lines),
        deliverable_lines="\n".join(deliverable_lines),
        artifact_lines="\n".join(artifact_lines),
        patch_lines="\n".join(patch_lines),
        warning_lines="\n".join(warning_lines),
        errors="\n".join(error_lines),
    )
