import json

from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.doctor import run_doctor
from local_engine.runtime.engine import Engine


class FailingWorker:
    def run(self, prompt, task, project_root):
        return WorkerResult(
            raw="network unavailable",
            failed=True,
            error_message="network unavailable",
            failure_type="network",
        )


def test_error_artifacts_have_the_standard_shape_and_warnings_reach_final_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    engine = Engine()
    engine.initialize(project)

    outcome = engine.run(project, "审计这个项目", worker_factory=lambda: FailingWorker())

    error = json.loads((outcome.report_dir / "artifacts" / "errors" / "scan.json").read_text(encoding="utf-8"))
    assert {"task_id", "stage", "error_type", "message", "recoverable"}.issubset(error)
    assert error["stage"] == "worker"
    final_report = outcome.final_report.read_text(encoding="utf-8")
    assert "retry warning" in final_report
    assert "failed task" in final_report


def test_doctor_checks_runtime_and_project_control_points(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()

    checks = {check.name: check for check in run_doctor(project)}

    for name in (
        "Python",
        "Claude CLI",
        "Project path writable",
        ".local_engine writable",
        "Agents loadable",
        "Skills loadable",
        "Runs index writable",
        "Cache writable",
    ):
        assert name in checks
