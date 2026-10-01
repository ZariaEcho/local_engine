"""Small pipeline components used by the Runtime orchestrator."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from local_engine.agents.registry import AgentRegistry
from local_engine.intents.registry import IntentRegistry
from local_engine.kernel.schemas import TaskResult
from local_engine.runtime.task_cache import stable_hash
from local_engine.skills.registry import SkillRegistry
from local_engine.task_templates.registry import TaskTemplateRegistry


@dataclass(frozen=True)
class RunRegistries:
    agents: AgentRegistry
    skills: SkillRegistry
    intents: IntentRegistry
    templates: TaskTemplateRegistry


@dataclass(frozen=True)
class ResultSummary:
    task_statuses: Dict[str, str]
    lifecycle_statuses: Dict[str, str]
    passed_count: int
    failed_count: int


def load_run_registries() -> RunRegistries:
    return RunRegistries(
        agents=AgentRegistry.load(),
        skills=SkillRegistry.load(),
        intents=IntentRegistry.load(),
        templates=TaskTemplateRegistry.load(),
    )


def build_run_metadata(
    run_id: str,
    root: Path,
    normalized: Dict[str, Any],
    report_dir: Path,
    deliverables_dir: Path,
    mode: str,
    graph: Dict[str, Any],
    intent: str,
    classification: Any,
    project_type: Any,
) -> Dict[str, Any]:
    return {
        "run_id": run_id,
        "project": str(root),
        "project_root": str(root),
        "input": normalized["raw_requirement"],
        "report_dir": str(report_dir),
        "report_path": "",
        "deliverables_path": str(deliverables_dir),
        "mode": mode,
        "graph_source": graph.get("metadata", {}).get("graph_source"),
        "intent": intent,
        "classification": classification.to_dict(),
        "project_type": project_type.to_dict(),
        "status": "running",
        "task_count": len(graph["tasks"]),
        "passed_count": 0,
        "failed_count": 0,
    }


def build_definition_hashes(
    graph: Dict[str, Any],
    resolved_skills: Dict[str, Any],
    resolved_agents: Dict[str, Any],
    config: Dict[str, Any],
) -> Dict[str, str]:
    return {
        task["id"]: stable_hash(
            {
                "task": task,
                "skill": resolved_skills[task["id"]].to_dict(),
                "skill_prompt": resolved_skills[task["id"]].prompt_path.read_text(encoding="utf-8"),
                "agent": resolved_agents[task["id"]].to_dict(),
                "execution": config.get("execution", {}),
                "review": config.get("review", {}),
            }
        )
        for task in graph["tasks"]
    }


def summarize_results(results: Dict[str, TaskResult]) -> ResultSummary:
    task_statuses = {task_id: result.status for task_id, result in results.items()}
    lifecycle_statuses = {task_id: result.lifecycle_status for task_id, result in results.items()}
    failed_states = {"failed", "failed_but_continued", "needs_human", "logic_failed"}
    passed_count = sum(
        task_statuses[task_id] not in failed_states and lifecycle_statuses[task_id] not in failed_states
        for task_id in task_statuses
    )
    return ResultSummary(
        task_statuses=task_statuses,
        lifecycle_statuses=lifecycle_statuses,
        passed_count=passed_count,
        failed_count=len(task_statuses) - passed_count,
    )
