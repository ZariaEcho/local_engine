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
    context_quality: Optional[Dict] = None,
) -> str:
    task_lines = []
    unresolved_lines = []
    for task in graph["tasks"]:
        result = results[task["id"]]
        status = getattr(result, "status", task_status(result.sip))
        lifecycle = getattr(result, "lifecycle_status", status)
        rounds = getattr(result, "review_rounds", 0)
        review = getattr(result, "review_status", "skipped")
        task_lines.append(
            "| {0} | {1} | {2} | {3} | {4} | {5} | {6} | {7} |".format(
                task["id"], task.get("agent", "-"), task["skill"], task["expected_output"]["type"], status, lifecycle, getattr(result, "cache_action", "execute"), "{0} ({1})".format(review, rounds)
            )
        )
        for issue in getattr(result, "unresolved_issues", []) or []:
            unresolved_lines.append("- `{0}`: {1}".format(task["id"], issue))
    warning_lines = ["- {0}".format(warning) for warning in warnings] or ["- None"]
    patch_lines = ["- {0}".format(path.name) for path in patches] or ["- None"]
    artifact_lines = ["- {0}".format(path.name) for path in artifacts] or ["- None"]
    deliverable_lines = ["- {0}".format(path.name) for path in (deliverables or [])] or ["- None"]
    metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata"), dict) else {}
    classification = metadata.get("classification", {}) if isinstance(metadata.get("classification"), dict) else {}
    context_quality = context_quality if isinstance(context_quality, dict) else {}
    errors = [
        (task_id, result)
        for task_id, result in results.items()
        if getattr(result, "status", "") == "failed_but_continued"
    ]
    review_failures = [
        (task_id, result)
        for task_id, result in results.items()
        if getattr(result, "lifecycle_status", "") == "failed" and not getattr(result, "failed", False)
    ]
    error_lines = [
        "- `{0}`: {1} (see `agent_outputs/{0}.md`)".format(task_id, result.error_message or result.raw or "Claude CLI failed")
        for task_id, result in errors
    ] or ["- None"]
    if error_log is not None:
        error_lines.append("- Full failure evidence: `error.log`")
    error_lines.extend(
        "- `{0}`: review did not pass after {1} round(s)".format(task_id, getattr(result, "review_rounds", 0))
        for task_id, result in review_failures
    )
    return """# local_engine Final Report

## Run ID
{run_id}

## Requirement Summary
{summary}

## Graph Source
{graph_source}

Planner confidence: {planner_confidence}

## Classification
- Intent: {classification_intent}
- Confidence: {classification_confidence}
- Reason: {classification_reason}

## Context Quality
- Coverage: {context_coverage}
- Complete: {context_complete}
- Language confidence: {language_confidence}
- Dependency confidence: {dependency_confidence}
- Evidence: `artifacts/context_quality.json` and `artifacts/context_quality.md`

## Task Graph Overview
{count} tasks; independent tasks were scheduled in parallel where worker capacity allowed.

## Task Status
    | Task | Agent | Skill | Expected Output | Execution | Lifecycle | Cache | Review |
    | --- | --- | --- | --- | --- | --- | --- | --- |
{task_lines}

## Review Summary
- Per-task review evidence is in `reviews/<task_id>.roundN.md`.
- Unresolved issues:
{unresolved_issues}

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
        classification_intent=classification.get("intent", metadata.get("intent", "unknown")),
        classification_confidence=classification.get("confidence", "unknown"),
        classification_reason=classification.get("reason", "No classification evidence was recorded."),
        context_coverage=context_quality.get("coverage", "not recorded"),
        context_complete=("yes" if context_quality.get("complete") is True else "no") if context_quality else "not recorded",
        language_confidence=context_quality.get("language_confidence", "not recorded"),
        dependency_confidence=context_quality.get("dependency_confidence", "not recorded"),
        count=len(graph["tasks"]),
        task_lines="\n".join(task_lines),
        unresolved_issues="\n".join(unresolved_lines) or "- None",
        deliverable_lines="\n".join(deliverable_lines),
        artifact_lines="\n".join(artifact_lines),
        patch_lines="\n".join(patch_lines),
        warning_lines="\n".join(warning_lines),
        errors="\n".join(error_lines),
    )
