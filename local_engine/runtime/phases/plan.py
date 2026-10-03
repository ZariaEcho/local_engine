"""Plan phase: classify, scan, compose and validate a TaskGraph."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from local_engine.agents.registry import AgentRegistry
from local_engine.agents.schema import validate_agent_skill_references
from local_engine.context.context_builder import build_context, write_project_context
from local_engine.context.context_quality import assess_context_quality
from local_engine.context.project_type import detect_project_type
from local_engine.context.repo_scanner import RepoInfo, scan_project
from local_engine.graph.dynamic_builder import build_graph, build_skill_graph
from local_engine.graph.graph_quality import GraphQualityError, graph_quality_check
from local_engine.graph.graph_validator import validate_graph
from local_engine.intake.input_loader import load_input
from local_engine.intake.requirement_normalizer import normalize_requirement
from local_engine.intents.classification import ClarificationRequired, ClassificationResult
from local_engine.intents.registry import IntentRegistry
from local_engine.memory.memory_loader import load_engine_memory, load_project_memory, read_text
from local_engine.runtime.config import load_engine_config, load_preferences, load_yaml
from local_engine.runtime.contracts import RuntimeInput
from local_engine.runtime.events import RuntimeEventRecorder, emit_optional
from local_engine.runtime.execution_context import RunExecutionContext
from local_engine.runtime.executor_manager import ExecutorManager
from local_engine.runtime.pipeline import build_definition_hashes, build_run_metadata, load_run_registries
from local_engine.runtime.reporting import write_run_metadata
from local_engine.runtime.run_context import new_run_context
from local_engine.runtime.session import RunSession
from local_engine.runtime.state import update_state
from local_engine.runtime.task_cache import TaskCache, fingerprint_repository, stable_hash
from local_engine.safety.permission_guard import validate_project_root
from local_engine.skills.registry import SkillRegistry


def classify_requirement(
    intents: IntentRegistry,
    requirement: str,
    intent_override: Optional[str] = None,
    skill: Optional[str] = None,
) -> ClassificationResult:
    """Apply the shared clarification gate to every graph-building path."""
    if skill:
        return ClassificationResult.explicit("SKILL", "explicit reusable skill selection: {0}".format(skill))
    if intent_override:
        definition = intents.get(intent_override)
        return ClassificationResult.explicit(definition.name, "explicit --intent override")
    result = intents.classify_result(requirement)
    if result.needs_clarification:
        raise ClarificationRequired(result)
    return result


def resolve_task_capabilities(
    graph: Dict[str, Any], agents: AgentRegistry, skills: SkillRegistry
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Bind each graph task to definitions loaded from YAML, not role constants."""
    resolved_agents: Dict[str, Any] = {}
    resolved_skills: Dict[str, Any] = {}
    for task in graph["tasks"]:
        skill = skills.get(task["skill"])
        agent_name = task.get("agent") or skill.default_agent
        agent = agents.get(agent_name)
        if task["skill"] not in agent.skills:
            raise ValueError(
                "agent `{0}` is not configured for skill `{1}` (task `{2}`)".format(agent.name, task["skill"], task["id"])
            )
        task["agent"] = agent.name
        resolved_agents[task["id"]] = agent
        resolved_skills[task["id"]] = skill
    return resolved_agents, resolved_skills


def registry_capability_names() -> set[str]:
    return set(AgentRegistry.load().names) | set(SkillRegistry.load().names)


def refresh_repo_scan(project_root: Path) -> RepoInfo:
    """Refresh the repository map and canonical project context without a worker run."""
    root = validate_project_root(project_root)
    repo_info = scan_project(root)
    state = root / ".local_engine"
    context_path = write_project_context(repo_info, state)
    (state / "context.md").write_text(context_path.read_text(encoding="utf-8"), encoding="utf-8")
    return repo_info


def preview_planned_graph(
    project_root: Path, task_text: str = "", intent_override: Optional[str] = None
) -> Dict[str, Any]:
    """Return a validated intent graph without invoking any worker or allocating a run."""
    repo_info = refresh_repo_scan(project_root)
    text = task_text or "Create a project plan"
    normalized = normalize_requirement({"raw": text, "task_text": text, "source_type": "text"})
    project_type = detect_project_type(Path(project_root).expanduser().resolve(), repo_info, normalized)
    intents = IntentRegistry.load()
    from local_engine.task_templates.registry import TaskTemplateRegistry

    templates = TaskTemplateRegistry.load()
    skills = SkillRegistry.load()
    agents = AgentRegistry.load()
    classification = classify_requirement(intents, normalized["raw_requirement"], intent_override=intent_override)
    graph = build_graph(
        classification.intent,
        build_context(repo_info),
        "preview",
        normalized,
        intents,
        templates,
        skills,
        agents,
        project_type=project_type.to_dict(),
    )
    graph.setdefault("metadata", {})["classification"] = classification.to_dict()
    validate_graph(graph, registry_capability_names())
    quality = graph_quality_check(graph, classification.intent, intents, templates)
    graph["metadata"]["graph_quality"] = quality.to_dict()
    if not quality.passed:
        raise GraphQualityError(quality)
    return graph


def plan_run(inp: RuntimeInput) -> RunSession:
    """Classify, scan, compose, and persist a validated graph for one run."""
    session = RunSession(inp=inp, started_at=time.monotonic())
    if inp.mode not in {"plan", "apply"}:
        raise ValueError("mode must be 'plan' or 'apply'")
    session.root = validate_project_root(inp.project_root)
    session.registries = load_run_registries()
    agents = session.registries.agents
    skills = session.registries.skills
    intents = session.registries.intents
    templates = session.registries.templates
    session.loaded = load_input(inp.raw_input, inp.input_file)
    session.normalized = normalize_requirement(session.loaded)
    session.classification = classify_requirement(
        intents,
        session.normalized["raw_requirement"],
        intent_override=inp.intent_override,
        skill=inp.skill,
    )
    session.context = new_run_context(session.root)
    session.hook_recorder = RuntimeEventRecorder(session.context.run_id, session.context.report_dir)
    session.hook_recorder.emit(
        "before_run",
        {"mode": inp.mode, "skill": inp.skill or "", "intent_override": inp.intent_override or ""},
    )
    write_run_metadata(
        session.context.global_run_dir,
        {
            "run_id": session.context.run_id,
            "project": str(session.root),
            "project_root": str(session.root),
            "input": session.normalized["raw_requirement"],
            "report_dir": str(session.context.report_dir),
            "report_path": "",
            "deliverables_path": str(session.context.deliverables_dir),
            "status": "running",
        },
    )
    session.config = load_engine_config()
    update_state(
        session.context.report_dir,
        phase="initializing",
        status="running",
        config={
            "hooks_enabled": bool(session.config.get("hooks", {}).get("enabled", True)),
            "loop_enabled": bool(session.config.get("loop", {}).get("enabled", False)),
        },
    )
    for agent in agents:
        validate_agent_skill_references(agent, skills.names)
    load_preferences()
    project_config = load_yaml(session.context.project_state / "project.yaml")
    session.repo_info = refresh_repo_scan(session.root)
    session.repository_fingerprint = fingerprint_repository(session.root, session.context.project_state / "cache")
    session.project_context = read_text(session.context.project_state / "PROJECT_CONTEXT.md")
    session.context_quality = assess_context_quality(session.root, session.repo_info, session.project_context)
    session.project_type = detect_project_type(session.root, session.repo_info, session.normalized)
    session.execution_context = RunExecutionContext(
        mode=inp.mode,
        apply_approved=inp.apply_approved,
        project_root=session.root,
        allowed_write_root=session.root,
    )
    if project_config:
        session.project_context = "{0}\n\n# Project Config\n{1}".format(
            session.project_context, yaml.safe_dump(project_config, sort_keys=False)
        )
    session.project_memory = load_project_memory(session.context.project_state)
    session.engine_memory = load_engine_memory()

    session.context.write_text("raw_input.md", session.loaded["raw"])
    session.context.write_yaml("normalized_requirement.yaml", session.normalized)
    session.context.write_text(
        "repo_context.json", json.dumps(session.repo_info.to_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    session.project_type_json = session.context.write_text("artifacts/project_type.json", session.project_type.to_json())
    session.context.write_yaml("internal/repo_map.yaml", session.repo_info.to_dict())
    session.context.write_text("internal/PROJECT_CONTEXT.md", session.project_context)
    session.context_quality_json = session.context.write_text(
        "artifacts/context_quality.json",
        json.dumps(session.context_quality.to_dict(), ensure_ascii=False, indent=2) + "\n",
    )
    session.context_quality_markdown = session.context.write_text(
        "artifacts/context_quality.md", session.context_quality.to_markdown()
    )
    session.intent = session.classification.intent
    session.context.write_text("internal/intent.txt", session.intent + "\n")
    session.context.write_yaml("internal/classification.yaml", session.classification.to_dict())

    session.worker_factory = inp.worker_factory or ExecutorManager(session.config).worker_factory()

    if inp.skill:
        selected = skills.get(inp.skill)
        session.graph = build_skill_graph(session.context.run_id, session.normalized, selected.name, selected.default_agent)
        session.intent = "SKILL"
    else:
        session.graph = build_graph(
            session.intent,
            session.project_context,
            session.context.run_id,
            session.normalized,
            intents,
            templates,
            skills,
            agents,
            project_type=session.project_type.to_dict(),
        )
    session.graph.setdefault("metadata", {})["classification"] = session.classification.to_dict()
    session.resolved_agents, session.resolved_skills = resolve_task_capabilities(session.graph, agents, skills)
    validate_graph(session.graph, set(agents.names) | set(skills.names))
    session.graph_quality = graph_quality_check(session.graph, session.intent, intents, templates)
    session.graph["metadata"]["graph_quality"] = session.graph_quality.to_dict()
    session.context.write_yaml("task_graph.yaml", session.graph)
    session.context.write_text(
        "artifacts/graph_quality.json", json.dumps(session.graph_quality.to_dict(), ensure_ascii=False, indent=2) + "\n"
    )
    session.context.write_text("internal/graph_quality.md", session.graph_quality.to_markdown())
    if not session.graph_quality.passed:
        raise GraphQualityError(session.graph_quality)
    session.hook_recorder.emit(
        "after_plan",
        {
            "intent": session.intent,
            "task_count": len(session.graph["tasks"]),
            "graph_quality": session.graph_quality.to_dict(),
        },
    )
    update_state(
        session.context.report_dir,
        phase="planned",
        tasks={
            task["id"]: {
                "status": "pending",
                "lifecycle_status": "pending",
                "skill": task["skill"],
                "agent": task.get("agent", ""),
                "depends_on": task.get("depends_on", []),
            }
            for task in session.graph["tasks"]
        },
        artifacts={"task_graph": "task_graph.yaml", "state": "state.json"},
    )
    session.input_hash = stable_hash(
        {
            "requirement": session.normalized,
            "intent": session.intent,
            "classification": session.classification.to_dict(),
            "project_type": session.project_type.to_dict(),
            "intent_definition": intents.get(session.intent).to_dict() if session.intent != "SKILL" else {},
            "mode": inp.mode,
            "selected_skill": inp.skill or "",
        }
    )
    session.task_cache = TaskCache(session.context.project_state / "cache", session.repository_fingerprint)
    session.definition_hashes = build_definition_hashes(
        session.graph, session.resolved_skills, session.resolved_agents, session.config
    )
    session.run_metadata = build_run_metadata(
        session.context.run_id,
        session.root,
        session.normalized,
        session.context.report_dir,
        session.context.deliverables_dir,
        inp.mode,
        session.graph,
        session.intent,
        session.classification,
        session.project_type,
    )
    write_run_metadata(session.context.global_run_dir, session.run_metadata)
    emit_optional(inp.event_callback, "graph_ready", {"run_id": session.context.run_id, "tasks": session.graph["tasks"]})
    return session
