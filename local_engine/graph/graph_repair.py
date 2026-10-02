"""Deterministic repair of incomplete Graph Planner candidates."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set


@dataclass
class GraphRepairResult:
    graph: Optional[Dict[str, Any]]
    warnings: List[str]
    report: str


_OUTPUT_BY_SKILL = {
    "backend": ("patch", "patches/backend.patch"),
    "frontend": ("patch", "patches/frontend.patch"),
    "integrator": ("review", "integration_review.md"),
    "memory_manager": ("memory_update", "memory_update.md"),
    "researcher": ("research", "artifacts/research.md"),
    "writer": ("doc", "deliverables/draft.md"),
    "designer": ("design", "deliverables/design.md"),
    "data_analyst": ("analysis", "artifacts/analysis.md"),
}


def repair_graph(
    candidate: Any, run_id: str, requirement: Dict[str, Any], allowed_skills: Optional[Iterable[str]] = None
) -> GraphRepairResult:
    """Repair only predictable omissions; return ``None`` when fallback is safer."""
    warnings: List[str] = []
    if not isinstance(candidate, dict):
        return _result(None, ["Planner candidate is not a mapping and cannot be repaired."])
    raw_tasks = candidate.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        return _result(None, ["Planner candidate has no task list and cannot be repaired safely."])

    graph = deepcopy(candidate)
    if not graph.get("run_id"):
        graph["run_id"] = run_id
        warnings.append("Added missing run_id.")
    if not isinstance(graph.get("requirement"), dict):
        graph["requirement"] = _requirement_payload(requirement)
        warnings.append("Added requirement from normalized input.")
    if not isinstance(graph.get("goal"), dict):
        graph["goal"] = _goal_payload(requirement)
        warnings.append("Added goal from normalized input.")
    if not isinstance(graph.get("eval"), dict):
        graph["eval"] = {"checklist": []}
        warnings.append("Added an empty evaluation checklist.")
    elif not isinstance(graph["eval"].get("checklist"), list):
        graph["eval"]["checklist"] = _list(graph["eval"].get("checklist"))
        warnings.append("Normalized evaluation checklist.")

    tasks: List[Dict[str, Any]] = []
    used_ids: Set[str] = set()
    for index, raw_task in enumerate(raw_tasks, 1):
        if not isinstance(raw_task, dict):
            warnings.append("Dropped non-mapping task at position {0}.".format(index))
            continue
        task = deepcopy(raw_task)
        task_id = str(task.get("id") or "task_{0}".format(index)).strip()
        original_id = task_id
        suffix = 2
        while not task_id or task_id in used_ids:
            task_id = "{0}_{1}".format(original_id or "task", suffix)
            suffix += 1
        if task_id != raw_task.get("id"):
            warnings.append("Assigned unique task id `{0}`.".format(task_id))
        used_ids.add(task_id)
        task["id"] = task_id
        if not str(task.get("title") or "").strip():
            task["title"] = task_id.replace("_", " ").title()
            warnings.append("Added title for `{0}`.".format(task_id))

        skill = str(task.get("skill") or "").strip()
        if skill not in _allowed_skills(allowed_skills):
            skill = "writer" if _looks_like_content(requirement) else "reviewer"
            task["skill"] = skill
            warnings.append("Replaced invalid skill for `{0}` with `{1}`.".format(task_id, skill))
        task["depends_on"] = _list(task.get("depends_on"))
        if "depends_on" not in raw_task:
            warnings.append("Added empty dependencies for `{0}`.".format(task_id))
        if not isinstance(task.get("can_parallel"), bool):
            task["can_parallel"] = True
            warnings.append("Added can_parallel=true for `{0}`.".format(task_id))
        task["constraints"] = _constraints(task.get("constraints"))
        task["expected_output"] = _expected_output(task, task_id, warnings)
        tasks.append(task)

    if not tasks:
        return _result(None, warnings + ["No repairable tasks remained in planner candidate."])
    known = {task["id"] for task in tasks}
    for task in tasks:
        before = list(task["depends_on"])
        task["depends_on"] = [dependency for dependency in before if dependency in known and dependency != task["id"]]
        if len(before) != len(task["depends_on"]):
            warnings.append("Removed unknown or self dependency from `{0}`.".format(task["id"]))

    if not _has_integrator(tasks):
        task_id = _unique_id("integration_review", known)
        tasks.append(_integration_task(task_id, [task["id"] for task in tasks]))
        known.add(task_id)
        warnings.append("Added required integration task `{0}`.".format(task_id))
    if not _has_memory_update(tasks):
        integration_id = next((task["id"] for task in tasks if task.get("skill") == "integrator"), None)
        task_id = _unique_id("memory_update", known)
        tasks.append(_memory_task(task_id, [integration_id] if integration_id else []))
        warnings.append("Added required memory update task `{0}`.".format(task_id))

    graph["tasks"] = tasks
    _remove_cycles(tasks, warnings)
    return _result(graph, warnings)


def _allowed_skills(allowed_skills: Optional[Iterable[str]] = None) -> Set[str]:
    if allowed_skills is not None:
        return set(allowed_skills)
    # Imported lazily to keep repair usable by the validator without a module cycle.
    from local_engine.graph.graph_validator import available_task_skills

    return available_task_skills()


def _requirement_payload(requirement: Dict[str, Any]) -> Dict[str, Any]:
    return {"source_type": requirement.get("source_type", "text"), "raw_summary": requirement.get("raw_summary", "")}


def _goal_payload(requirement: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_goal": requirement.get("user_goal", ""),
        "real_goal": requirement.get("real_goal", requirement.get("user_goal", "")),
        "success_definition": requirement.get("success_definition", "Produce a reviewable result."),
    }


def _list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _constraints(value: Any) -> Dict[str, List[Any]]:
    value = value if isinstance(value, dict) else {}
    return {"must": _list(value.get("must")), "must_not": _list(value.get("must_not"))}


def _expected_output(task: Dict[str, Any], task_id: str, warnings: List[str]) -> Dict[str, str]:
    expected = task.get("expected_output") if isinstance(task.get("expected_output"), dict) else {}
    default_type, default_path = _OUTPUT_BY_SKILL.get(task.get("skill"), ("report", "artifacts/{0}.md".format(task_id)))
    output_type = str(expected.get("type") or default_type)
    path = str(expected.get("path") or _path_for(task_id, output_type, default_path))
    if not _is_safe_output_path(path, output_type):
        path = _path_for(task_id, output_type, default_path)
        warnings.append("Replaced unsafe output path for `{0}`.".format(task_id))
    if not expected:
        warnings.append("Added expected output for `{0}`.".format(task_id))
    return {"type": output_type, "path": path}


def _path_for(task_id: str, output_type: str, default_path: str) -> str:
    if output_type == "patch":
        return "patches/{0}.patch".format(task_id)
    if output_type == "memory_update":
        return "memory_update.md"
    if output_type == "review" and task_id.startswith("integration"):
        return "integration_review.md"
    if output_type in {"doc", "design"}:
        return "deliverables/{0}.md".format(task_id)
    return "artifacts/{0}.md".format(task_id) if default_path.startswith("patches/") else default_path


def _is_safe_output_path(path: str, output_type: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or ":" in path or ".." in path.split("/"):
        return False
    if output_type == "patch":
        return path.startswith("patches/")
    if output_type == "memory_update":
        return path == "memory_update.md"
    if output_type == "review" and path == "integration_review.md":
        return True
    return path.startswith("artifacts/") or path.startswith("deliverables/")


def _looks_like_content(requirement: Dict[str, Any]) -> bool:
    text = " ".join(str(requirement.get(key, "")) for key in ("user_goal", "raw_summary")).lower()
    return any(token in text for token in ("content", "script", "video", "copy", "文章", "脚本", "短视频", "图文"))


def _has_integrator(tasks: List[Dict[str, Any]]) -> bool:
    return any(task.get("skill") == "integrator" or task.get("id") == "integration_review" for task in tasks)


def _has_memory_update(tasks: List[Dict[str, Any]]) -> bool:
    return any(task.get("skill") == "memory_manager" or task.get("id") == "memory_update" for task in tasks)


def _unique_id(base: str, known: Set[str]) -> str:
    if base not in known:
        return base
    number = 2
    while "{0}_{1}".format(base, number) in known:
        number += 1
    return "{0}_{1}".format(base, number)


def _integration_task(task_id: str, dependencies: List[str]) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": "Review integration readiness",
        "skill": "integrator",
        "depends_on": dependencies,
        "can_parallel": False,
        "expected_output": {"type": "review", "path": "integration_review.md"},
        "constraints": {"must": ["Summarize all task outputs."], "must_not": []},
    }


def _memory_task(task_id: str, dependencies: List[str]) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": "Capture durable run memory",
        "skill": "memory_manager",
        "depends_on": dependencies,
        "can_parallel": False,
        "expected_output": {"type": "memory_update", "path": "memory_update.md"},
        "constraints": {"must": ["Record decisions and follow-up risks."], "must_not": []},
    }


def _remove_cycles(tasks: List[Dict[str, Any]], warnings: List[str]) -> None:
    dependencies = {task["id"]: list(task.get("depends_on", [])) for task in tasks}
    while True:
        cycle = _find_cycle(dependencies)
        if not cycle:
            return
        source, target = cycle[-2], cycle[-1]
        dependencies[source].remove(target)
        for task in tasks:
            if task["id"] == source:
                task["depends_on"] = dependencies[source]
                break
        warnings.append("Removed cyclic dependency `{0}` -> `{1}`.".format(source, target))


def _find_cycle(dependencies: Dict[str, List[str]]) -> Optional[List[str]]:
    visited: Set[str] = set()
    stack: List[str] = []
    active: Set[str] = set()

    def visit(task_id: str) -> Optional[List[str]]:
        if task_id in active:
            return stack[stack.index(task_id) :] + [task_id]
        if task_id in visited:
            return None
        visited.add(task_id)
        active.add(task_id)
        stack.append(task_id)
        for dependency in dependencies.get(task_id, []):
            cycle = visit(dependency)
            if cycle:
                return cycle
        stack.pop()
        active.remove(task_id)
        return None

    for task_id in dependencies:
        cycle = visit(task_id)
        if cycle:
            return cycle
    return None


def _result(graph: Optional[Dict[str, Any]], warnings: List[str]) -> GraphRepairResult:
    body = "\n".join("- {0}".format(warning) for warning in warnings) or "- No repair was required."
    report = "# Graph Repair Report\n\n## Actions\n{0}\n".format(body)
    return GraphRepairResult(graph, warnings, report)
