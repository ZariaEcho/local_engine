"""Recover a readable final report from an existing run directory."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Tuple

import yaml


FAILED_STATES = {"failed", "failed_but_continued", "needs_human", "logic_failed"}
FAILED_TASK_STATUSES = FAILED_STATES | {"error", "timeout", "cancelled", "interrupted", "blocked"}
FAILED_LIFECYCLE_STATUSES = {
    "failed",
    "error",
    "timeout",
    "cancelled",
    "blocked",
    "interrupted",
    "needs_human",
    "logic_failed",
}
SUCCESS_TASK_STATUSES = {"completed", "complete", "passed", "success", "skipped", "warning"}
SUCCESS_LIFECYCLE_STATUSES = {"passed", "completed", "complete", "success", "skipped"}


def recover_report(report_dir: Path) -> Path:
    """
    Recover ``final_report.md`` from artifacts already present in ``report_dir``.

    Recovery is intentionally best-effort: a missing or malformed intermediate
    artifact becomes report evidence instead of preventing report creation.
    """
    root = Path(report_dir).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError("report_dir does not exist: {0}".format(root))

    raw_input = _read_text(root / "raw_input.md")
    normalized, normalized_error = _read_yaml(root / "normalized_requirement.yaml")
    graph, graph_error = _read_yaml(root / "task_graph.yaml")
    task_results, task_results_error = _read_yaml(root / "artifacts" / "task_results.yaml")
    context_quality, context_quality_error = _read_json(root / "artifacts" / "context_quality.json")
    graph_quality, graph_quality_error = _read_json(root / "artifacts" / "graph_quality.json")
    errors = _read_json_dir(root / "artifacts" / "errors")
    quality = _read_json_dir(root / "artifacts" / "quality")
    retries = _read_json_dir(root / "artifacts" / "retries")
    reviews = sorted((root / "reviews").glob("*.md")) if (root / "reviews").is_dir() else []
    outputs = sorted((root / "agent_outputs").glob("*.md")) if (root / "agent_outputs").is_dir() else []
    deliverables = _deliverables(root)
    status = detect_status(root)

    read_warnings = [
        warning
        for warning in [
            normalized_error,
            graph_error,
            task_results_error,
            context_quality_error,
            graph_quality_error,
        ]
        if warning
    ]
    lines = [
        "# Local Engine Final Report",
        "",
        "Status: {0}".format(status),
        "",
        "Recovered at: {0}".format(datetime.now(timezone.utc).isoformat()),
        "",
        "## Input",
        _section_text(raw_input, "No raw input recorded."),
        "",
        "## Intent",
        _intent_section(normalized, graph),
        "",
        "## Task Graph",
        _task_graph_section(graph),
        "",
        "## Completed Outputs",
        _completed_outputs_section(graph, task_results, outputs, errors),
        "",
        "## Failed / Retried Tasks",
        _failed_retried_section(errors, retries),
        "",
        "## Quality Warnings",
        _quality_warnings_section(quality, read_warnings),
        "",
        "## Context Quality",
        _quality_object_section(context_quality, "No context quality artifact found."),
        "",
        "## Graph Quality",
        _quality_object_section(graph_quality, "No graph quality artifact found."),
        "",
        "## Review Summary",
        _review_section(reviews),
        "",
        "## Deliverables",
        _deliverables_section(root, deliverables),
        "",
        "## Debug Paths",
        "- agent_outputs/",
        "- artifacts/errors/",
        "- artifacts/retries/",
        "- prompts/",
        "",
    ]
    final = root / "final_report.md"
    final.write_text("\n".join(lines), encoding="utf-8")
    return final


def detect_status(report_dir: Path) -> str:
    """Infer a terminal run status from the artifacts left in ``report_dir``."""
    root = Path(report_dir).expanduser()
    task_records = _task_records(root)
    error_records = _read_json_dir(root / "artifacts" / "errors")
    output_paths = sorted((root / "agent_outputs").glob("*.md")) if (root / "agent_outputs").is_dir() else []
    graph, _ = _read_yaml(root / "task_graph.yaml")
    graph_task_ids = _graph_task_ids(graph)
    fatal_error = _has_fatal_error(root, error_records)

    if task_records:
        completed = 0
        failed = 0
        for record in task_records:
            if _record_failed(record):
                failed += 1
            elif _record_completed(record):
                completed += 1
        total = len(task_records)
        if total > 0 and completed == total and failed == 0:
            return "completed"
        if total > 0 and failed == total:
            return "failed"
        if completed > 0 or failed > 0:
            return "partial"
        return "running"

    if _has_interrupt_marker(root):
        return "interrupted"

    output_statuses = _output_statuses(output_paths)
    passed_outputs = [task_id for task_id, status in output_statuses.items() if status not in FAILED_STATES]
    failed_outputs = [task_id for task_id, status in output_statuses.items() if status in FAILED_STATES]

    if graph_task_ids and output_statuses and set(graph_task_ids).issubset(set(output_statuses)) and not error_records and not failed_outputs:
        return "completed"
    if passed_outputs and (failed_outputs or error_records or len(output_statuses) < len(graph_task_ids or output_statuses)):
        return "partial"
    if failed_outputs and not passed_outputs:
        return "failed"
    if output_paths:
        return "partial"
    if fatal_error:
        return "failed"
    if (root / "final_report.md").is_file():
        return "completed"
    return "unknown"


def _read_text(path: Path, limit: int = 12000) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _truncate(text.strip(), limit)


def _read_yaml(path: Path) -> Tuple[Dict[str, Any], str]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ""
    except (OSError, yaml.YAMLError) as exc:
        return {}, "Could not read {0}: {1}".format(path.name, exc)
    return (value if isinstance(value, dict) else {}, "") if value is not None else ({}, "")


def _read_json(path: Path) -> Tuple[Dict[str, Any], str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}, ""
    except (OSError, json.JSONDecodeError) as exc:
        return {}, "Could not read {0}: {1}".format(path.name, exc)
    return (value if isinstance(value, dict) else {}, "") if value is not None else ({}, "")


def _read_json_dir(path: Path) -> List[Dict[str, Any]]:
    if not path.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for item in sorted(path.glob("*.json")):
        payload, error = _read_json(item)
        if payload:
            payload.setdefault("_path", str(item))
            records.append(payload)
        elif error:
            records.append({"task_id": item.stem, "error_type": "read_error", "message": error, "_path": str(item)})
    return records


def _section_text(text: str, fallback: str) -> str:
    return text if text else fallback


def _intent_section(normalized: Dict[str, Any], graph: Dict[str, Any]) -> str:
    metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata"), dict) else {}
    classification = metadata.get("classification", {}) if isinstance(metadata.get("classification"), dict) else {}
    intent = classification.get("intent") or metadata.get("intent") or normalized.get("intent")
    lines = []
    if intent:
        lines.append("- Intent: {0}".format(intent))
    for key, label in (
        ("raw_summary", "Summary"),
        ("user_goal", "User goal"),
        ("real_goal", "Real goal"),
        ("success_definition", "Success definition"),
        ("source_type", "Source type"),
    ):
        value = normalized.get(key)
        if value:
            lines.append("- {0}: {1}".format(label, _inline(value)))
    if classification.get("reason"):
        lines.append("- Classification reason: {0}".format(_inline(classification["reason"])))
    return "\n".join(lines) if lines else "No normalized intent recorded."


def _task_graph_section(graph: Dict[str, Any]) -> str:
    tasks = graph.get("tasks") if isinstance(graph.get("tasks"), list) else []
    if not tasks:
        return "No task graph recorded."
    lines = ["{0} task(s) recorded.".format(len(tasks))]
    for task in tasks:
        if not isinstance(task, dict):
            continue
        expected = task.get("expected_output", {}) if isinstance(task.get("expected_output"), dict) else {}
        depends = ", ".join(str(value) for value in task.get("depends_on", []) or []) or "none"
        lines.append(
            "- {0}: {1} via {2}; output `{3}`; depends on {4}".format(
                task.get("id", "unknown"),
                task.get("title", "Untitled task"),
                task.get("skill", "unknown"),
                expected.get("path", "not recorded"),
                depends,
            )
        )
    return "\n".join(lines)


def _completed_outputs_section(
    graph: Dict[str, Any],
    task_results: Dict[str, Any],
    outputs: List[Path],
    errors: List[Dict[str, Any]],
) -> str:
    failed_ids = {str(record.get("task_id") or Path(str(record.get("_path", ""))).stem) for record in errors}
    records = {str(record.get("task_id", "")): record for record in _task_records_from_payload(task_results)}
    output_statuses = _output_statuses(outputs)
    completed = []
    for path in outputs:
        task_id = path.stem
        record = records.get(task_id)
        status = str(record.get("lifecycle_status") or record.get("status") or output_statuses.get(task_id, "completed")) if record else output_statuses.get(task_id, "completed")
        if task_id in failed_ids or status in FAILED_STATES:
            continue
        completed.append((task_id, status, path))
    if not completed:
        return "No completed agent outputs found."
    task_titles = {
        str(task.get("id")): str(task.get("title", ""))
        for task in graph.get("tasks", [])
        if isinstance(task, dict) and task.get("id")
    }
    lines = []
    for task_id, status, path in completed:
        title = task_titles.get(task_id)
        prefix = "- {0}".format(task_id)
        if title:
            prefix += ": {0}".format(title)
        lines.append("{0} ({1})".format(prefix, status))
        excerpt = _agent_output_excerpt(path)
        if excerpt:
            lines.append("  Output excerpt: {0}".format(excerpt))
    return "\n".join(lines)


def _failed_retried_section(errors: List[Dict[str, Any]], retries: List[Dict[str, Any]]) -> str:
    lines = []
    for error in errors:
        task_id = error.get("task_id") or Path(str(error.get("_path", ""))).stem or "run"
        lines.append(
            "- {0}: {1} - {2}".format(
                task_id,
                error.get("error_type") or error.get("final_failure_type") or "error",
                _inline(error.get("message") or error.get("error_message") or "No message recorded."),
            )
        )
    for retry in retries:
        attempts = retry.get("attempts") if isinstance(retry.get("attempts"), list) else []
        failed_attempts = [attempt for attempt in attempts if isinstance(attempt, dict) and attempt.get("failed")]
        if not failed_attempts and retry.get("recovered") is not False:
            continue
        lines.append(
            "- {0}: {1} retry attempt(s); recovered={2}; final_model={3}".format(
                retry.get("task_id", "unknown"),
                len(failed_attempts) or len(attempts),
                retry.get("recovered", "unknown"),
                retry.get("final_model", "unknown"),
            )
        )
    return "\n".join(lines) if lines else "- None"


def _quality_warnings_section(quality: List[Dict[str, Any]], read_warnings: List[str]) -> str:
    lines = ["- {0}".format(warning) for warning in read_warnings]
    for record in quality:
        task_id = record.get("task_id") or Path(str(record.get("_path", ""))).stem or "unknown"
        warnings = [str(value) for value in record.get("warnings", []) if str(value).strip()] if isinstance(record.get("warnings"), list) else []
        triggers = [str(value) for value in record.get("triggers", []) if str(value).strip()] if isinstance(record.get("triggers"), list) else []
        review_status = record.get("review_status")
        lifecycle = record.get("lifecycle_status")
        if warnings:
            lines.extend("- {0}: {1}".format(task_id, warning) for warning in warnings)
        if triggers:
            lines.append("- {0}: quality triggers: {1}".format(task_id, ", ".join(triggers)))
        if lifecycle in FAILED_STATES or review_status == "failed":
            lines.append("- {0}: lifecycle={1}; review={2}".format(task_id, lifecycle or "unknown", review_status or "unknown"))
    return "\n".join(lines) if lines else "- None"


def _quality_object_section(payload: Dict[str, Any], fallback: str) -> str:
    if not payload:
        return fallback
    lines = []
    for key in ("passed", "coverage", "complete", "quality", "language_confidence", "dependency_confidence"):
        if key in payload:
            lines.append("- {0}: {1}".format(key, payload[key]))
    for key in ("warnings", "errors"):
        values = payload.get(key)
        if isinstance(values, list) and values:
            lines.append("- {0}: {1}".format(key, "; ".join(_inline(value) for value in values)))
    if lines:
        return "\n".join(lines)
    return "```json\n{0}\n```".format(json.dumps(payload, ensure_ascii=False, indent=2))


def _review_section(reviews: List[Path]) -> str:
    if not reviews:
        return "No review artifacts recorded."
    lines = []
    for path in reviews:
        text = _read_text(path, 3000)
        status = _match_line(text, r"^- Status:\s*(.+)$") or _match_line(text, r"\bVERDICT\s*:\s*(PASS|FAIL)\b")
        issues = _review_issue_count(text)
        lines.append("- {0}: status={1}; unresolved_issues={2}".format(path.name, status or "unknown", issues))
    return "\n".join(lines)


def _deliverables(root: Path) -> List[Path]:
    directory = root / "deliverables"
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*") if path.is_file())


def _deliverables_section(root: Path, deliverables: List[Path]) -> str:
    if not deliverables:
        return "No deliverables generated."
    return "\n".join("- {0}".format(path.relative_to(root).as_posix()) for path in deliverables)


def _task_records(root: Path) -> List[Dict[str, Any]]:
    payload, _ = _read_yaml(root / "artifacts" / "task_results.yaml")
    return _task_records_from_payload(payload)


def _task_records_from_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [task for task in payload if isinstance(task, dict)]
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    if isinstance(tasks, dict):
        return [task for task in tasks.values() if isinstance(task, dict)]
    return [task for task in tasks if isinstance(task, dict)] if isinstance(tasks, list) else []


def _record_failed(record: Dict[str, Any]) -> bool:
    status = _normalize_status(record.get("status"))
    lifecycle = _normalize_status(record.get("lifecycle_status"))
    return _truthy(record.get("worker_failed")) or status in FAILED_TASK_STATUSES or lifecycle in FAILED_LIFECYCLE_STATUSES


def _record_completed(record: Dict[str, Any]) -> bool:
    status = _normalize_status(record.get("status"))
    lifecycle = _normalize_status(record.get("lifecycle_status"))
    if status in SUCCESS_TASK_STATUSES and (not lifecycle or lifecycle in SUCCESS_LIFECYCLE_STATUSES):
        return True
    if lifecycle in SUCCESS_LIFECYCLE_STATUSES and (not status or status not in FAILED_TASK_STATUSES):
        return True
    return False


def _normalize_status(value: Any) -> str:
    return str(value or "").strip().lower()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _output_statuses(outputs: Iterable[Path]) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    for path in outputs:
        text = _read_text(path, 2000)
        statuses[path.stem] = _match_line(text, r"^- Status:\s*(.+)$") or "completed"
    return statuses


def _graph_task_ids(graph: Dict[str, Any]) -> List[str]:
    tasks = graph.get("tasks") if isinstance(graph, dict) else []
    if not isinstance(tasks, list):
        return []
    return [str(task.get("id")) for task in tasks if isinstance(task, dict) and task.get("id")]


def _has_interrupt_marker(root: Path) -> bool:
    if (root / "artifacts" / "errors" / "interrupted.json").is_file():
        return True
    for record in _read_json_dir(root / "artifacts" / "errors"):
        text = json.dumps(record, ensure_ascii=False).lower()
        if "keyboardinterrupt" in text or "interrupted" in text:
            return True
    return False


def _has_fatal_error(root: Path, errors: List[Dict[str, Any]]) -> bool:
    if (root / "error.log").is_file():
        return True
    for record in errors:
        if record.get("recoverable") is False:
            return True
        text = "{0} {1}".format(record.get("error_type", ""), record.get("message", "")).lower()
        if "fatal" in text or "graphqualityerror" in text:
            return True
    return bool(errors)


def _agent_output_excerpt(path: Path) -> str:
    text = _read_text(path, 4000)
    body = text.split("## Claude Response", 1)[-1] if "## Claude Response" in text else text
    body = re.sub(r"\s+", " ", body).strip()
    return _truncate(body, 220)


def _review_issue_count(text: str) -> int:
    if "## Unresolved Issues" not in text:
        return 0
    section = text.split("## Unresolved Issues", 1)[-1].split("## ", 1)[0]
    items = [line for line in section.splitlines() if line.strip().startswith("- ") and line.strip() != "- None"]
    return len(items)


def _match_line(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _inline(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 22].rstrip() + "\n\n[truncated for recovery]"
