"""Finalize phase: apply artifacts, write reports, and close the run index."""

from __future__ import annotations

from pathlib import Path
import json
import re
import time
from typing import Any, Dict, List, Optional

from local_engine.artifacts.artifact_store import ArtifactStore
from local_engine.artifacts.patch_collector import collect_patches
from local_engine.artifacts.recover_report import detect_status
from local_engine.artifacts.report_builder import build_final_report, render_delivery_section
from local_engine.eval.eval_runner import build_eval_report
from local_engine.integrator.integrator import build_integration_review
from local_engine.intents.classification import ClassificationResult
from local_engine.kernel.schemas import TaskResult
from local_engine.memory.memory_writer import write_memory_update
from local_engine.runtime.artifact_applier import ArtifactApplier, ApplyResult, DeliveryStatus, VerificationStatus
from local_engine.runtime.outcome import RunOutcome
from local_engine.runtime.events import emit_optional
from local_engine.runtime.pipeline import summarize_results
from local_engine.runtime.reporting import write_run_metadata
from local_engine.runtime.run_context import RunContext
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.run_store import resolve_run_dir
from local_engine.runtime.session import RunSession
from local_engine.runtime.phases.schedule import write_run_state
from local_engine.runtime.state import load_state, write_state
from local_engine.runtime.telemetry import write_telemetry_event
from local_engine.safety.permission_guard import validate_project_root


def task_graph_status(task_statuses: Dict[str, str], lifecycle_statuses: Dict[str, str], warnings: List[str]) -> str:
    failed_states = {"failed", "failed_but_continued", "needs_human", "logic_failed"}
    if task_statuses and all(
        task_statuses[task_id] in failed_states or lifecycle_statuses.get(task_id) in failed_states
        for task_id in task_statuses
    ):
        return "failed"
    if any(status in failed_states for status in task_statuses.values()) or any(
        status in failed_states for status in lifecycle_statuses.values()
    ):
        return "partial"
    return "completed_with_warnings" if warnings else "completed"


def user_goal_satisfied(result: ApplyResult, failed_count: int) -> bool:
    if failed_count > 0:
        return False
    if result.requires_apply:
        if result.delivery_status not in {DeliveryStatus.APPLIED.value, DeliveryStatus.VERIFIED.value}:
            return False
        return result.verification_status not in {VerificationStatus.FAILED.value, VerificationStatus.ERROR.value}
    return result.delivery_status not in {DeliveryStatus.FAILED.value, DeliveryStatus.NEEDS_HUMAN.value}


def run_status_with_delivery(task_status: str, result: ApplyResult) -> str:
    if result.delivery_status == DeliveryStatus.FAILED.value:
        return "partial" if task_status == "completed" else task_status
    if result.requires_apply and result.delivery_status in {
        DeliveryStatus.ARTIFACTS_GENERATED.value,
        DeliveryStatus.NEEDS_HUMAN.value,
    }:
        return "partial" if task_status in {"completed", "unknown"} else task_status
    return task_status


def upsert_delivery_section(final_report: Path, delivery: Dict[str, Any]) -> None:
    if not final_report.is_file():
        return
    section = render_delivery_section(delivery)
    text = final_report.read_text(encoding="utf-8")
    pattern = re.compile(r"\n## Delivery Status\n.*?(?=\n## |\Z)", re.DOTALL)
    if pattern.search(text):
        updated = pattern.sub("\n" + section, text)
    else:
        updated = text.rstrip() + "\n\n" + section + "\n"
    final_report.write_text(updated, encoding="utf-8")


def collect_run_warnings(
    graph: Dict[str, Any],
    graph_quality: Any,
    context_quality: Any,
    classification: ClassificationResult,
    results: Dict[str, TaskResult],
    integration_warnings: List[str],
) -> List[str]:
    metadata = graph.get("metadata", {}) if isinstance(graph.get("metadata"), dict) else {}
    warnings: List[str] = [str(value) for value in metadata.get("warnings", [])]
    warnings.extend("graph quality: {0}".format(value) for value in getattr(graph_quality, "warnings", []))
    warnings.extend("context quality: {0}".format(value) for value in context_quality.warnings)
    if classification.confidence < 0.6:
        warnings.append("classification low confidence: {0:.2f}".format(classification.confidence))
    warnings.extend(str(value) for value in integration_warnings)
    for task_id, result in results.items():
        warnings.extend("{0}: {1}".format(task_id, value) for value in result.warnings)
        warnings.extend(
            "{0}: {1}".format(task_id, value)
            for value in result.output_quality.get("warnings", [])
            if str(value).strip()
        )
        for attempt in result.retry_history:
            if attempt.get("failed"):
                warnings.append(
                    "{0}: retry warning ({1}): {2}".format(
                        task_id,
                        attempt.get("failure_type") or attempt.get("stage") or "unknown",
                        attempt.get("error_message") or "worker/output failure",
                    )
                )
        if result.cache_action == "reuse" or result.lifecycle_status == "skipped":
            warnings.append(
                "{0}: skipped task; reused verified cache{1}.".format(
                    task_id, " from {0}".format(result.source_run_id) if result.source_run_id else ""
                )
            )
        if result.failed or result.lifecycle_status in {"failed", "needs_human", "logic_failed"}:
            warnings.append("{0}: failed task: {1}".format(task_id, result.error_message or result.lifecycle_status))
    return list(dict.fromkeys(warnings))


def write_task_outputs(graph: Dict[str, Any], results: Dict[str, Any], context: RunContext) -> tuple:
    artifacts: List[Path] = []
    deliverables: List[Path] = []
    for task in graph["tasks"]:
        expected = task["expected_output"]
        if expected["type"] in {"patch", "memory_update"} or expected["path"] == "integration_review.md":
            continue
        content = "# {0}\n\n{1}\n".format(task["title"], results[task["id"]].sip.get("body", ""))
        path = context.write_text(expected["path"], content)
        if expected["path"].startswith("deliverables/"):
            deliverables.append(path)
        else:
            artifacts.append(path)
    return artifacts, deliverables


def write_review_summary(graph: Dict[str, Any], results: Dict[str, TaskResult], context: RunContext) -> Path:
    payload = {
        "run_id": context.run_id,
        "tasks": [
            {
                "task_id": task["id"],
                "execution_status": results[task["id"]].status,
                "lifecycle_status": results[task["id"]].lifecycle_status,
                "review_rounds": results[task["id"]].review_rounds,
                "review_status": results[task["id"]].review_status,
                "unresolved_issues": results[task["id"]].unresolved_issues,
            }
            for task in graph["tasks"]
        ],
    }
    return context.write_text("artifacts/review_summary.json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def write_execution_artifacts(graph: Dict[str, Any], results: Dict[str, Any], context: RunContext) -> List[Path]:
    task_index = {task["id"]: task for task in graph["tasks"]}
    task_records = []
    for task_id, result in results.items():
        task = task_index[task_id]
        task_records.append(
            {
                "task_id": task_id,
                "skill": task["skill"],
                "expected_output": task["expected_output"],
                "status": result.status,
                "lifecycle_status": result.lifecycle_status,
                "worker_failed": result.failed,
                "error_message": result.error_message,
                "agent": task.get("agent"),
                "model": result.model,
                "retry_history": result.retry_history,
                "review_rounds": result.review_rounds,
                "review_status": result.review_status,
                "loop_rounds": result.loop_rounds,
                "loop_status": result.loop_status,
                "loop_history": result.loop_history,
                "unresolved_issues": result.unresolved_issues,
                "failure_type": result.failure_type,
                "warnings": result.warnings,
                "quality_score": result.quality_score,
                "quality_reasons": result.quality_reasons,
                "output_quality": result.output_quality,
                "task_summary": result.task_summary,
                "cache_action": result.cache_action,
                "source_run_id": result.source_run_id,
                "sip_type": result.sip.get("type"),
                "prompt": "prompts/{0}.prompt.md".format(task_id),
                "task_output": "agent_outputs/{0}.md".format(task_id),
                "raw_output": "agent_outputs/{0}.raw.txt".format(task_id),
                "structured_output": "agent_outputs/{0}.sip.yaml".format(task_id),
            }
        )
    manifest = {
        "run_id": context.run_id,
        "graph_source": graph.get("metadata", {}).get("graph_source", "unknown"),
        "intent": graph.get("metadata", {}).get("intent", "unknown"),
        "task_count": len(task_records),
        "tasks": task_records,
    }
    return [
        context.write_yaml("artifacts/task_results.yaml", manifest),
        context.write_yaml(
            "artifacts/execution_manifest.yaml",
            {
                "run_id": context.run_id,
                "task_graph": "task_graph.yaml",
                "task_results": "artifacts/task_results.yaml",
                "deliverables_dir": "deliverables/",
                "agent_outputs_dir": "agent_outputs/",
            },
        ),
    ]


def write_error_log(results: Dict[str, Any], context: RunContext) -> Optional[Path]:
    failures = [(task_id, result) for task_id, result in results.items() if result.failed]
    if not failures:
        return None
    sections = ["# local_engine Execution Errors", "", "This run completed its graph, but one or more Claude CLI calls failed.", ""]
    for task_id, result in failures:
        sections.extend(
            [
                "## {0}".format(task_id),
                "- Status: {0}".format(result.status),
                "- Error: {0}".format(result.error_message or "Claude CLI failed"),
                "- Output: `agent_outputs/{0}.md`".format(task_id),
                "",
                "### Captured Error Output",
                "",
                result.raw or "(empty response)",
                "",
            ]
        )
    return context.write_text("error.log", "\n".join(sections))


def build_delivery_summary(run_id: str, graph: Dict[str, Any], results: Dict[str, Any], deliverables: List[Path]) -> str:
    declared = [task for task in graph["tasks"] if task["expected_output"]["path"].startswith("deliverables/")]
    lines = ["# Final Delivery", "", "Run ID: `{0}`".format(run_id), "", "## Included Deliverables"]
    lines.extend("- `{0}`".format(path.name) for path in deliverables if path.name != "FINAL_DELIVERY.md")
    if len(lines) == 5:
        lines.append("- No task-declared document; review the run report and generated patches.")
    lines.extend(["", "## Declared Deliverable Status"])
    if declared:
        lines.extend(
            "- `{0}` — {1}".format(task["expected_output"]["path"], results[task["id"]].status) for task in declared
        )
    else:
        lines.append("- No task declared a standalone deliverable.")
    lines.extend(
        [
            "",
            "## Review Notes",
            "- See `../final_report.md` for full task state, warnings, and failures.",
            "- See `../agent_outputs/` for the complete Claude response from every task.",
        ]
    )
    return "\n".join(lines) + "\n"


def artifact_category(intent: str, expected_path: str, output_type: str) -> str:
    parts = Path(expected_path).parts
    if len(parts) > 1 and parts[0] == "artifacts" and parts[1] in {"audit", "plan", "review", "test", "docs"}:
        return parts[1]
    return {
        "AUDIT": "audit",
        "PLAN": "plan",
        "LEARN": "plan",
        "BUILD": "plan",
        "TEST": "test",
        "DOCUMENT": "docs",
        "REFACTOR": "review",
        "RESEARCH": "review",
    }.get(intent, "docs")


def store_durable_artifacts(graph: Dict[str, Any], results: Dict[str, Any], project_root: Path, intent: str) -> None:
    store = ArtifactStore(project_root)
    for task in graph["tasks"]:
        expected = task["expected_output"]
        if expected["type"] in {"patch", "memory_update"} or expected["path"] == "integration_review.md":
            continue
        content = "# {0}\n\n{1}\n".format(task["title"], results[task["id"]].sip.get("body", ""))
        store.save_artifact(artifact_category(intent, expected["path"], expected["type"]), content, Path(expected["path"]).name)


def finalize_run(session: RunSession) -> RunOutcome:
    inp = session.inp
    context = session.context
    review_summary = write_review_summary(session.graph, session.results, context)
    session.patch_paths = collect_patches(session.results, context.patches_dir, session.graph)
    session.artifact_paths, session.deliverable_paths = write_task_outputs(session.graph, session.results, context)
    session.artifact_paths.extend(
        [session.context_quality_json, session.context_quality_markdown, session.project_type_json]
    )
    session.artifact_paths.append(review_summary)
    session.artifact_paths.extend(write_execution_artifacts(session.graph, session.results, context))
    store_durable_artifacts(session.graph, session.results, session.root, session.intent)
    integration_review, integration_warnings = build_integration_review(session.results, session.patch_paths, session.graph)
    session.warnings = collect_run_warnings(
        session.graph,
        session.graph_quality,
        session.context_quality,
        session.classification,
        session.results,
        integration_warnings,
    )
    context.write_text("integration_review.md", integration_review)
    context.write_text("eval_report.md", build_eval_report(session.graph, session.results, context.report_dir, inp.mode))
    memory_update = write_memory_update(context.project_state, context.run_id, session.warnings)
    context.write_text("memory_update.md", memory_update)
    session.error_log = write_error_log(session.results, context)
    delivery = context.write_text(
        "deliverables/FINAL_DELIVERY.md",
        build_delivery_summary(context.run_id, session.graph, session.results, session.deliverable_paths),
    )
    session.deliverable_paths.append(delivery)

    artifact_applier = ArtifactApplier(session.root, context.report_dir)
    session.apply_result = artifact_applier.apply(
        approved=inp.apply_approved,
        mode=inp.mode,
        patch_paths=session.patch_paths,
        verify=True,
    )
    if session.apply_result.manifest_path:
        session.artifact_paths.append(Path(session.apply_result.manifest_path))
    session.warnings.extend(session.apply_result.warnings)
    session.warnings.extend("delivery: {0}".format(error) for error in session.apply_result.errors)

    result_summary = summarize_results(session.results)
    task_statuses = result_summary.task_statuses
    lifecycle_statuses = result_summary.lifecycle_statuses
    failed_count = result_summary.failed_count
    session.task_graph_status = task_graph_status(task_statuses, lifecycle_statuses, session.warnings)
    session.apply_result.user_goal_satisfied = user_goal_satisfied(session.apply_result, result_summary.failed_count)
    artifact_applier.write_manifest(session.apply_result)
    session.delivery_status = session.apply_result.to_dict(
        task_graph_status=session.task_graph_status,
        user_goal_satisfied=session.apply_result.user_goal_satisfied,
        apply_manifest="apply_manifest.json" if session.apply_result.manifest_path else "",
    )

    session.hook_recorder.emit(
        "before_report", {"warnings": session.warnings, "deliverable_count": len(session.deliverable_paths)}
    )
    session.final_report = context.write_text(
        "final_report.md",
        build_final_report(
            context.run_id,
            session.graph,
            session.results,
            session.warnings,
            session.patch_paths,
            session.artifact_paths,
            session.deliverable_paths,
            error_log=session.error_log,
            context_quality=session.context_quality.to_dict(),
            delivery=session.delivery_status,
        ),
    )
    session.hook_recorder.emit("after_report", {"final_report": str(session.final_report)})
    session.run_status = run_status_with_delivery(detect_status(context.report_dir), session.apply_result)
    write_run_state(
        context,
        session.graph,
        session.results,
        "finished",
        status=session.run_status,
        quality_failures=session.quality_failures,
        final_report=session.final_report,
        duration_seconds=time.monotonic() - session.started_at,
        delivery=session.delivery_status,
    )
    session.run_metadata.update(
        {
            "status": session.run_status,
            "final_report": str(session.final_report),
            "report_path": str(session.final_report),
            "error_log": str(session.error_log) if session.error_log else None,
            "task_statuses": task_statuses,
            "lifecycle_statuses": lifecycle_statuses,
            "task_graph_status": session.task_graph_status,
            "delivery": session.delivery_status,
            "passed_count": result_summary.passed_count,
            "failed_count": result_summary.failed_count,
        }
    )
    write_run_metadata(context.global_run_dir, session.run_metadata)
    emit_optional(
        inp.event_callback,
        "run_finished",
        {
            "run_id": context.run_id,
            "task_statuses": task_statuses,
            "has_failures": failed_count > 0,
        },
    )
    write_telemetry_event(
        context.project_state,
        session.config,
        {
            "command_type": "run",
            "run_status": session.run_status,
            "delivery_status": session.apply_result.delivery_status,
            "task_count": len(task_statuses),
            "success_count": result_summary.passed_count,
            "failure_count": result_summary.failed_count,
            "duration_seconds": round(time.monotonic() - session.started_at, 3),
            "executor_type": session.config.get("execution", {}).get("default_executor", "claude")
            if isinstance(session.config.get("execution"), dict)
            else "claude",
            "error_type": "task_failure" if failed_count else "",
        },
    )
    return RunOutcome(
        context.run_id,
        context.report_dir,
        session.final_report,
        session.warnings,
        task_statuses,
        lifecycle_statuses=lifecycle_statuses,
        task_graph_status=session.task_graph_status,
        delivery_status=session.apply_result.delivery_status,
        verification_status=session.apply_result.verification_status,
        files_created=session.apply_result.files_created,
        files_modified=session.apply_result.files_modified,
        files_not_applied=session.apply_result.files_not_applied,
        user_goal_satisfied=session.apply_result.user_goal_satisfied,
        error_log=session.error_log,
    )


def apply_existing_run(
    project_root: Path,
    run_id: str,
    apply_approved: bool = False,
    verify: bool = True,
) -> ApplyResult:
    """Apply generated artifacts from an existing project-local run."""
    root = validate_project_root(project_root)
    run_dir = resolve_run_dir(root / ".local_engine", run_id=run_id, latest=False)
    if run_dir is None:
        raise FileNotFoundError("no matching run found: {0}".format(run_id))
    applier = ArtifactApplier(root, run_dir)
    result = applier.apply(approved=apply_approved, mode="apply", verify=verify)
    result.user_goal_satisfied = user_goal_satisfied(result, failed_count=0)
    applier.write_manifest(result)
    delivery = result.to_dict(
        task_graph_status=detect_status(run_dir),
        user_goal_satisfied=result.user_goal_satisfied,
        apply_manifest="apply_manifest.json" if result.manifest_path else "",
    )
    state = load_state(run_dir)
    state["delivery"] = delivery
    state["phase"] = "applied" if result.applied_to_project else state.get("phase", "finished")
    state["status"] = run_status_with_delivery(detect_status(run_dir), result)
    state.setdefault("artifacts", {})["apply_manifest"] = "apply_manifest.json"
    write_state(run_dir, state)
    upsert_delivery_section(run_dir / "final_report.md", delivery)
    RunIndex().finalize(
        run_dir.name,
        status=state["status"],
        report_path=run_dir / "final_report.md" if (run_dir / "final_report.md").is_file() else None,
        report_dir=str(run_dir),
        delivery=delivery,
    )
    return result
