import json
from pathlib import Path

from typer.testing import CliRunner

from local_engine.agents.registry import AgentRegistry
from local_engine.cli import app
from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.config import load_engine_config
from local_engine.runtime.contracts import ExecutorRequest, ExecutorResult, RuntimeInput, RuntimeOutput
from local_engine.runtime.engine import Engine
from local_engine.runtime.executor_manager import ExecutorManager, MockExecutor


class PassingWorker:
    def run(self, prompt, task, project_root):
        if task["skill"] == "review_code":
            return WorkerResult(
                raw=(
                    "type: review\nconfidence: 1\nfindings: [ok]\nrecommendations: [accept]\n"
                    "decisions: [pass]\nbody: 'VERDICT: PASS'\n"
                )
            )
        return WorkerResult(
            raw=(
                "type: report\nconfidence: 0.95\nfindings: [done]\nrecommendations: [ship]\n"
                "decisions: [recorded]\nbody: completed with enough detail for quality checks.\n"
            )
        )


class LoopWorker:
    def __init__(self):
        self.reviews = 0

    def run(self, prompt, task, project_root):
        if task["skill"] == "review_code":
            self.reviews += 1
            verdict = "FAIL\n- missing validation" if self.reviews == 1 else "PASS"
            return WorkerResult(raw="type: review\nbody: 'VERDICT: {0}'\n".format(verdict))
        return WorkerResult(
            raw=(
                "type: report\nconfidence: 0.95\nfindings: [done]\nrecommendations: [ship]\n"
                "decisions: [recorded]\nbody: loop-capable output with enough detail.\n"
            )
        )


def initialized_project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    Engine().initialize(project)
    return project


def test_p0_docs_and_runtime_contract_types_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs" / "ARCHITECTURE.md").is_file()
    assert (root / "docs" / "RUNTIME_CONTRACTS.md").is_file()
    assert (root / "docs" / "REFACTOR_BASELINE.md").is_file()

    request = ExecutorRequest("task", "prompt", root, root / ".local_engine" / "runs" / "run")
    result = ExecutorResult("task", "completed", raw_output="ok")
    runtime_input = RuntimeInput(project_root=root, raw_input="审计这个项目")
    runtime_output = RuntimeOutput("run", "completed", root, root / "final_report.md", root / "deliverables")

    assert request.task_id == result.task_id == "task"
    assert runtime_input.raw_input
    assert runtime_output.status == "completed"


def test_default_configuration_is_claude_only(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    config = load_engine_config()
    assert "codex" not in config.get("model_commands", {})
    assert config["execution"]["fallback_model"] is None
    assert config["execution"]["fallback_executor"] is None
    assert AgentRegistry.load().get("backend").model.fallback == ""


def test_new_run_dir_state_latest_status_resume_hooks_and_version(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    outcome = Engine().run(project, "审计这个项目", worker_factory=lambda: PassingWorker())

    assert outcome.report_dir.parent == project / ".local_engine" / "runs"
    assert (outcome.report_dir / "state.json").is_file()
    assert (project / ".local_engine" / "latest").exists()
    assert not (project / ".local_engine" / "task_reports" / outcome.run_id).exists()

    state = json.loads((outcome.report_dir / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["phase"] == "finished"
    assert state["tasks"]

    hooks = [
        json.loads(line)["event_type"]
        for line in (outcome.report_dir / "artifacts" / "hooks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {"before_run", "after_plan", "before_task", "after_task", "before_report", "after_report"}.issubset(set(hooks))

    runner = CliRunner()
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert "0.3.1" in version.output

    status = runner.invoke(app, ["status", "--project", str(project)])
    assert status.exit_code == 0, status.output
    assert outcome.run_id in status.output
    assert "run_dir:" in status.output

    resume = runner.invoke(app, ["resume", "--project", str(project)])
    assert resume.exit_code == 0, resume.output
    assert "status: completed" in resume.output
    assert "phase: finished" in resume.output


def test_loop_state_and_final_report_are_recorded_when_enabled(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    (tmp_path / "home" / "config.yaml").write_text(
        json.dumps({"loop": {"enabled": True, "max_rounds": 2}}),
        encoding="utf-8",
    )
    worker = LoopWorker()
    outcome = Engine().run(project, "审计这个项目", skill="audit_repo", worker_factory=lambda: worker)
    state = json.loads((outcome.report_dir / "state.json").read_text(encoding="utf-8"))

    assert state["loops"]["audit_repo"]["rounds"] >= 1
    assert state["loops"]["audit_repo"]["status"] == "passed"
    assert "Loop" in outcome.final_report.read_text(encoding="utf-8")


def test_telemetry_is_default_disabled_and_sanitized_when_enabled(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    Engine().run(project, "审计这个项目", skill="audit_repo", worker_factory=lambda: PassingWorker())
    assert not (project / ".local_engine" / "telemetry" / "events.jsonl").exists()

    (tmp_path / "home" / "config.yaml").write_text(
        json.dumps({"telemetry": {"enabled": True, "mode": "local_only", "anonymize": True}}),
        encoding="utf-8",
    )
    Engine().run(project, "不要采集这个原始需求", skill="audit_repo", worker_factory=lambda: PassingWorker())
    event = json.loads((project / ".local_engine" / "telemetry" / "events.jsonl").read_text(encoding="utf-8").splitlines()[-1])

    assert event["local_engine_version"] == "0.3.1"
    assert event["command_type"] == "run"
    forbidden = json.dumps(event, ensure_ascii=False)
    assert "不要采集这个原始需求" not in forbidden
    assert str(project) not in forbidden


def test_mock_executor_uses_executor_contract(tmp_path):
    executor = MockExecutor()
    request = ExecutorRequest(
        task_id="contract_task",
        prompt="prompt",
        project_root=tmp_path,
        run_dir=tmp_path,
        metadata={"task": {"id": "contract_task", "skill": "mock", "depends_on": []}},
    )
    result = executor.run(request)

    assert result.task_id == "contract_task"
    assert result.status == "completed"
    assert result.raw_output
    assert executor.healthcheck().status == "ok"


def test_executor_manager_worker_factory_is_default_execution_boundary(tmp_path):
    manager = ExecutorManager({"execution": {"default_executor": "mock"}, "timeout_seconds": 10})
    worker = manager.worker_factory()()
    result = worker.run("prompt", {"id": "contract_task", "skill": "mock", "depends_on": []}, tmp_path)

    assert result.failed is False
    assert "Mock output for contract_task" in result.raw
