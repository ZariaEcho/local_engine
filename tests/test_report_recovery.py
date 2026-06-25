import json

import yaml
from typer.testing import CliRunner

from local_engine.artifacts.recover_report import detect_status, recover_report
from local_engine.cli import app
from local_engine.runtime.run_index import RunIndex
from local_engine.runtime.run_store import create_run_dir
from local_engine.runtime.state import write_state


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


def write_task_results(report_dir, tasks):
    (report_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (report_dir / "artifacts" / "task_results.yaml").write_text(
        yaml.safe_dump(
            {
                "run_id": report_dir.name,
                "task_count": len(tasks),
                "tasks": tasks,
            },
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def task_record(task_id, status="completed", lifecycle_status="passed", worker_failed=False, **extra):
    payload = {
        "task_id": task_id,
        "status": status,
        "lifecycle_status": lifecycle_status,
        "worker_failed": worker_failed,
        "warnings": [],
        "quality_reasons": [],
    }
    payload.update(extra)
    return payload


def write_completed_project_run(tmp_path, monkeypatch, state_status="partial", state_phase="finished"):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    project_state = project / ".local_engine"
    run_dir = create_run_dir(project_state, "2026-06-25-005", project)
    tasks = [
        task_record("scan"),
        task_record("analyze"),
    ]
    write_task_results(run_dir, tasks)
    (run_dir / "final_report.md").write_text("# Final\n", encoding="utf-8")
    write_state(
        run_dir,
        {
            "run_id": run_dir.name,
            "status": state_status,
            "phase": state_phase,
            "run_dir": str(run_dir),
            "project_root": str(project),
            "tasks": {
                task["task_id"]: {
                    "status": task["status"],
                    "lifecycle_status": task["lifecycle_status"],
                    "worker_failed": task["worker_failed"],
                }
                for task in tasks
            },
            "artifacts": {"final_report": "final_report.md"},
        },
    )
    return project, run_dir


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


def test_completed_task_results_with_warnings_are_completed(tmp_path, monkeypatch):
    _project, report_dir = write_recoverable_run(tmp_path, monkeypatch)
    write_task_results(
        report_dir,
        [
            task_record(
                "scan",
                warnings=["worker warning"],
                quality_reasons=["worker_warning", "review_required", "risky_change"],
                quality_score=0.2,
            ),
            task_record(
                "analyze",
                warnings=["context coverage is low"],
                quality_reasons=["low_context_coverage"],
                quality_score=0.4,
            ),
        ],
    )
    (report_dir / "artifacts" / "errors" / "scan.json").write_text(
        json.dumps({"task_id": "scan", "message": "older diagnostic warning", "recoverable": True}) + "\n",
        encoding="utf-8",
    )

    assert detect_status(report_dir) == "completed"


def test_retry_history_warning_does_not_force_partial(tmp_path, monkeypatch):
    _project, report_dir = write_recoverable_run(tmp_path, monkeypatch)
    write_task_results(
        report_dir,
        [
            task_record(
                "scan",
                retry_history=[
                    {
                        "failed": True,
                        "stage": "worker",
                        "failure_type": "timeout",
                        "error_message": "first attempt timed out",
                    }
                ],
            )
        ],
    )
    (report_dir / "artifacts" / "retries" / "scan.json").write_text(
        json.dumps(
            {
                "task_id": "scan",
                "attempts": [{"failed": True, "error_message": "first attempt timed out"}],
                "recovered": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert detect_status(report_dir) == "completed"


def test_task_results_are_partial_only_for_real_failed_tasks(tmp_path, monkeypatch):
    _project, report_dir = write_recoverable_run(tmp_path, monkeypatch)
    write_task_results(
        report_dir,
        [
            task_record("scan"),
            task_record("analyze"),
            task_record("audit_report"),
            task_record("integration_review"),
            task_record("memory_update", status="failed", lifecycle_status="failed", worker_failed=True),
        ],
    )

    assert detect_status(report_dir) == "partial"


def test_task_results_are_failed_only_when_all_tasks_failed(tmp_path, monkeypatch):
    _project, report_dir = write_recoverable_run(tmp_path, monkeypatch)
    write_task_results(
        report_dir,
        [
            task_record("scan", status="failed", lifecycle_status="failed", worker_failed=True),
            task_record("analyze", status="error", lifecycle_status="error"),
            task_record("audit_report", status="cancelled", lifecycle_status="cancelled"),
        ],
    )

    assert detect_status(report_dir) == "failed"


def test_resume_completed_run_keeps_finished_phase(tmp_path, monkeypatch):
    project, run_dir = write_completed_project_run(tmp_path, monkeypatch, state_phase="resumed")

    result = CliRunner().invoke(app, ["resume", "--project", str(project)])

    assert result.exit_code == 0, result.output
    assert "status: completed" in result.output
    assert "phase: finished" in result.output
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["phase"] == "finished"


def test_status_uses_completed_task_results_over_stale_state(tmp_path, monkeypatch):
    project, _run_dir = write_completed_project_run(tmp_path, monkeypatch, state_status="partial", state_phase="finished")

    result = CliRunner().invoke(app, ["status", "--project", str(project)])

    assert result.exit_code == 0, result.output
    assert "status: completed" in result.output
    assert "status: partial" not in result.output
    assert "phase: finished" in result.output


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
