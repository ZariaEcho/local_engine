import json

import yaml
from typer.testing import CliRunner

from local_engine.artifacts.recover_report import detect_status, recover_report
from local_engine.cli import app
from local_engine.runtime.run_index import RunIndex


def write_recoverable_run(tmp_path, monkeypatch, with_error=False, run_id="2026-06-25-001"):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    report_dir = project / ".local_engine" / "task_reports" / run_id
    (report_dir / "agent_outputs").mkdir(parents=True)
    (report_dir / "artifacts" / "errors").mkdir(parents=True)
    (report_dir / "artifacts" / "quality").mkdir(parents=True)
    (report_dir / "artifacts" / "retries").mkdir(parents=True)
    (report_dir / "reviews").mkdir(parents=True)
    (report_dir / "deliverables").mkdir(parents=True)
    (report_dir / "prompts").mkdir(parents=True)
    (report_dir / "raw_input.md").write_text("生成文件\n", encoding="utf-8")
    (report_dir / "normalized_requirement.yaml").write_text(
        yaml.safe_dump(
            {
                "raw_summary": "生成文件",
                "user_goal": "Create a file",
                "real_goal": "Create a useful file",
                "success_definition": "A file exists",
                "source_type": "text",
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (report_dir / "task_graph.yaml").write_text(
        yaml.safe_dump(
            {
                "metadata": {"intent": "PLAN", "classification": {"intent": "PLAN", "reason": "test"}},
                "tasks": [
                    {
                        "id": "plan",
                        "title": "Plan the work",
                        "skill": "product",
                        "agent": "product",
                        "depends_on": [],
                        "expected_output": {"type": "report", "path": "artifacts/plan.md"},
                    }
                ],
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (report_dir / "agent_outputs" / "plan.md").write_text(
        "# Task Output: plan\n\n- Status: completed\n\n## Claude Response\n\nfinished\n",
        encoding="utf-8",
    )
    (report_dir / "artifacts" / "context_quality.json").write_text(
        json.dumps({"coverage": 0.8, "complete": True, "warnings": []}) + "\n",
        encoding="utf-8",
    )
    (report_dir / "artifacts" / "graph_quality.json").write_text(
        json.dumps({"passed": True, "warnings": []}) + "\n",
        encoding="utf-8",
    )
    if with_error:
        (report_dir / "artifacts" / "errors" / "backend.json").write_text(
            json.dumps(
                {
                    "task_id": "backend",
                    "stage": "worker",
                    "error_type": "unknown",
                    "message": "worker failed",
                    "recoverable": False,
                }
            )
            + "\n",
            encoding="utf-8",
        )
    return project, report_dir


def index_run(report_dir, project, run_id):
    RunIndex().upsert(
        {
            "run_id": run_id,
            "project": str(project),
            "report_dir": str(report_dir),
            "report_path": "",
            "status": "running",
        }
    )


def test_run_dir_with_agent_outputs_recovers_final_report(tmp_path, monkeypatch):
    _project, report_dir = write_recoverable_run(tmp_path, monkeypatch)

    final = recover_report(report_dir)

    assert final == report_dir / "final_report.md"
    content = final.read_text(encoding="utf-8")
    assert "# Local Engine Final Report" in content
    assert "Status: completed" in content
    assert "- plan" in content


def test_errors_make_recovered_status_partial_and_empty_deliverables_are_reported(tmp_path, monkeypatch):
    _project, report_dir = write_recoverable_run(tmp_path, monkeypatch, with_error=True)

    final = recover_report(report_dir)
    content = final.read_text(encoding="utf-8")

    assert detect_status(report_dir) == "partial"
    assert "Status: partial" in content
    assert "worker failed" in content
    assert "No deliverables generated." in content


def test_report_latest_auto_recovers_when_report_path_is_empty(tmp_path, monkeypatch):
    run_id = "2026-06-25-002"
    project, report_dir = write_recoverable_run(tmp_path, monkeypatch, run_id=run_id)
    index_run(report_dir, project, run_id)

    result = CliRunner().invoke(app, ["report", "--latest"])

    assert result.exit_code == 0, result.output
    assert "# Local Engine Final Report" in result.output
    record = RunIndex().get(run_id)
    assert record["report_path"] == str(report_dir / "final_report.md")
    assert record["completed_at"]


def test_runs_recover_run_id_updates_index(tmp_path, monkeypatch):
    run_id = "2026-06-25-004"
    project, report_dir = write_recoverable_run(tmp_path, monkeypatch, run_id=run_id)
    index_run(report_dir, project, run_id)

    result = CliRunner().invoke(app, ["runs", "recover", run_id])

    assert result.exit_code == 0, result.output
    assert "status: completed" in result.output
    assert RunIndex().get(run_id)["report_path"] == str(report_dir / "final_report.md")


def test_runs_recover_latest_updates_index(tmp_path, monkeypatch):
    run_id = "2026-06-25-003"
    project, report_dir = write_recoverable_run(tmp_path, monkeypatch, with_error=True, run_id=run_id)
    index_run(report_dir, project, run_id)

    result = CliRunner().invoke(app, ["runs", "recover", "--latest"])

    assert result.exit_code == 0, result.output
    assert "status: partial" in result.output
    record = RunIndex().get(run_id)
    assert record["status"] == "partial"
    assert record["report_path"] == str(report_dir / "final_report.md")
