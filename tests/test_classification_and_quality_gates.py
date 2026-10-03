import copy
import json

import pytest
from typer.testing import CliRunner

from local_engine.cli import _resolve_cli_intent, app
from local_engine.graph.dynamic_builder import build_graph
from local_engine.graph.graph_quality import GraphQualityError, graph_quality_check
from local_engine.intents.classification import ClarificationRequired
from local_engine.intents.registry import IntentRegistry
from local_engine.kernel.schemas import TaskResult, WorkerResult
from local_engine.runtime.engine import Engine
from local_engine.runtime.quality import QualityEvaluator
from local_engine.runtime.run_index import RunIndex
from local_engine.task_templates.registry import TaskTemplateRegistry
from local_engine.workers.mock_worker import MockWorker


def initialized_project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    Engine().initialize(project)
    return project


def test_structured_classification_is_explainable_and_ambiguous_requests_gate(tmp_path, monkeypatch):
    registry = IntentRegistry.load()
    audit = registry.classify_result("请分析项目结构和问题")
    assert audit.intent == "AUDIT"
    assert audit.confidence >= 0.6
    assert "项目结构" in audit.reason
    assert audit.to_dict()["candidates"]

    ambiguous = registry.classify_result("调研几个方案")
    assert ambiguous.intent == "RESEARCH"
    assert ambiguous.confidence < 0.6
    assert "tied" in ambiguous.reason

    project = initialized_project(tmp_path, monkeypatch)
    with pytest.raises(ClarificationRequired):
        Engine().run(project, "do something helpful", worker_factory=lambda: MockWorker())


def test_cli_noninteractive_clarification_and_explicit_override(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    runner = CliRunner()
    uncertain = runner.invoke(app, ["run", "--project", str(project), "do something helpful"])
    assert uncertain.exit_code == 2
    assert "判断不确定" in uncertain.output
    assert "A. PLAN" in uncertain.output

    outcome = Engine().run(
        project,
        "do something helpful",
        intent_override="PLAN",
        worker_factory=lambda: MockWorker(),
    )
    # YAML evidence is intentionally human-readable and records the explicit gate bypass.
    evidence = (outcome.report_dir / "internal" / "classification.yaml").read_text(encoding="utf-8")
    assert "confidence: 1.0" in evidence
    assert "explicit --intent override" in evidence
    assert "## Classification" in outcome.final_report.read_text(encoding="utf-8")


def test_interactive_cli_selection_uses_ranked_candidate(monkeypatch):
    class InteractiveInput:
        @staticmethod
        def isatty():
            return True

    monkeypatch.setattr("local_engine.cli.sys.stdin", InteractiveInput())
    monkeypatch.setattr("local_engine.cli.typer.prompt", lambda _label: "B")
    selected = _resolve_cli_intent(Engine(), "do something helpful", None, None, None)
    assert selected == "AUDIT"


def test_graph_quality_detects_missing_dependencies_and_redundancy():
    intents = IntentRegistry.load()
    templates = TaskTemplateRegistry.load()
    graph = build_graph("AUDIT", "context", "audit", intents=intents, templates=templates)
    assert graph_quality_check(graph, "AUDIT", intents, templates).passed
    for intent in intents.names:
        builtin = build_graph(intent, "context", intent.lower(), intents=intents, templates=templates)
        assert graph_quality_check(builtin, intent, intents, templates).passed

    missing_task = copy.deepcopy(graph)
    missing_task["tasks"] = [task for task in missing_task["tasks"] if task["id"] != "analyze"]
    missing = graph_quality_check(missing_task, "AUDIT", intents, templates)
    assert not missing.passed
    assert any("audit_analyze" in error for error in missing.errors)

    missing_dependency = copy.deepcopy(graph)
    next(task for task in missing_dependency["tasks"] if task["id"] == "audit_report")["depends_on"] = []
    dependency = graph_quality_check(missing_dependency, "AUDIT", intents, templates)
    assert not dependency.passed
    assert any("audit_report" in error and "analyze" in error for error in dependency.errors)

    duplicate = copy.deepcopy(graph)
    extra = copy.deepcopy(next(task for task in duplicate["tasks"] if task["id"] == "audit_report"))
    extra["id"] = "audit_report_copy"
    duplicate["tasks"].append(extra)
    redundant = graph_quality_check(duplicate, "AUDIT", intents, templates)
    assert redundant.passed
    assert redundant.warnings


def test_engine_persists_and_indexes_blocked_graph_quality_failure(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    from local_engine.runtime.phases import plan as plan_module

    original_builder = plan_module.build_graph

    def invalid_builder(*args, **kwargs):
        graph = original_builder(*args, **kwargs)
        next(task for task in graph["tasks"] if task["id"] == "audit_report")["depends_on"] = []
        return graph

    monkeypatch.setattr(plan_module, "build_graph", invalid_builder)
    with pytest.raises(GraphQualityError):
        Engine().run(project, "审计这个项目", worker_factory=lambda: MockWorker())

    record = RunIndex().latest()
    assert record["status"] == "failed"
    report = project / ".local_engine" / "runs" / record["run_id"]
    quality = json.loads((report / "artifacts" / "graph_quality.json").read_text(encoding="utf-8"))
    assert quality["passed"] is False
    assert "missing declared dependencies" in (report / "internal" / "graph_quality.md").read_text(encoding="utf-8")


def test_task_summaries_reach_only_direct_dependencies_and_cache_materializes_them(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    first = Engine().run(project, "给我一个计划", worker_factory=lambda: MockWorker())
    summary = first.report_dir / "artifacts" / "task_summaries" / "scan.yaml"
    assert summary.is_file()
    assert "findings:" in summary.read_text(encoding="utf-8")

    plan_prompt = (first.report_dir / "prompts" / "plan.prompt.md").read_text(encoding="utf-8")
    assert "# Direct Dependency Summaries" in plan_prompt
    assert "Mock finding for scan" in plan_prompt
    assert "Mock finding for integration_review" not in plan_prompt

    second = Engine().run(project, "给我一个计划", worker_factory=lambda: MockWorker())
    manifest = (second.report_dir / "artifacts" / "task_results.yaml").read_text(encoding="utf-8")
    assert "cache_action: reuse" in manifest
    assert (second.report_dir / "artifacts" / "task_summaries" / "scan.yaml").is_file()


def test_output_quality_is_nonblocking_and_incomplete_results_are_not_cached(tmp_path, monkeypatch):
    complete = TaskResult(
        "task",
        "raw",
        {"confidence": 0.9, "body": "x" * 100, "findings": ["finding"], "recommendations": ["recommendation"]},
    )
    report = QualityEvaluator().evaluate({"id": "task"}, complete)
    assert report.complete and report.quality == 1.0

    incomplete = TaskResult("task", "raw", {"confidence": 0.2, "body": "short", "findings": []})
    incomplete_report = QualityEvaluator().evaluate({"id": "task"}, incomplete)
    assert not incomplete_report.complete
    assert any("incomplete_output" in warning for warning in incomplete_report.warnings)
    assert any("low_confidence" in warning for warning in incomplete_report.warnings)

    class IncompleteWorker:
        def run(self, prompt, task, project_root):
            if task["skill"] == "review_code":
                return WorkerResult(raw="type: review\nconfidence: 1\nbody: 'VERDICT: PASS'\n")
            return WorkerResult(raw="type: report\nconfidence: 0.2\nbody: short\n")

    project = initialized_project(tmp_path, monkeypatch)
    first = Engine().run(project, "给我一个计划", worker_factory=IncompleteWorker)
    quality = json.loads((first.report_dir / "artifacts" / "quality" / "scan.json").read_text(encoding="utf-8"))
    assert quality["complete"] is False
    assert quality["quality"] == 0.2
    assert first.task_statuses["scan"] == "completed"
    assert "scan: incomplete_output" in first.final_report.read_text(encoding="utf-8")

    second_worker = MockWorker()
    Engine().run(project, "给我一个计划", worker_factory=lambda: second_worker)
    assert "scan" in second_worker.prompts
