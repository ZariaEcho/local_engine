"""Compile auditable prompts for arbitrary validated graph tasks."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml


ROLE_BY_SKILL = {
    "product": "You are a product strategist.",
    "backend": "You are a backend engineer.",
    "frontend": "You are a frontend engineer.",
    "tester": "You are a software test engineer.",
    "reviewer": "You are a senior reviewer.",
    "integrator": "You are an integration reviewer.",
    "memory_manager": "You are a technical memory manager.",
    "researcher": "You are a careful research analyst.",
    "writer": "You are a clear, audience-aware writer.",
    "designer": "You are a practical experience designer.",
    "data_analyst": "You are a data analyst.",
}


def _yaml(value: Any) -> str:
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()


def _skill_guidance(skill: str) -> str:
    root = Path(__file__).resolve().parents[2]
    path = root / "templates" / "skills" / "{0}.md".format(skill)
    try:
        content = path.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""
    return content or "Use disciplined reasoning, state assumptions, and produce only the requested output."


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
) -> str:
    """Render the SIP contract with graph, dependency, and task-specific context."""
    warning = any(
        result.sip.get("type") in {"error", "parse_error", "unstructured"} or getattr(result, "status", "") == "failed_but_continued"
        for result in dependency_outputs.values()
    )
    dependencies = "No dependency outputs." if not dependency_outputs else _yaml(
        {task_id: {"status": getattr(result, "status", "completed"), "sip": result.sip, "raw": result.raw} for task_id, result in dependency_outputs.items()}
    )
    constraints = task.get("constraints") if isinstance(task.get("constraints"), dict) else {"must": [], "must_not": []}
    sections = [
        "# System Role\nYou are a software architect working within a repository-aware task engine.",
        "# Role\n" + ROLE_BY_SKILL.get(task["skill"], "You are a careful delivery specialist."),
        "# Skill Guidance\n" + _skill_guidance(str(task.get("skill", ""))),
        "# Task\n{0} (`{1}`)".format(task["title"], task["id"]),
        "# User Requirement\n" + normalized_requirement["user_goal"],
        "# Normalized Requirement\n" + _yaml(normalized_requirement),
        "# Graph Metadata\n" + _yaml(graph_metadata or {}),
        "# Repository Summary\n" + (repository_summary or "No repository summary was recorded."),
        "# Project Context\n" + (project_context or "No project context was recorded."),
        "# Project Memory\n" + (project_memory or "No project memory was recorded."),
        "# Engine Memory\n" + (engine_memory or "No engine memory was recorded."),
        "# Dependency Outputs\n" + dependencies,
        "# Task-specific Constraints\n" + _yaml(constraints),
        "# Constraints\n- Work only within the supplied project context.\n- Mode: {0}. In plan mode, propose changes but do not assume patches will be applied.\n- Do not claim success without noting unknowns and risks.".format(mode),
        "# Permission Rules\n- Do not write outside the project root.\n- Do not delete files, modify `.git/`, run `git push`, use `rm -rf`, or run destructive SQL.\n- Return a patch only as a unified diff in your SIP body.",
        "# Expected Output\n" + _yaml(task["expected_output"]),
        "# Eval Checklist\n- Preserve safety boundaries\n- State assumptions and risks\n- Produce the expected artifact",
        "# Dependency Warning\n" + ("WARNING: one or more upstream outputs were malformed or failed; use their raw content cautiously." if warning else "No upstream output warnings."),
        "# SIP Output Contract\nIMPORTANT OUTPUT CONTRACT:\nPlease return one SIP object if possible.\nPrefer YAML.\nDo not include explanations before or after the SIP object.\nDo not wrap the SIP object in Markdown fences if you can avoid it.\n\nRequired fields:\ntype\nskill\ntask_id\nconfidence\nassumptions\nunknowns\nrisks\ndependencies\nartifacts\nbody\n\nIf you cannot complete the task, still return a valid SIP object with:\ntype: error\n\nNote:\nIf you do not follow this format, local_engine will not crash.\nIt will wrap your response as unstructured output and continue the workflow.",
    ]
    return "\n\n".join(sections) + "\n"
