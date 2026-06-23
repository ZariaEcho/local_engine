"""Build task graphs by composing declarative intent, template, skill, and agent registries."""

from typing import Any, Dict, Optional

from local_engine.agents.registry import AgentRegistry
from local_engine.intents.registry import IntentRegistry
from local_engine.skills.registry import SkillRegistry
from local_engine.task_templates.registry import TaskTemplateRegistry


def build_graph(
    intent: str,
    context: Any,
    run_id: str = "preview",
    requirement: Optional[Dict[str, Any]] = None,
    intents: Optional[IntentRegistry] = None,
    templates: Optional[TaskTemplateRegistry] = None,
    skills: Optional[SkillRegistry] = None,
    agents: Optional[AgentRegistry] = None,
) -> Dict[str, Any]:
    """Resolve an intent's task requirements into a concrete executable DAG."""
    intents = intents or IntentRegistry.load()
    templates = templates or TaskTemplateRegistry.load()
    skills = skills or SkillRegistry.load()
    agents = agents or AgentRegistry.load()
    definition = intents.get(intent or intents.default_name)
    selected_names = list(definition.required_tasks) + list(definition.optional_tasks)
    selected = [templates.get(name) for name in selected_names]
    task_ids = {template.name: template.task_id for template in selected}
    if len(set(task_ids.values())) != len(task_ids):
        raise ValueError("intent `{0}` resolves duplicate task IDs".format(definition.name))

    tasks = []
    for template in selected:
        candidates = skills.for_task_type(template.task_type)
        if not candidates:
            raise ValueError("task template `{0}` has no skill for task_type `{1}`".format(template.name, template.task_type))
        selected_skill = None
        selected_agent = None
        for candidate in candidates:
            try:
                agent = agents.get(candidate.default_agent)
            except KeyError:
                continue
            if candidate.name in agent.skills:
                selected_skill, selected_agent = candidate, agent
                break
        if selected_skill is None or selected_agent is None:
            raise ValueError("task template `{0}` has no skill/agent binding".format(template.name))
        dependency_ids = []
        for dependency in template.depends_on:
            if dependency == "*":
                dependency_ids.extend(task["id"] for task in tasks)
            elif dependency in task_ids:
                dependency_ids.append(task_ids[dependency])
            else:
                raise ValueError("task template `{0}` depends on unavailable template `{1}`".format(template.name, dependency))
        task = template.to_task(selected_skill.name, selected_agent.name, list(dict.fromkeys(dependency_ids)))
        task["template"] = template.name
        tasks.append(task)

    requirement = requirement or _default_requirement(definition.name, context)
    return {
        "run_id": run_id,
        "requirement": {
            "source_type": requirement.get("source_type", "text"),
            "raw_summary": requirement.get("raw_summary", "Context Engine graph preview"),
        },
        "goal": {
            "user_goal": requirement.get("user_goal", "Build a {0} graph".format(definition.name)),
            "real_goal": requirement.get("real_goal", requirement.get("user_goal", "Build a {0} graph".format(definition.name))),
            "success_definition": requirement.get("success_definition", "Produce a reviewable Context Engine result."),
        },
        "tasks": tasks,
        "eval": {"checklist": ["Repository context was injected.", "Expected outputs were persisted.", "Warnings were recorded."]},
        "metadata": {
            "graph_source": "dynamic",
            "registry_composed": True,
            "intent": definition.name,
            "context_available": bool(context) if definition.context_sensitive else False,
            "warnings": [],
            "planner_confidence": 1.0,
            "registry_sources": {
                "intents": str(intents.source_dir or ""),
                "task_templates": str(templates.source_dir or ""),
                "skills": str(skills.source_dir or ""),
                "agents": str(agents.source_dir or ""),
            },
        },
    }


def build_skill_graph(run_id: str, requirement: Dict[str, Any], skill: str, agent: str) -> Dict[str, Any]:
    """Build the smallest compatible graph for a directly selected reusable skill."""
    selected = SkillRegistry.load().get(skill)
    task_id = skill.replace("-", "_")
    tasks = [
        {
            "id": task_id,
            "title": "Run skill: {0}".format(skill),
            "task_type": selected.task_type,
            "skill": skill,
            "agent": agent,
            "depends_on": [],
            "can_parallel": True,
            "expected_output": {"type": "report", "path": "deliverables/{0}.md".format(skill.upper())},
            "risk_tags": [],
            "review_required": selected.review_required,
            "constraints": {"must": ["Use the selected reusable skill."], "must_not": ["Do not modify files outside the project root."]},
        },
        {
            "id": "integration_review",
            "title": "Review selected skill delivery",
            "task_type": "integration_review",
            "skill": "integrator",
            "agent": "integrator",
            "depends_on": [task_id],
            "can_parallel": False,
            "expected_output": {"type": "review", "path": "integration_review.md"},
            "risk_tags": [],
            "review_required": False,
            "constraints": {"must": ["Summarize the selected skill output."], "must_not": []},
        },
        {
            "id": "memory_update",
            "title": "Record selected skill context",
            "task_type": "memory_update",
            "skill": "memory_manager",
            "agent": "memory_manager",
            "depends_on": ["integration_review"],
            "can_parallel": False,
            "expected_output": {"type": "memory_update", "path": "memory_update.md"},
            "risk_tags": [],
            "review_required": False,
            "constraints": {"must": ["Record durable decisions and follow-up risks."], "must_not": []},
        },
    ]
    return {
        "run_id": run_id,
        "requirement": {"source_type": requirement["source_type"], "raw_summary": requirement["raw_summary"]},
        "goal": {"user_goal": requirement["user_goal"], "real_goal": requirement["real_goal"], "success_definition": requirement["success_definition"]},
        "tasks": tasks,
        "eval": {"checklist": ["Selected skill was rendered and executed.", "Review evidence was persisted."]},
        "metadata": {"graph_source": "selected_skill", "intent": "SKILL", "selected_skill": skill, "warnings": [], "planner_confidence": 1.0},
    }


def _default_requirement(intent: str, context: Any) -> Dict[str, str]:
    return {
        "source_type": "text",
        "raw_summary": "Context Engine {0} graph".format(intent),
        "user_goal": "Create a {0} result using repository context.".format(intent),
        "real_goal": "Create a reviewable {0} result.".format(intent),
        "success_definition": "The graph executes with project context.",
    }
