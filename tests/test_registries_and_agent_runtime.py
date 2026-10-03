import json
import sys

from typer.testing import CliRunner

from local_engine.agents.registry import AgentRegistry
from local_engine.cli import app
from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.engine import Engine
from local_engine.skills.registry import SkillRegistry
from local_engine.skills.renderer import render_prompt


def test_default_agent_and_skill_registries_are_yaml_backed_and_render_prompts():
    agents = AgentRegistry.load()
    skills = SkillRegistry.load()

    backend = agents.get("backend")
    audit = skills.get("audit_repo")
    assert backend.model.primary == "claude"
    assert backend.model.fallback == ""
    assert audit.default_agent == "reviewer"
    assert "{{ task_title }}" not in render_prompt("Task: {{ task_title }}", {"task_title": "Audit"})


def test_registry_cli_commands_show_dynamic_definitions():
    runner = CliRunner()
    agents = runner.invoke(app, ["agents", "show", "backend"])
    skills = runner.invoke(app, ["skills", "show", "audit_repo"])

    assert agents.exit_code == 0, agents.output
    assert "name: backend" in agents.output
    assert "fallback: ''" in agents.output
    assert skills.exit_code == 0, skills.output
    assert "name: audit_repo" in skills.output
    assert "default_agent: reviewer" in skills.output


def test_direct_skill_run_renders_skill_prompt_and_persists_review_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine()
    engine.initialize(project)

    outcome = engine.run(project, "审计这个项目", skill="audit_repo", worker_factory=lambda: PassingWorker())

    prompt = (outcome.report_dir / "prompts" / "audit_repo.prompt.md").read_text(encoding="utf-8")
    assert "Audit the repository" in prompt
    assert (outcome.report_dir / "reviews" / "audit_repo.round1.md").is_file()
    assert (outcome.report_dir / "artifacts" / "retries" / "audit_repo.json").is_file()
    assert (outcome.report_dir / "artifacts" / "review_summary.json").is_file()
    assert (outcome.report_dir / "repo_context.json").is_file()
    final = outcome.final_report.read_text(encoding="utf-8")
    assert "Review Summary" in final
    assert "passed (1)" in final


def test_cli_run_accepts_selected_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    Engine().initialize(project)
    worker = tmp_path / "worker.py"
    worker.write_text("print('type: report\\nbody: cli skill output')\n", encoding="utf-8")
    (tmp_path / "global" / "config.yaml").write_text(
        json.dumps({"claude_command": [sys.executable, str(worker)]}), encoding="utf-8"
    )

    outcome = CliRunner().invoke(app, ["run", "--project", str(project), "--skill", "audit_repo", "审计这个项目"])

    assert outcome.exit_code == 0, outcome.output
    report_dir = next((project / ".local_engine" / "runs").iterdir())
    assert (report_dir / "deliverables" / "AUDIT_REPO.md").is_file()


class PassingWorker:
    def run(self, prompt, task, project_root):
        if task["skill"] == "review_code":
            return WorkerResult(raw="type: review\nbody: 'VERDICT: PASS'\n")
        return WorkerResult(raw="type: report\nbody: completed\n")


class FallbackWorker:
    def __init__(self, state, model):
        self.state = state
        self.model = model

    def run(self, prompt, task, project_root):
        self.state["calls"].append((task["id"], task["skill"], self.model))
        if task["id"] == "audit_repo" and task["skill"] == "audit_repo" and self.model == "claude":
            return WorkerResult(raw="simulated claude stderr", failed=True, error_message="Claude unavailable")
        if task["skill"] == "review_code":
            return WorkerResult(raw="type: review\nbody: 'VERDICT: PASS'\n")
        return WorkerResult(raw="type: report\nbody: recovered\n")


def test_primary_failure_retries_then_uses_fallback_and_writes_required_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine()
    engine.initialize(project)
    (tmp_path / "global" / "config.yaml").write_text(
        json.dumps({"execution": {"max_retries": 2, "fallback_model": "codex"}}),
        encoding="utf-8",
    )
    state = {"calls": []}

    outcome = engine.run(
        project,
        "审计这个项目",
        skill="audit_repo",
        worker_factory=lambda model: FallbackWorker(state, model),
    )

    retries = json.loads((outcome.report_dir / "artifacts" / "retries" / "audit_repo.json").read_text(encoding="utf-8"))
    assert [attempt["stage"] for attempt in retries["attempts"]][:4] == ["primary", "retry_1", "retry_2", "fallback_model"]
    assert (outcome.report_dir / "artifacts" / "errors" / "audit_repo.log").is_file()
    assert "simulated claude stderr" in (outcome.report_dir / "artifacts" / "errors" / "audit_repo.log").read_text(encoding="utf-8")
    assert any(model == "codex" for _, _, model in state["calls"])
    assert outcome.task_statuses["audit_repo"] == "completed"


class RevisionWorker:
    def __init__(self, state):
        self.state = state

    def run(self, prompt, task, project_root):
        if task["skill"] == "review_code":
            self.state["reviews"] += 1
            verdict = "FAIL\n- missing validation" if self.state["reviews"] == 1 else "PASS"
            return WorkerResult(raw="type: review\nbody: 'VERDICT: {0}'\n".format(verdict))
        if task["id"] == "audit_repo" and "# Required Revision" in prompt:
            self.state["revised"] = True
        return WorkerResult(raw="type: report\nbody: completed\n")


def test_failed_review_triggers_revision_then_records_second_review_round(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine()
    engine.initialize(project)
    state = {"reviews": 0, "revised": False}

    outcome = engine.run(project, "审计这个项目", skill="audit_repo", worker_factory=lambda: RevisionWorker(state))

    assert state["revised"]
    assert (outcome.report_dir / "reviews" / "audit_repo.round1.md").is_file()
    assert (outcome.report_dir / "reviews" / "audit_repo.round2.md").is_file()
    task_results = (outcome.report_dir / "artifacts" / "task_results.yaml").read_text(encoding="utf-8")
    assert "review_rounds: 2" in task_results
    assert "review_status: passed" in task_results
