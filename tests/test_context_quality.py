import json

from local_engine.context.context_builder import build_context
from local_engine.context.context_quality import assess_context_quality
from local_engine.context.repo_scanner import scan_project
from local_engine.runtime.engine import Engine
from local_engine.workers.mock_worker import MockWorker


def _quality_project(tmp_path, *, tests: bool = False):
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    if tests:
        (project / "tests").mkdir()
        (project / "tests" / "test_main.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    return project


def test_python_project_without_tests_warns_and_fails_completeness_threshold(tmp_path):
    project = _quality_project(tmp_path)
    info = scan_project(project)
    report = assess_context_quality(project, info, build_context(info))

    assert "tests/" in report.missing_core_paths
    assert any("No test directory" in warning for warning in report.warnings)
    assert report.coverage < 0.7
    assert report.complete is False


def test_package_manifest_not_named_in_context_warns(tmp_path):
    project = tmp_path / "node_project"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"name": "demo", "dependencies": {"vite": "1"}}), encoding="utf-8")
    (project / "src").mkdir()
    (project / "src" / "index.ts").write_text("export const value = 1;\n", encoding="utf-8")
    info = scan_project(project)

    report = assess_context_quality(project, info, "# Project Summary\n\n## Languages\n- TypeScript\n")

    assert "package.json" in report.detected_core_paths
    assert any("package.json exists but was not summarized" in warning for warning in report.warnings)
    assert report.dependency_confidence == 0.0


def test_large_project_gets_size_risk_warning(tmp_path):
    project = tmp_path / "large"
    project.mkdir()
    (project / "main.py").write_text("print('ok')\n", encoding="utf-8")
    for number in range(1001):
        (project / "generated_{0}.txt".format(number)).write_text("x", encoding="utf-8")

    info = scan_project(project)
    report = assess_context_quality(project, info, build_context(info))

    assert report.file_count > 1000
    assert "Large project detected. Context may be incomplete." in report.warnings


def test_low_context_coverage_is_not_complete(tmp_path):
    project = _quality_project(tmp_path, tests=True)
    info = scan_project(project)
    report = assess_context_quality(project, info, "# Project Summary\n")

    assert report.coverage < 0.7
    assert report.complete is False


def test_context_warnings_are_persisted_and_injected_into_task_prompts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = _quality_project(tmp_path)
    engine = Engine()
    engine.initialize(project)

    outcome = engine.run(project, "审计这个项目", worker_factory=lambda: MockWorker())

    quality = json.loads((outcome.report_dir / "artifacts" / "context_quality.json").read_text(encoding="utf-8"))
    prompt = (outcome.report_dir / "prompts" / "scan.prompt.md").read_text(encoding="utf-8")
    final_report = outcome.final_report.read_text(encoding="utf-8")
    assert quality["warnings"]
    assert (outcome.report_dir / "artifacts" / "context_quality.md").is_file()
    assert "# Context Quality Warnings" in prompt
    assert "When making conclusions, explicitly mark assumptions and avoid overclaiming." in prompt
    assert "## Context Quality" in final_report
    assert "context quality:" in final_report
