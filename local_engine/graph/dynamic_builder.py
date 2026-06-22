"""Build safe task graphs directly from Context Engine intents."""

from typing import Any, Dict, Iterable, List, Optional

from local_engine.planner.intent_classifier import AUDIT, BUILD, DOCUMENT, LEARN, PLAN, REFACTOR, RESEARCH, TEST


def build_graph(
    intent: str,
    context: Any,
    run_id: str = "preview",
    requirement: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return an executable, intent-appropriate graph with integration and memory."""
    intent = str(intent or PLAN).upper()
    tasks = _tasks_for(intent)
    _append_required_tasks(tasks)
    requirement = requirement or _default_requirement(intent, context)
    return {
        "run_id": run_id,
        "requirement": {
            "source_type": requirement.get("source_type", "text"),
            "raw_summary": requirement.get("raw_summary", "Context Engine graph preview"),
        },
        "goal": {
            "user_goal": requirement.get("user_goal", "Build a {0} graph".format(intent)),
            "real_goal": requirement.get("real_goal", requirement.get("user_goal", "Build a {0} graph".format(intent))),
            "success_definition": requirement.get("success_definition", "Produce a reviewable Context Engine result."),
        },
        "tasks": tasks,
        "eval": {"checklist": ["Repository context was injected.", "Expected outputs were persisted.", "Warnings were recorded."]},
        "metadata": {"graph_source": "dynamic", "intent": intent, "context_available": bool(context), "warnings": [], "planner_confidence": 1.0},
    }


def _tasks_for(intent: str) -> List[Dict[str, Any]]:
    routes = {
        AUDIT: [
            _task("scan", "Inspect repository structure", "reviewer", [], "report", "artifacts/audit/REPOSITORY_SCAN.md"),
            _task("analyze", "Analyze architecture and risks", "data_analyst", ["scan"], "analysis", "artifacts/audit/ARCHITECTURE_ANALYSIS.md"),
            _task("audit_report", "Write the audit report", "writer", ["analyze"], "report", "deliverables/AUDIT_REPORT.md"),
        ],
        PLAN: [
            _task("scan", "Inspect relevant project context", "reviewer", [], "report", "artifacts/plan/PROJECT_SCAN.md"),
            _task("plan", "Write an actionable implementation plan", "product", ["scan"], "doc", "deliverables/PLAN.md"),
        ],
        LEARN: [
            _task("scan", "Inspect repository concepts", "reviewer", [], "report", "artifacts/plan/REPOSITORY_CONCEPTS.md"),
            _task("knowledge_gap", "Identify knowledge gaps", "data_analyst", ["scan"], "analysis", "deliverables/KNOWLEDGE_GAP.md"),
            _task("learning_plan", "Write a learning plan", "writer", ["knowledge_gap"], "doc", "deliverables/LEARNING_PLAN.md"),
        ],
        BUILD: [
            _task("plan", "Plan the requested implementation", "product", [], "report", "artifacts/plan/IMPLEMENTATION_PLAN.md"),
            _task("backend", "Implement backend changes", "backend", ["plan"], "patch", "patches/BACKEND.patch"),
            _task("frontend", "Implement frontend changes", "frontend", ["plan"], "patch", "patches/FRONTEND.patch"),
            _task("test", "Create a regression test plan", "tester", ["backend", "frontend"], "report", "artifacts/test/TEST_PLAN.md"),
        ],
        TEST: [
            _task("scan", "Inspect test coverage and project behavior", "reviewer", [], "report", "artifacts/test/TEST_SCAN.md"),
            _task("test_plan", "Create focused test cases", "tester", ["scan"], "report", "artifacts/test/TEST_PLAN.md"),
            _task("test_report", "Write test recommendations", "writer", ["test_plan"], "report", "deliverables/TEST_REPORT.md"),
        ],
        DOCUMENT: [
            _task("scan", "Inspect project modules and public interfaces", "reviewer", [], "report", "artifacts/docs/DOCUMENTATION_SCAN.md"),
            _task("documentation", "Write project documentation", "writer", ["scan"], "doc", "deliverables/DOCUMENTATION.md"),
        ],
        REFACTOR: [
            _task("scan", "Audit refactoring opportunities", "reviewer", [], "report", "artifacts/review/REFACTOR_AUDIT.md"),
            _task("refactor_plan", "Plan the refactor", "product", ["scan"], "report", "artifacts/plan/REFACTOR_PLAN.md"),
            _task("refactor", "Implement refactoring changes", "backend", ["refactor_plan"], "patch", "patches/REFACTOR.patch"),
            _task("regression_test", "Plan regression tests", "tester", ["refactor"], "report", "artifacts/test/REFACTOR_TESTS.md"),
        ],
        RESEARCH: [
            _task("research_scope", "Define repository research scope", "researcher", [], "research", "artifacts/plan/RESEARCH_SCOPE.md"),
            _task("analysis", "Analyze research findings", "data_analyst", ["research_scope"], "analysis", "artifacts/review/RESEARCH_ANALYSIS.md"),
            _task("research_report", "Write the research report", "writer", ["analysis"], "report", "deliverables/RESEARCH_REPORT.md"),
        ],
    }
    return routes.get(intent, routes[PLAN])


def _task(task_id: str, title: str, skill: str, dependencies: Iterable[str], output_type: str, path: str) -> Dict[str, Any]:
    dependencies = list(dependencies)
    return {
        "id": task_id,
        "title": title,
        "skill": skill,
        "depends_on": dependencies,
        "can_parallel": not dependencies,
        "expected_output": {"type": output_type, "path": path},
        "constraints": {"must": ["Use repository context and state uncertainty."], "must_not": ["Do not modify files outside the project root."]},
    }


def _append_required_tasks(tasks: List[Dict[str, Any]]) -> None:
    task_ids = [task["id"] for task in tasks]
    tasks.append(
        _task("integration_review", "Review all task outputs", "integrator", task_ids, "review", "integration_review.md")
    )
    tasks.append(
        _task("memory_update", "Record durable project context", "memory_manager", ["integration_review"], "memory_update", "memory_update.md")
    )


def _default_requirement(intent: str, context: Any) -> Dict[str, str]:
    return {
        "source_type": "text",
        "raw_summary": "Context Engine {0} graph".format(intent),
        "user_goal": "Create a {0} result using repository context.".format(intent),
        "real_goal": "Create a reviewable {0} result.".format(intent),
        "success_definition": "The graph executes with project context.",
    }
