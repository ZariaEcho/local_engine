import json
import sys

import pytest
from typer.testing import CliRunner

from local_engine.artifacts.artifact_store import ArtifactStore
from local_engine.cli import app
from local_engine.context.context_builder import build_context, list_modules
from local_engine.context.repo_scanner import scan_project
from local_engine.graph.dynamic_builder import build_graph
from local_engine.planner.intent_classifier import AUDIT, BUILD, DOCUMENT, LEARN, PLAN, REFACTOR, RESEARCH, TEST, classify
from local_engine.runtime.engine import Engine
from local_engine.workers.mock_worker import MockWorker


def algorithm_project(tmp_path):
    project = tmp_path / "algorithms"
    (project / "tree").mkdir(parents=True)
    (project / "BFS.py").write_text("def bfs(): pass\n", encoding="utf-8")
    (project / "tree" / "dfs.py").write_text("def dfs(): pass\n", encoding="utf-8")
    (project / "dijkstra.py").write_text("def dijkstra(): pass\n", encoding="utf-8")
    (project / "README.md").write_text("# Algorithms\n", encoding="utf-8")
    (project / "requirements.txt").write_text("PyYAML>=6\npytest\n", encoding="utf-8")
    return project


def test_repo_scanner_writes_repo_map_and_context_includes_algorithm_modules(tmp_path):
    project = algorithm_project(tmp_path)
    info = scan_project(project)
    cache = project / ".local_engine" / "cache" / "repo_map.json"
    assert cache.is_file()
    assert "Python" in info.languages
    assert "BFS.py" in info.files
    assert "tree" in info.directories
    assert {"PyYAML", "pytest"}.issubset(set(info.dependencies))
    persisted = json.loads(cache.read_text(encoding="utf-8"))
    assert persisted["files"] == info.files

    context = build_context(info)
    assert "# Project Summary" in context
    assert "- Python" in context
    assert {"BFS", "DFS", "Dijkstra"}.issubset(set(list_modules(info)))


@pytest.mark.parametrize(
    "text, expected",
    [
        ("审计这个项目", AUDIT),
        ("给我一个计划", PLAN),
        ("指出知识点欠缺", LEARN),
        ("实现一个新功能", BUILD),
        ("补充测试", TEST),
        ("生成项目文档", DOCUMENT),
        ("重构这个模块", REFACTOR),
        ("调研几个方案", RESEARCH),
    ],
)
def test_intent_classifier_supports_context_engine_intents(text, expected):
    assert classify(text) == expected


def test_dynamic_builder_generates_intent_specific_safe_graphs():
    audit = build_graph(AUDIT, "context", "audit_run")
    assert [task["id"] for task in audit["tasks"]][:3] == ["scan", "analyze", "audit_report"]
    assert audit["tasks"][2]["expected_output"]["path"] == "deliverables/AUDIT_REPORT.md"

    learn = build_graph(LEARN, "context", "learn_run")
    assert any(task["id"] == "knowledge_gap" for task in learn["tasks"])
    assert any(task["expected_output"]["path"] == "deliverables/KNOWLEDGE_GAP.md" for task in learn["tasks"])

    build = build_graph(BUILD, "context", "build_run")
    assert {"backend", "frontend", "test"}.issubset({task["id"] for task in build["tasks"]})
    for graph in (audit, learn, build):
        assert any(task["skill"] == "integrator" for task in graph["tasks"])
        assert any(task["skill"] == "memory_manager" for task in graph["tasks"])


def test_artifact_store_uses_required_project_local_categories(tmp_path):
    target = ArtifactStore(tmp_path).save_artifact("audit", "# Audit\n", "AUDIT_REPORT.md")
    assert target == tmp_path / ".local_engine" / "artifacts" / "audit" / "AUDIT_REPORT.md"
    assert target.read_text(encoding="utf-8") == "# Audit\n"


def test_engine_run_produces_audit_and_knowledge_gap_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = algorithm_project(tmp_path)
    engine = Engine()
    engine.initialize(project)
    audit = engine.run(project, "审计这个项目", worker_factory=lambda: MockWorker())
    learn = engine.run(project, "指出知识点欠缺", worker_factory=lambda: MockWorker())
    assert (audit.report_dir / "deliverables" / "AUDIT_REPORT.md").is_file()
    assert (learn.report_dir / "deliverables" / "KNOWLEDGE_GAP.md").is_file()
    assert (audit.report_dir / "deliverables" / "FINAL_DELIVERY.md").is_file()
    assert (audit.report_dir / "artifacts" / "task_results.yaml").is_file()
    assert (project / ".local_engine" / "artifacts" / "audit" / "AUDIT_REPORT.md").is_file()
    assert (project / ".local_engine" / "artifacts" / "plan" / "KNOWLEDGE_GAP.md").is_file()
    audit_prompt = (audit.report_dir / "prompts" / "scan.prompt.md").read_text(encoding="utf-8")
    assert "# Project Context\n# Project Summary" in audit_prompt
    assert "# Repository Summary" in audit_prompt


def test_cli_scan_inspect_and_graph_commands(tmp_path):
    project = algorithm_project(tmp_path)
    runner = CliRunner()
    scan = runner.invoke(app, ["scan", "--project", str(project)])
    assert scan.exit_code == 0, scan.output
    assert (project / ".local_engine" / "cache" / "repo_map.json").is_file()
    assert (project / ".local_engine" / "PROJECT_CONTEXT.md").is_file()

    inspect = runner.invoke(app, ["inspect", "--project", str(project)])
    assert inspect.exit_code == 0, inspect.output
    assert "Languages:\nPython" in inspect.output
    assert "Modules:\nBFS" in inspect.output
    assert "Dijkstra" in inspect.output

    preview = runner.invoke(app, ["graph", "--project", str(project), "实现一个功能"])
    assert preview.exit_code == 0, preview.output
    assert "Task Graph Preview (BUILD)" in preview.output
    assert "project_type: algorithm_repository" in preview.output
    assert "- generate_algorithm_files [generate_code]" in preview.output
    assert "- test_algorithm_files [test_generation]" in preview.output
    assert "backend" not in preview.output
    assert "frontend" not in preview.output


def test_cli_run_writes_audit_and_knowledge_gap_deliverables(tmp_path, monkeypatch):
    project = algorithm_project(tmp_path)
    global_home = tmp_path / "global"
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(global_home))
    Engine().initialize(project)
    fake_worker = tmp_path / "fake_worker.py"
    fake_worker.write_text("print('type: report\\nbody: fake worker output')\n", encoding="utf-8")
    (global_home / "config.yaml").write_text(
        json.dumps({"workers": 2, "claude_command": [sys.executable, str(fake_worker)], "timeout_seconds": 10}), encoding="utf-8"
    )
    runner = CliRunner()
    audit = runner.invoke(app, ["run", "--project", str(project), "审计这个项目"])
    assert audit.exit_code == 0, audit.output
    assert "run_id:" in audit.output
    assert "report_dir:" in audit.output
    assert "final_report:" in audit.output
    assert "task_status:" in audit.output
    learn = runner.invoke(app, ["run", "--project", str(project), "指出知识点欠缺"])
    assert learn.exit_code == 0, learn.output
    reports = sorted((project / ".local_engine" / "runs").iterdir())
    assert any((report / "deliverables" / "AUDIT_REPORT.md").is_file() for report in reports)
    assert any((report / "deliverables" / "KNOWLEDGE_GAP.md").is_file() for report in reports)
    latest = runner.invoke(app, ["report", "--latest"])
    assert latest.exit_code == 0, latest.output
    assert "# local_engine Final Report" in latest.output
    diagnostics = runner.invoke(app, ["doctor"])
    assert diagnostics.exit_code == 0, diagnostics.output
    assert "local-engine doctor" in diagnostics.output


def test_cli_run_reports_claude_failure_after_writing_run_evidence(tmp_path, monkeypatch):
    project = algorithm_project(tmp_path)
    global_home = tmp_path / "global"
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(global_home))
    Engine().initialize(project)
    fake_worker = tmp_path / "failing_worker.py"
    fake_worker.write_text("import sys\nsys.stderr.write('Claude unavailable\\n')\nsys.exit(23)\n", encoding="utf-8")
    (global_home / "config.yaml").write_text(
        json.dumps({"workers": 2, "claude_command": [sys.executable, str(fake_worker)], "timeout_seconds": 10}), encoding="utf-8"
    )
    result = CliRunner().invoke(app, ["run", "--project", str(project), "审计这个项目"])
    assert result.exit_code == 1, result.output
    assert "Claude CLI failed" in result.output
    assert "task_status:" in result.output
    reports = sorted((project / ".local_engine" / "runs").iterdir())
    report = reports[-1]
    assert (report / "error.log").is_file()
    assert "Claude CLI exited with code 23" in (report / "final_report.md").read_text(encoding="utf-8")
