"""Generate a graph-aware integration review from normalized worker results."""

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from local_engine.integrator.conflict_checker import check_conflicts
from local_engine.kernel.schemas import TaskResult


def build_integration_review(
    results: Dict[str, TaskResult], patches: Iterable[Path], graph: Optional[Dict[str, Any]] = None
) -> Tuple[str, List[str]]:
    patch_list = list(patches)
    warnings = check_conflicts(results, patch_list)
    tasks = graph.get("tasks", []) if graph else []
    task_index = {task["id"]: task for task in tasks}
    outputs = defaultdict(list)
    paths = defaultdict(list)
    for task_id, task in task_index.items():
        expected = task.get("expected_output", {})
        output_type = str(expected.get("type", "unknown"))
        output_path = str(expected.get("path", ""))
        outputs[output_type].append("{0}: {1}".format(task_id, output_path))
        paths[output_path].append(task_id)
        result = results.get(task_id)
        if result is None or not str(result.sip.get("body", "")).strip():
            warnings.append("Missing expected output from `{0}` ({1}).".format(task_id, output_path or output_type))
        if output_type == "patch" and not any(path.stem == Path(output_path).stem for path in patch_list):
            warnings.append("Patch task `{0}` did not produce a unified diff.".format(task_id))
    for output_path, task_ids in paths.items():
        if output_path and len(task_ids) > 1:
            warnings.append("Multiple tasks declare the same output path `{0}`: {1}.".format(output_path, ", ".join(sorted(task_ids))))

    status_counts = Counter(getattr(result, "status", "completed") for result in results.values())
    rows = [
        "| {0} | {1} | {2} | {3} |".format(
            task_id,
            task_index.get(task_id, {}).get("expected_output", {}).get("type", "unknown"),
            getattr(result, "status", "completed"),
            result.sip.get("confidence", 0.0),
        )
        for task_id, result in results.items()
    ]
    output_rows = ["- **{0}**: {1}".format(output_type, "; ".join(entries)) for output_type, entries in sorted(outputs.items())]
    warning_text = "\n".join("- {0}".format(item) for item in warnings) or "- No structural conflicts detected."
    report = """# Integration Review

## Task Outputs
| Task | Expected Type | Status | Confidence |
| --- | --- | --- | --- |
{rows}

## Declared Outputs by Type
{outputs}

## Patch Review
{patches}

## Task State Counts
{states}

## Warnings and Conflicts
{warnings}

## Recommendation
Review all warnings before applying patches. Absence of a warning is not proof that generated code is safe.
""".format(
        rows="\n".join(rows),
        outputs="\n".join(output_rows) or "- No declared task outputs.",
        patches="\n".join("- {0}".format(path.name) for path in patch_list) or "- No patches were extracted.",
        states="\n".join("- {0}: {1}".format(status, count) for status, count in sorted(status_counts.items())) or "- No task states.",
        warnings=warning_text,
    )
    return report, warnings
