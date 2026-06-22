"""Dynamic graph selection with a deterministic software-development fallback."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from local_engine.graph.graph_validator import validate_and_repair_graph


@dataclass
class GraphSelection:
    graph: Dict[str, Any]
    repair_report: str


def _task(task_id: str, title: str, skill: str, depends_on: list, output_type: str, output_path: str) -> Dict[str, Any]:
    return {
        "id": task_id,
        "title": title,
        "skill": skill,
        "depends_on": depends_on,
        "can_parallel": not depends_on,
        "expected_output": {"type": output_type, "path": output_path},
        "constraints": {"must": [], "must_not": []},
    }


def build_task_graph(run_id: str, requirement: Dict[str, Any], warnings: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Build the legacy software-development graph exclusively as a safe fallback."""
    return {
        "run_id": run_id,
        "requirement": {"source_type": requirement["source_type"], "raw_summary": requirement["raw_summary"]},
        "goal": {
            "user_goal": requirement["user_goal"],
            "real_goal": requirement["real_goal"],
            "success_definition": requirement["success_definition"],
        },
        "tasks": [
            _task("project_audit", "Audit relevant project architecture", "reviewer", [], "report", "artifacts/project_audit.md"),
            _task("product_plan", "Create the product plan", "product", [], "report", "artifacts/product_plan.md"),
            _task("backend_plan", "Plan backend changes", "backend", ["project_audit", "product_plan"], "patch", "patches/backend_plan.patch"),
            _task("frontend_plan", "Plan frontend changes", "frontend", ["project_audit", "product_plan"], "patch", "patches/frontend_plan.patch"),
            _task("test_plan", "Create the test plan", "tester", ["backend_plan", "frontend_plan"], "report", "artifacts/test_plan.md"),
            _task("integration_review", "Review integration readiness", "integrator", ["backend_plan", "frontend_plan", "test_plan"], "review", "integration_review.md"),
            _task("memory_update", "Capture durable run memory", "memory_manager", ["integration_review"], "memory_update", "memory_update.md"),
        ],
        "eval": {
            "checklist": [
                "All graph tasks produced raw and normalized output.",
                "Malformed worker output was preserved without stopping dependent tasks.",
                "Patches and conflicts were listed for review.",
                "Plan mode did not apply project-source changes.",
            ]
        },
        "metadata": {
            "graph_source": "fallback_template",
            "planner_confidence": 0.0,
            "warnings": list(warnings or ["Dynamic graph planning failed; used default software_dev template."]),
        },
    }


def select_task_graph(
    run_id: str,
    requirement: Dict[str, Any],
    candidate: Any,
    planner_confidence: float = 0.0,
    planner_warnings: Optional[Iterable[str]] = None,
) -> GraphSelection:
    """Choose a validated dynamic graph, repaired graph, or final fallback graph."""
    warnings = list(planner_warnings or [])
    if isinstance(candidate, dict) and candidate.get("run_id") and candidate.get("run_id") != run_id:
        candidate = deepcopy(candidate)
        candidate["run_id"] = run_id
        warnings.append("Normalized the planner candidate run_id to the active run.")
    validation = validate_and_repair_graph(candidate, run_id, requirement)
    if validation.graph is not None:
        graph = validation.graph
        metadata = graph.get("metadata") if isinstance(graph.get("metadata"), dict) else {}
        metadata.update(
            {
                "graph_source": "repaired" if validation.repaired else "dynamic",
                "planner_confidence": float(planner_confidence or 0.0),
                "warnings": warnings + validation.warnings,
            }
        )
        graph["metadata"] = metadata
        return GraphSelection(graph, validation.report)

    warnings.extend(validation.warnings)
    warnings.append("Dynamic graph planning failed; used default software_dev template.")
    graph = build_task_graph(run_id, requirement, warnings)
    graph["metadata"]["planner_confidence"] = float(planner_confidence or 0.0)
    return GraphSelection(graph, validation.report)
