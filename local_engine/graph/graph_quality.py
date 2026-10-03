"""Semantic quality checks for structurally valid task graphs."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from local_engine.intents.registry import IntentRegistry
from local_engine.task_templates.registry import TaskTemplateRegistry


@dataclass
class GraphQualityResult:
    """A deterministic graph-quality decision with blocking errors and warnings."""

    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    checks: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.errors

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
        }

    def to_markdown(self) -> str:
        def section(title: str, values: List[str]) -> List[str]:
            return ["## " + title, ""] + (["- " + value for value in values] or ["- None"]) + [""]

        lines = ["# Graph Quality Report", "", "- Passed: {0}".format("yes" if self.passed else "no"), ""]
        lines.extend(section("Errors", self.errors))
        lines.extend(section("Warnings", self.warnings))
        lines.extend(["## Checks", ""])
        for name, value in self.checks.items():
            lines.append("- {0}: {1}".format(name, value))
        return "\n".join(lines).rstrip() + "\n"


class GraphQualityError(ValueError):
    """Raised when a graph is executable but semantically insufficient."""

    def __init__(self, result: GraphQualityResult) -> None:
        self.result = result
        super().__init__("graph quality check failed: {0}".format("; ".join(result.errors)))


def graph_quality_check(
    graph: Dict[str, Any],
    intent: Optional[str] = None,
    intents: Optional[IntentRegistry] = None,
    templates: Optional[TaskTemplateRegistry] = None,
) -> GraphQualityResult:
    """Validate intent-template coverage, declared dependencies, and redundancy.

    Structural validity belongs to :func:`validate_graph`; this function assumes a
    safe DAG and checks whether the graph still contains the work declared by its
    registry intent. Direct-skill graphs deliberately have no intent profile and
    therefore receive a passing, not-applicable coverage check.
    """
    intents = intents or IntentRegistry.load()
    templates = templates or TaskTemplateRegistry.load()
    result = GraphQualityResult()
    tasks = graph.get("tasks", []) if isinstance(graph, dict) else []
    metadata = graph.get("metadata", {}) if isinstance(graph, dict) else {}
    selected_intent = str(intent or metadata.get("intent", "")).upper()
    by_template = {
        str(task.get("template")): task
        for task in tasks
        if isinstance(task, dict) and isinstance(task.get("template"), str)
    }

    _check_project_compatibility(result, tasks, metadata)
    _check_required_templates(result, selected_intent, by_template, intents, metadata)
    _check_declared_dependencies(result, tasks, by_template, templates)
    _check_redundancy(result, tasks)
    result.checks.setdefault("task_count", len(tasks))
    result.checks.setdefault("intent", selected_intent or "unknown")
    return result


def require_graph_quality(*args: Any, **kwargs: Any) -> GraphQualityResult:
    """Return a passing report or raise :class:`GraphQualityError`."""
    result = graph_quality_check(*args, **kwargs)
    if not result.passed:
        raise GraphQualityError(result)
    return result


def _check_required_templates(
    result: GraphQualityResult,
    selected_intent: str,
    by_template: Dict[str, Dict[str, Any]],
    intents: IntentRegistry,
    metadata: Dict[str, Any],
) -> None:
    if not selected_intent or selected_intent == "SKILL":
        result.checks["required_template_coverage"] = "not_applicable"
        return
    try:
        definition = intents.get(selected_intent)
    except KeyError:
        result.errors.append("graph metadata references unknown intent `{0}`".format(selected_intent))
        return
    required = list(definition.required_tasks)
    project_type = metadata.get("project_type") if isinstance(metadata.get("project_type"), dict) else {}
    if (
        selected_intent == "BUILD"
        and project_type.get("project_type") == "algorithm_repository"
        and not metadata.get("explicit_web_requested")
    ):
        required = ["build_plan", "build_algorithm_generate", "build_algorithm_test"]
    missing = [name for name in required if name not in by_template]
    if missing:
        result.errors.append(
            "intent `{0}` is missing required task templates: {1}".format(selected_intent, ", ".join(missing))
        )
    result.checks["required_template_coverage"] = "pass" if not missing else "missing: " + ", ".join(missing)


def _check_declared_dependencies(
    result: GraphQualityResult,
    tasks: List[Dict[str, Any]],
    by_template: Dict[str, Dict[str, Any]],
    templates: TaskTemplateRegistry,
) -> None:
    task_ids = [str(task.get("id", "")) for task in tasks if isinstance(task, dict)]
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not isinstance(task.get("template"), str):
            continue
        template_name = task["template"]
        try:
            template = templates.get(template_name)
        except KeyError:
            result.errors.append("task `{0}` references unknown template `{1}`".format(task.get("id", "?"), template_name))
            continue
        actual = {str(value) for value in task.get("depends_on", [])}
        expected = set()
        for dependency in template.depends_on:
            if dependency == "*":
                expected.update(task_ids[:index])
                continue
            dependency_task = by_template.get(dependency)
            if dependency_task is None:
                result.errors.append(
                    "task `{0}` requires unavailable template dependency `{1}`".format(task.get("id", "?"), dependency)
                )
                continue
            expected.add(str(dependency_task.get("id", "")))
        missing = sorted(value for value in expected if value and value not in actual)
        if missing:
            result.errors.append(
                "task `{0}` is missing declared dependencies: {1}".format(task.get("id", "?"), ", ".join(missing))
            )
    result.checks["declared_dependencies"] = "pass" if not result.errors else "failed"


def _check_project_compatibility(
    result: GraphQualityResult,
    tasks: List[Dict[str, Any]],
    metadata: Dict[str, Any],
) -> None:
    project_type = metadata.get("project_type") if isinstance(metadata.get("project_type"), dict) else {}
    if project_type.get("project_type") != "algorithm_repository":
        result.checks["project_compatibility"] = "not_applicable"
        return
    if metadata.get("explicit_web_requested"):
        result.checks["project_compatibility"] = "explicit_web_requested"
        return
    forbidden = {"backend", "frontend", "api", "ui", "db", "database"}
    offenders: List[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        expected = task.get("expected_output", {}) if isinstance(task.get("expected_output"), dict) else {}
        values = [
            task.get("id", ""),
            task.get("skill", ""),
            task.get("task_type", ""),
            task.get("title", ""),
            expected.get("path", ""),
        ]
        text = " ".join(str(value).casefold().replace("-", "_") for value in values)
        if any(re.search(r"(?<![a-z0-9_]){0}(?![a-z0-9_])".format(re.escape(term)), text) for term in forbidden):
            offenders.append(str(task.get("id", "?")))
    if offenders:
        result.errors.append(
            "Algorithm repository should not use generic web-app BUILD graph. Offending tasks: {0}".format(
                ", ".join(dict.fromkeys(offenders))
            )
        )
        result.checks["project_compatibility"] = "failed"
    else:
        result.checks["project_compatibility"] = "pass"


def _check_redundancy(result: GraphQualityResult, tasks: List[Dict[str, Any]]) -> None:
    template_seen: Dict[str, str] = {}
    output_seen: Dict[tuple[str, str], str] = {}
    equivalent_seen: Dict[tuple[str, str], str] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id", "?"))
        template = task.get("template")
        if isinstance(template, str) and template in template_seen:
            result.warnings.append("tasks `{0}` and `{1}` repeat template `{2}`".format(template_seen[template], task_id, template))
        elif isinstance(template, str):
            template_seen[template] = task_id

        expected = task.get("expected_output", {}) if isinstance(task.get("expected_output"), dict) else {}
        output_key = (str(task.get("task_type", "")), str(expected.get("path", "")))
        if output_key[0] and output_key[1] and output_key in output_seen:
            result.warnings.append(
                "tasks `{0}` and `{1}` share task type/output `{2}` -> `{3}`".format(
                    output_seen[output_key], task_id, output_key[0], output_key[1]
                )
            )
        elif output_key[0] and output_key[1]:
            output_seen[output_key] = task_id

        title = re.sub(r"\s+", " ", str(task.get("title", "")).strip().casefold())
        equivalent_key = (str(task.get("task_type", "")), title)
        if equivalent_key[0] and equivalent_key[1] and equivalent_key in equivalent_seen:
            result.warnings.append(
                "tasks `{0}` and `{1}` have equivalent `{2}` titles".format(
                    equivalent_seen[equivalent_key], task_id, equivalent_key[0]
                )
            )
        elif equivalent_key[0] and equivalent_key[1]:
            equivalent_seen[equivalent_key] = task_id
    result.checks["redundancy"] = "warnings" if result.warnings else "pass"
