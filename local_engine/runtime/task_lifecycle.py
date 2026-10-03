"""Per-task quality, review, revision, and recovery used by the schedule phase."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from local_engine.agents.registry import AgentRegistry
from local_engine.compiler.prompt_compiler import compile_task_prompt
from local_engine.context.context_builder import repo_summary
from local_engine.context.repo_scanner import RepoInfo
from local_engine.kernel.schemas import FailureType, TaskResult, make_error_sip
from local_engine.kernel.sip_parser import parse_sip
from local_engine.runtime.execution_context import RunExecutionContext
from local_engine.runtime.fallback import FallbackPolicy
from local_engine.runtime.quality import QualityEvaluator
from local_engine.runtime.retry import RetryPolicy, invoke_worker, run_with_recovery, write_recovery_artifacts
from local_engine.runtime.run_context import RunContext
from local_engine.runtime.task_summary import build_task_summary
from local_engine.scheduler.parallel_scheduler import ParallelScheduler
from local_engine.skills.registry import SkillRegistry

NEEDS_HUMAN_FAILURES = {
    FailureType.LOGIC.value,
    FailureType.PERMISSION_REQUEST.value,
    FailureType.CLARIFICATION_REQUEST.value,
    FailureType.TOOL_REQUEST.value,
}


def confidence(result: TaskResult) -> float:
    try:
        return max(0.0, min(1.0, float(result.sip.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def quality_triggers(
    task: Dict[str, Any], result: TaskResult, skill: Any, review_config: Dict[str, Any], threshold: float
) -> List[str]:
    triggers: List[str] = []
    if bool(task.get("review_required")) or bool(getattr(skill, "review_required", False)):
        triggers.append("review_required")
    if confidence(result) < threshold:
        triggers.append("low_confidence")
    risky_types = {str(value) for value in review_config.get("risky_task_types", [])}
    if str(task.get("task_type", "")) in risky_types:
        triggers.append("risky_task_type")
    risky_tags = {str(value) for value in review_config.get("risky_tags", ["code_change", "architecture", "security"])}
    if risky_tags.intersection(str(value) for value in task.get("risk_tags", [])):
        triggers.append("risky_change")
    if task.get("expected_output", {}).get("type") == "patch":
        triggers.append("code_change")
    if result.sip.get("warnings"):
        triggers.append("worker_warning")
    return list(dict.fromkeys(triggers))


def write_quality_evidence(
    context: RunContext,
    task: Dict[str, Any],
    result: TaskResult,
    triggers: List[str],
    threshold: float,
    review_required: bool,
) -> str:
    payload = dict(result.output_quality)
    payload.update(
        {
            "task_id": task["id"],
            "failure_type": result.failure_type,
            "triggers": triggers,
            "review_required": review_required,
            "review_status": result.review_status,
            "lifecycle_status": result.lifecycle_status,
            "threshold": threshold,
            "review": {
                "confidence": result.quality_score,
                "triggers": triggers,
                "required": review_required,
                "status": result.review_status,
                "threshold": threshold,
            },
        }
    )
    context.write_text("artifacts/quality/{0}.json".format(task["id"]), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    needs_patch = (
        result.quality_score < threshold
        or bool(result.warnings)
        or result.lifecycle_status in {"failed", "needs_human", "logic_failed"}
        or bool(result.unresolved_issues)
    )
    if not needs_patch:
        return ""
    lines = [
        "# Context Patch: {0}".format(task["id"]),
        "",
        "The upstream result requires independent verification before reuse.",
        "- Confidence: {0:.2f} (threshold {1:.2f})".format(result.quality_score, threshold),
        "- Output quality: {0:.2f}".format(float(result.output_quality.get("quality", 0.0))),
        "- Lifecycle: {0}".format(result.lifecycle_status),
    ]
    if triggers:
        lines.append("- Quality triggers: {0}".format(", ".join(triggers)))
    for warning in result.warnings:
        lines.append("- Warning: {0}".format(warning))
    for issue in result.unresolved_issues:
        lines.append("- Unresolved: {0}".format(issue))
    content = "\n".join(lines) + "\n"
    context.write_text("artifacts/context_patches/{0}.md".format(task["id"]), content)
    return content


def recover(
    worker_factory: Callable[..., Any],
    task: Dict[str, Any],
    prompt: str,
    context: RunContext,
    artifact_id: str,
    agent: Any,
    config: Dict[str, Any],
    execution_context: Optional[RunExecutionContext] = None,
) -> Any:
    execution = config.get("execution", {}) if isinstance(config.get("execution"), dict) else {}
    recovery = run_with_recovery(
        lambda model, call_prompt, timeout=None: invoke_worker(
            worker_factory, model, call_prompt, task, context.project_root, timeout
        ),
        prompt,
        task["id"],
        getattr(agent.model, "primary", "claude"),
        RetryPolicy.from_config(execution, agent),
        FallbackPolicy.from_config(execution, agent),
        skill=task["skill"],
        timeout_seconds=config.get("timeout_seconds", 300),
        apply_approved=bool(execution_context.apply_approved) if execution_context is not None else False,
        execution_contract=execution_context.to_prompt_section() if execution_context is not None else "",
    )
    write_recovery_artifacts(context.artifacts_dir, artifact_id, recovery)
    return recovery


def task_result_from_recovery(task: Dict[str, Any], recovery: Any) -> TaskResult:
    worker_result = recovery.worker_result
    raw = worker_result.raw or ""
    sip = (
        make_error_sip(task["skill"], task["id"], raw or worker_result.error_message)
        if worker_result.failed
        else parse_sip(raw, task["skill"], task["id"])
    )
    status = "failed_but_continued" if worker_result.failed else (
        "warning" if sip.get("type") in {"error", "parse_error", "unstructured"} else "completed"
    )
    return TaskResult(
        task["id"],
        raw,
        sip,
        failed=worker_result.failed,
        status=status,
        error_message=worker_result.error_message,
        model=recovery.model,
        retry_history=[attempt.to_dict() for attempt in recovery.attempts],
        lifecycle_status=(
            "needs_human"
            if (recovery.failure_type or worker_result.failure_type) in NEEDS_HUMAN_FAILURES
            else ("failed" if worker_result.failed else "completed")
        ),
        failure_type=recovery.failure_type or worker_result.failure_type,
        warnings=[str(value) for value in sip.get("warnings", [])],
    )


def review_issues(text: str, sip: Dict[str, Any]) -> List[str]:
    issues = [str(value) for value in sip.get("risks", []) + sip.get("unknowns", []) if str(value).strip()]
    body = str(sip.get("body", "")).strip()
    if body:
        issues.append(body)
    if not issues:
        issues.append(text.strip() or "Reviewer did not provide a specific issue.")
    return issues


def review_verdict(raw: str, sip: Dict[str, Any], failed: bool) -> Tuple[bool, List[str]]:
    if failed or sip.get("type") == "error":
        return False, [str(sip.get("body") or "Review worker failed")]
    text = "{0}\n{1}".format(raw, sip.get("body", ""))
    verdict = re.search(r"\bVERDICT\s*:\s*(PASS|FAIL)\b", text, flags=re.IGNORECASE)
    if verdict and verdict.group(1).upper() == "PASS":
        return True, []
    if verdict and verdict.group(1).upper() == "FAIL":
        return False, review_issues(text, sip)
    return True, []


def write_review(
    context: RunContext, task: Dict[str, Any], round_number: int, status: str, raw: str, issues: List[str]
) -> Path:
    lines = [
        "# Task Review",
        "",
        "- Task: `{0}`".format(task["id"]),
        "- Round: {0}".format(round_number),
        "- Status: {0}".format(status),
        "",
        "## Unresolved Issues",
    ]
    lines.extend("- {0}".format(issue) for issue in issues) if issues else lines.append("- None")
    lines.extend(["", "## Review Output", "", raw or "(no review output)", ""])
    return context.write_text("reviews/{0}.round{1}.md".format(task["id"], round_number), "\n".join(lines))


def revision_prompt(
    task: Dict[str, Any],
    normalized: Dict[str, Any],
    project_context: str,
    project_memory: str,
    engine_memory: str,
    mode: str,
    graph_metadata: Dict[str, Any],
    repo_info: RepoInfo,
    current: TaskResult,
    issues: List[str],
    agent: Any,
    skill: Any,
    context_quality_warnings: Optional[List[str]] = None,
    execution_context: Optional[RunExecutionContext] = None,
) -> str:
    prompt = compile_task_prompt(
        task,
        normalized,
        project_context,
        project_memory,
        engine_memory,
        {task["id"]: current},
        mode,
        graph_metadata,
        repo_summary(repo_info),
        agent=agent,
        skill_definition=skill,
        context_quality_warnings=context_quality_warnings or [],
        execution_context=execution_context,
    )
    return "{0}\n\n# Required Revision\nThe review did not pass. Address every issue below before returning the updated SIP output.\n{1}\n".format(
        prompt, "\n".join("- {0}".format(issue) for issue in issues)
    )


def run_review_loop(
    graph: Dict[str, Any],
    results: Dict[str, TaskResult],
    context: RunContext,
    normalized: Dict[str, Any],
    project_context: str,
    project_memory: str,
    engine_memory: str,
    mode: str,
    repo_info: RepoInfo,
    worker_factory: Callable[..., Any],
    resolved_agents: Dict[str, Any],
    resolved_skills: Dict[str, Any],
    config: Dict[str, Any],
    context_quality_warnings: Optional[List[str]] = None,
    execution_context: Optional[RunExecutionContext] = None,
) -> None:
    """Review executable task outputs and revise them until pass or the configured limit."""
    review_config = config.get("review", {}) if isinstance(config.get("review"), dict) else {}
    enabled = bool(review_config.get("enabled", True))
    max_rounds = review_config.get("max_rounds", 2)
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
        max_rounds = 2
    agents = AgentRegistry.load()
    skills = SkillRegistry.load()
    reviewer_agent = agents.get(str(review_config.get("reviewer_agent", "reviewer")))
    reviewer_skill = skills.get(str(review_config.get("review_skill", "review_code")))
    if reviewer_skill.name not in reviewer_agent.skills:
        raise ValueError("reviewer agent `{0}` is not configured for `{1}`".format(reviewer_agent.name, reviewer_skill.name))

    for task in graph["tasks"]:
        task_id = task["id"]
        current = results[task_id]
        if task["expected_output"]["type"] in {"review", "memory_update"}:
            write_review(context, task, 1, "skipped", "Task type does not enter the execution review loop.", [])
            current.review_status = "skipped"
            continue
        if not enabled:
            write_review(context, task, 1, "skipped", "Review loop is disabled by configuration.", [])
            current.review_status = "skipped"
            continue
        if current.failed:
            issues = ["Task execution failed before review: {0}".format(current.error_message or "worker failure")]
            write_review(context, task, 1, "skipped", "Task could not be reviewed because execution failed.", issues)
            current.review_status = "skipped"
            current.unresolved_issues = issues
            current.lifecycle_status = "failed"
            continue

        for round_number in range(1, max_rounds + 1):
            current.lifecycle_status = "reviewing"
            review_task = {
                "id": task_id,
                "title": "Review {0} (round {1})".format(task["title"], round_number),
                "skill": reviewer_skill.name,
                "agent": reviewer_agent.name,
                "depends_on": [task_id],
                "expected_output": {"type": "review", "path": "reviews/{0}.round{1}.md".format(task_id, round_number)},
                "constraints": {
                    "must": ["Return VERDICT: PASS or VERDICT: FAIL with concrete issues."],
                    "must_not": ["Do not modify the project."],
                },
            }
            review_prompt = compile_task_prompt(
                review_task,
                normalized,
                project_context,
                project_memory,
                engine_memory,
                {task_id: current},
                mode,
                graph.get("metadata", {}),
                repo_summary(repo_info),
                agent=reviewer_agent,
                skill_definition=reviewer_skill,
                context_quality_warnings=context_quality_warnings or [],
                execution_context=execution_context,
            )
            review_recovery = recover(
                worker_factory,
                review_task,
                review_prompt,
                context,
                "{0}.review{1}".format(task_id, round_number),
                reviewer_agent,
                config,
                execution_context,
            )
            review_raw = review_recovery.worker_result.raw or ""
            review_sip = (
                make_error_sip(reviewer_skill.name, task_id, review_raw or review_recovery.worker_result.error_message)
                if review_recovery.worker_result.failed
                else parse_sip(review_raw, reviewer_skill.name, task_id)
            )
            passed, issues = review_verdict(review_raw, review_sip, review_recovery.worker_result.failed)
            write_review(context, task, round_number, "passed" if passed else "failed", review_raw, issues)
            current.review_rounds = round_number
            current.review_status = "passed" if passed else "failed"
            current.unresolved_issues = [] if passed else issues
            if passed:
                current.lifecycle_status = "passed"
                break
            if round_number == max_rounds:
                current.lifecycle_status = "failed"
                break

            current.lifecycle_status = "revising"
            rev_prompt = revision_prompt(
                task,
                normalized,
                project_context,
                project_memory,
                engine_memory,
                mode,
                graph.get("metadata", {}),
                repo_info,
                current,
                issues,
                resolved_agents[task_id],
                resolved_skills[task_id],
                context_quality_warnings,
                execution_context,
            )
            revision_recovery = recover(
                worker_factory,
                task,
                rev_prompt,
                context,
                "{0}.revise{1}".format(task_id, round_number),
                resolved_agents[task_id],
                config,
                execution_context,
            )
            current = task_result_from_recovery(task, revision_recovery)
            current.review_rounds = round_number
            current.review_status = "revising"
            results[task_id] = current
            ParallelScheduler._persist(context.agent_outputs_dir, task, current)


def finalize_task_result(
    task: Dict[str, Any],
    current: TaskResult,
    context: RunContext,
    normalized: Dict[str, Any],
    project_context: str,
    project_memory: str,
    engine_memory: str,
    mode: str,
    repo_info: RepoInfo,
    worker_factory: Callable[..., Any],
    resolved_agents: Dict[str, Any],
    resolved_skills: Dict[str, Any],
    config: Dict[str, Any],
    context_quality_warnings: List[str],
    execution_context: Optional[RunExecutionContext] = None,
) -> TaskResult:
    """Apply quality policy and optional review before releasing dependencies."""
    review_config = config.get("review", {}) if isinstance(config.get("review"), dict) else {}
    loop_config = config.get("loop", {}) if isinstance(config.get("loop"), dict) else {}
    loop_enabled = bool(loop_config.get("enabled", False))
    enabled = bool(review_config.get("enabled", True))
    threshold = review_config.get("threshold", 0.75)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        threshold = 0.75
    threshold = max(0.0, min(1.0, float(threshold)))
    max_rounds = review_config.get("max_rounds", 2)
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
        max_rounds = 2
    if loop_enabled:
        loop_rounds = loop_config.get("max_rounds", max_rounds)
        if not isinstance(loop_rounds, bool) and isinstance(loop_rounds, int) and loop_rounds >= 1:
            max_rounds = loop_rounds
    skill = resolved_skills[task["id"]]
    triggers = quality_triggers(task, current, skill, review_config, threshold)
    current.quality_score = confidence(current)
    current.quality_reasons = list(triggers)
    current.warnings = [str(value) for value in current.sip.get("warnings", [])]

    excluded = task["expected_output"]["type"] in {"review", "memory_update"}
    if current.failed:
        current.review_status = "skipped"
        current.loop_status = "needs_human" if loop_enabled and current.failure_type in NEEDS_HUMAN_FAILURES else "skipped"
        current.lifecycle_status = "needs_human" if current.failure_type in NEEDS_HUMAN_FAILURES else "failed"
        current.unresolved_issues = [current.error_message or "Task execution failed"]
    elif excluded or not enabled or not triggers:
        current.review_status = "skipped"
        current.loop_status = "skipped"
        current.lifecycle_status = "passed"
        write_review(context, task, 1, "skipped", "Review was not required by the quality policy.", [])
    else:
        agents = AgentRegistry.load()
        skills = SkillRegistry.load()
        reviewer_agent = agents.get(str(review_config.get("reviewer_agent", "reviewer")))
        reviewer_skill = skills.get(str(review_config.get("review_skill", "review_code")))
        if reviewer_skill.name not in reviewer_agent.skills:
            raise ValueError(
                "reviewer agent `{0}` is not configured for `{1}`".format(reviewer_agent.name, reviewer_skill.name)
            )
        for round_number in range(1, max_rounds + 1):
            current.lifecycle_status = "reviewing"
            review_task = {
                "id": task["id"],
                "title": "Review {0} (round {1})".format(task["title"], round_number),
                "skill": reviewer_skill.name,
                "agent": reviewer_agent.name,
                "depends_on": [task["id"]],
                "expected_output": {"type": "review", "path": "reviews/{0}.round{1}.md".format(task["id"], round_number)},
                "constraints": {
                    "must": ["Return VERDICT: PASS or VERDICT: FAIL with concrete issues."],
                    "must_not": ["Do not modify the project."],
                },
            }
            review_prompt = compile_task_prompt(
                review_task,
                normalized,
                project_context,
                project_memory,
                engine_memory,
                {task["id"]: current},
                mode,
                {"quality_triggers": triggers},
                repo_summary(repo_info),
                agent=reviewer_agent,
                skill_definition=reviewer_skill,
                context_quality_warnings=context_quality_warnings,
                execution_context=execution_context,
            )
            review_recovery = recover(
                worker_factory,
                review_task,
                review_prompt,
                context,
                "{0}.review{1}".format(task["id"], round_number),
                reviewer_agent,
                config,
                execution_context,
            )
            review_raw = review_recovery.worker_result.raw or ""
            review_sip = (
                make_error_sip(reviewer_skill.name, task["id"], review_raw or review_recovery.worker_result.error_message)
                if review_recovery.worker_result.failed
                else parse_sip(review_raw, reviewer_skill.name, task["id"])
            )
            passed, issues = review_verdict(review_raw, review_sip, review_recovery.worker_result.failed)
            write_review(context, task, round_number, "passed" if passed else "failed", review_raw, issues)
            current.review_rounds = round_number
            current.review_status = "passed" if passed else "failed"
            current.unresolved_issues = [] if passed else issues
            if passed:
                if current.loop_rounds:
                    current.loop_status = "passed"
                current.lifecycle_status = "passed"
                break
            if round_number == max_rounds:
                if loop_enabled:
                    current.loop_status = "needs_human"
                current.lifecycle_status = "failed"
                break
            current.lifecycle_status = "revising"
            if loop_enabled:
                current.loop_rounds += 1
                current.loop_status = "retrying"
                current.loop_history.append(
                    {
                        "round": round_number,
                        "trigger": "review_failed",
                        "action": "generate_fix_task",
                        "issues": list(issues),
                    }
                )
            rev_prompt = revision_prompt(
                task,
                normalized,
                project_context,
                project_memory,
                engine_memory,
                mode,
                {"quality_triggers": triggers},
                repo_info,
                current,
                issues,
                resolved_agents[task["id"]],
                resolved_skills[task["id"]],
                context_quality_warnings,
                execution_context,
            )
            revision_recovery = recover(
                worker_factory,
                task,
                rev_prompt,
                context,
                "{0}.revise{1}".format(task["id"], round_number),
                resolved_agents[task["id"]],
                config,
                execution_context,
            )
            current = task_result_from_recovery(task, revision_recovery)
            current.review_rounds = round_number
            current.review_status = "revising"
            if loop_enabled:
                current.loop_rounds = round_number
                current.loop_status = "retrying"
                current.loop_history.append(
                    {
                        "round": round_number,
                        "trigger": "review_failed",
                        "action": "retry",
                        "failed": current.failed,
                    }
                )
            if current.failed:
                if loop_enabled:
                    current.loop_status = "needs_human" if current.failure_type in NEEDS_HUMAN_FAILURES else "failed"
                current.lifecycle_status = "needs_human" if current.failure_type in NEEDS_HUMAN_FAILURES else "failed"
                break

    current.quality_score = confidence(current)
    output_quality = QualityEvaluator(config.get("quality")).evaluate(task, current)
    current.output_quality = output_quality.to_dict()
    current.warnings = list(
        dict.fromkeys([str(value) for value in current.sip.get("warnings", [])] + output_quality.warnings)
    )
    current.task_summary = build_task_summary(current)
    context.write_yaml("artifacts/task_summaries/{0}.yaml".format(task["id"]), current.task_summary)
    current.context_patch = write_quality_evidence(
        context, task, current, triggers, threshold, enabled and bool(triggers)
    )
    return current
