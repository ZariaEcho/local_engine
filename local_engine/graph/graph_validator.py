"""Strict graph validation plus a bounded repair attempt for planner output."""

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Dict, Iterable, List, Optional, Set

_ARTIFACT_OUTPUT_TYPES = {"report", "doc", "analysis", "research", "design"}
_WARNING_SIP_TYPES = {"error", "parse_error", "unstructured"}


def available_task_skills() -> Set[str]:
    """Load the currently installed task capabilities instead of fixing them in code."""
    try:
        from local_engine.agents.registry import AgentRegistry
        from local_engine.skills.registry import SkillRegistry

        return set(AgentRegistry.load().names) | set(SkillRegistry.load().names)
    except (FileNotFoundError, ValueError):
        return set()


# Compatibility export for integrations importing the old name. Validation refreshes
# the registry each time, so edits to YAML files take effect without a code change.
ALLOWED_SKILLS = available_task_skills()


def validate_graph(graph: Dict[str, Any], allowed_skills: Optional[Iterable[str]] = None) -> None:
    """Raise ``ValueError`` when a candidate is not a safe executable DAG."""
    if not isinstance(graph, dict):
        raise ValueError("task graph must be a mapping")
    if not isinstance(graph.get("run_id"), str) or not graph["run_id"].strip():
        raise ValueError("task graph must contain a non-empty run_id")
    tasks = graph.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("task graph must contain at least one task")

    capabilities = set(allowed_skills) if allowed_skills is not None else available_task_skills()
    if not capabilities:
        raise ValueError("no dynamic agent or skill definitions are available")
    ids: List[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("every graph task must be a mapping")
        for field in ("id", "title", "skill", "depends_on", "expected_output"):
            if field not in task:
                raise ValueError("task is missing required field `{0}`".format(field))
        task_id = task["id"]
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("every graph task needs a non-empty id")
        if not isinstance(task["title"], str) or not task["title"].strip():
            raise ValueError("task `{0}` needs a non-empty title".format(task_id))
        if task["skill"] not in capabilities:
            raise ValueError("task `{0}` has an unsupported skill `{1}`".format(task_id, task["skill"]))
        if "agent" in task and (not isinstance(task["agent"], str) or not task["agent"].strip()):
            raise ValueError("task `{0}` has an invalid agent".format(task_id))
        if not isinstance(task["depends_on"], list) or not all(isinstance(value, str) for value in task["depends_on"]):
            raise ValueError("task `{0}` must provide dependencies as a list of task ids".format(task_id))
        _validate_expected_output(task_id, task["expected_output"])
        ids.append(task_id)

    if len(ids) != len(set(ids)):
        raise ValueError("task graph contains duplicate task ids")
    known = set(ids)
    for task in tasks:
        missing = set(task["depends_on"]) - known
        if missing:
            raise ValueError("task {0} has unknown dependencies: {1}".format(task["id"], ", ".join(sorted(missing))))
    _assert_acyclic({task["id"]: set(task["depends_on"]) for task in tasks})
    if not any(task["skill"] == "integrator" or task["id"] == "integration_review" for task in tasks):
        raise ValueError("task graph must contain an integration task")
    if not any(task["skill"] == "memory_manager" or task["id"] == "memory_update" for task in tasks):
        raise ValueError("task graph must contain a memory update task")


def _validate_expected_output(task_id: str, expected: Any) -> None:
    if not isinstance(expected, dict):
        raise ValueError("task `{0}` must provide expected_output as a mapping".format(task_id))
    output_type = expected.get("type")
    path = expected.get("path")
    if not isinstance(output_type, str) or not output_type.strip():
        raise ValueError("task `{0}` expected_output.type must be non-empty".format(task_id))
    if not isinstance(path, str) or not path.strip():
        raise ValueError("task `{0}` expected_output.path must be non-empty".format(task_id))
    if not _safe_relative_path(path):
        raise ValueError("task `{0}` has an unsafe expected output path".format(task_id))
    if output_type == "patch" and not path.startswith("patches/"):
        raise ValueError("patch task `{0}` must write under patches/".format(task_id))
    if output_type in _ARTIFACT_OUTPUT_TYPES and not (path.startswith("artifacts/") or path.startswith("deliverables/")):
        raise ValueError("task `{0}` output must write under artifacts/ or deliverables/".format(task_id))
    if output_type == "memory_update" and path != "memory_update.md":
        raise ValueError("memory update task `{0}` must write memory_update.md".format(task_id))
    if output_type == "review" and path != "integration_review.md" and not (
        path.startswith("artifacts/") or path.startswith("deliverables/")
    ):
        raise ValueError("review task `{0}` output has an unsupported path".format(task_id))
    if output_type not in _ARTIFACT_OUTPUT_TYPES | {"patch", "memory_update", "review"} and not (
        path.startswith("artifacts/") or path.startswith("deliverables/")
    ):
        raise ValueError("task `{0}` output must write under artifacts/ or deliverables/".format(task_id))


def _safe_relative_path(path: str) -> bool:
    if "\\" in path or ":" in path:
        return False
    posix = PurePosixPath(path)
    return not posix.is_absolute() and not PureWindowsPath(path).is_absolute() and ".." not in posix.parts


def _assert_acyclic(dependencies: Dict[str, Set[str]]) -> None:
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            raise ValueError("task graph contains a dependency cycle")
        visiting.add(task_id)
        for dependency in dependencies[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in dependencies:
        visit(task_id)


