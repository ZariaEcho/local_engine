"""Compile auditable prompts for arbitrary validated graph tasks."""

from typing import Any, Dict, Optional

import yaml


def _yaml(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()


def _role(agent: Any) -> str:
    if agent is None:
        return "You are a careful delivery specialist."
    name = getattr(agent, "name", "delivery")
    description = getattr(agent, "description", "")
    return "You are the `{0}` agent. {1}".format(name, description).strip()


def _skill_guidance(skill: Any, variables: Dict[str, Any]) -> str:
    if skill is None:
        return "Use disciplined reasoning, state assumptions, and produce only the requested output."
    from local_engine.skills.renderer import SkillRenderer

    return SkillRenderer().render(skill, variables) or "Use disciplined reasoning, state assumptions, and produce only the requested output."


def compile_task_prompt(
    task: Dict[str, Any],
    normalized_requirement: Dict[str, Any],
    project_context: str,
    project_memory: str,
    engine_memory: str,
    dependency_outputs: Dict[str, Any],
    mode: str,
    graph_metadata: Optional[Dict[str, Any]] = None,
    repository_summary: str = "",
    agent: Any = None,
    skill_definition: Any = None,
) -> str:
    """Render the SIP contract with graph, dependency, and task-specific context."""
    warning = any(
        result.sip.get("type") in {"error", "parse_error", "unstructured"} or getattr(result, "status", "") == "failed_but_continued"
        for result in dependency_outputs.values()
    )
    dependencies = "No dependency outputs." if not dependency_outputs else _yaml(
        {task_id: {"status": getattr(result, "status", "completed"), "sip": result.sip, "raw": result.raw} for task_id, result in dependency_outputs.items()}
    )
    context_patches = [
        str(getattr(result, "context_patch", "")).strip()
        for result in dependency_outputs.values()
        if str(getattr(result, "context_patch", "")).strip()
    ]
    constraints = task.get("constraints") if isinstance(task.get("constraints"), dict) else {"must": [], "must_not": []}
    skill_variables = {
        "task_id": task["id"],
        "task_title": task["title"],
        "user_requirement": normalized_requirement["user_goal"],
        "normalized_requirement": normalized_requirement,
        "project_context": project_context,
        "project_memory": project_memory,
        "engine_memory": engine_memory,
        "repository_summary": repository_summary,
        "dependency_outputs": dependencies,
        "mode": mode,
        "expected_output": task["expected_output"],
    }
    sections = [
        "# System Role\nYou are a software architect working within a repository-aware task engine.",
        "# Role\n" + _role(agent),
        "# Skill Guidance\n" + _skill_guidance(skill_definition, skill_variables),
        "# Task\n{0} (`{1}`)".format(task["title"], task["id"]),
        "# User Requirement\n" + normalized_requirement["user_goal"],
        "# Normalized Requirement\n" + _yaml(normalized_requirement),
        "# Graph Metadata\n" + _yaml(graph_metadata or {}),
        "# Repository Summary\n" + (repository_summary or "No repository summary was recorded."),
        "# Project Context\n" + (project_context or "No project context was recorded."),
        "# Project Memory\n" + (project_memory or "No project memory was recorded."),
        "# Engine Memory\n" + (engine_memory or "No engine memory was recorded."),
        "# Dependency Outputs\n" + dependencies,
        "# Upstream Quality Compensation\n" + ("\n\n".join(context_patches) if context_patches else "No upstream context patches."),
        "# Task-specific Constraints\n" + _yaml(constraints),
        "# Constraints\n- Work only within the supplied project context.\n- Mode: {0}. In plan mode, propose changes but do not assume patches will be applied.\n- Do not claim success without noting unknowns and risks.".format(mode),
        "# Permission Rules\n- Do not write outside the project root.\n- Do not delete files, modify `.git/`, run `git push`, use `rm -rf`, or run destructive SQL.\n- Return a patch only as a unified diff in your SIP body.",
        "# Expected Output\n" + _yaml(task["expected_output"]),
        "# Eval Checklist\n- Preserve safety boundaries\n- State assumptions and risks\n- Produce the expected artifact",
        "# Dependency Warning\n" + ("WARNING: one or more upstream outputs were malformed or failed; use their raw content cautiously." if warning else "No upstream output warnings."),
        "# SIP Output Contract\nIMPORTANT OUTPUT CONTRACT:\nPlease return one SIP object if possible.\nPrefer YAML.\nDo not include explanations before or after the SIP object.\nDo not wrap the SIP object in Markdown fences if you can avoid it.\n\nRequired fields:\ntype\nskill\ntask_id\nconfidence\nassumptions\nunknowns\nrisks\nwarnings\ndependencies\nartifacts\nbody\n\nOptional failure field:\nfailure_type\n\nIf you cannot complete the task, still return a valid SIP object with:\ntype: error\n\nNote:\nIf you do not follow this format, local_engine will not crash.\nIt will retry with a strict format contract, preserve useful text, and continue the workflow.",
    ]
    return "\n\n".join(sections) + "\n"
