import threading
import time

from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.engine import Engine
from local_engine.scheduler.parallel_scheduler import ParallelScheduler
from local_engine.workers.mock_worker import MockWorker


def test_init_creates_project_and_global_state(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    state = Engine().initialize(project)
    assert (state / "project.yaml").is_file()
    assert (state / "context.md").is_file()
    assert (state / "memory.md").is_file()
    assert (state / "runs").is_dir()
    assert (state / "artifacts").is_dir()
    assert (tmp_path / "global" / "config.yaml").is_file()
    assert (tmp_path / "global" / "memory" / "experience_memory.md").is_file()


def test_plan_run_preserves_malformed_output_and_completes_dependents(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    source = project / "app.txt"
    source.write_text("original", encoding="utf-8")
    engine = Engine()
    engine.initialize(project)
    mock = MockWorker({"scan": "A useful natural-language scan without SIP."})
    outcome = engine.run(
        project, "Improve the app", mode="plan", workers=4, worker_factory=lambda: mock, intent_override="PLAN"
    )
    report = outcome.report_dir
    assert (report / "task_graph.yaml").is_file()
    assert len(list((report / "prompts").glob("*.prompt.md"))) == 4
    assert len(list((report / "agent_outputs").glob("*.raw.txt"))) == 4
    assert len(list((report / "agent_outputs").glob("*.sip.yaml"))) == 4
    assert len(list((report / "agent_outputs").glob("*.md"))) == 4
    assert (report / "agent_outputs" / "scan.md").is_file()
    assert "type: unstructured" in (report / "agent_outputs" / "scan.sip.yaml").read_text(encoding="utf-8")
    assert "Dependency Warning" in (report / "prompts" / "plan.prompt.md").read_text(encoding="utf-8")
    assert "unstructured" in outcome.final_report.read_text(encoding="utf-8")
    assert source.read_text(encoding="utf-8") == "original"


def test_run_invokes_one_worker_per_dynamic_task_and_persists_each_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine()
    engine.initialize(project)
    worker = MockWorker()

    outcome = engine.run(project, "审计这个项目", worker_factory=lambda: worker)

    task_ids = set(outcome.task_statuses)
    assert set(worker.prompts) == task_ids
    assert {path.stem.replace(".prompt", "") for path in (outcome.report_dir / "prompts").glob("*.prompt.md")} == task_ids
    assert {path.stem for path in (outcome.report_dir / "agent_outputs").glob("*.md")} == task_ids


class FailingClaudeWorker:
    def run(self, prompt, task, project_root):
        if task["id"] == "scan":
            return WorkerResult(raw="simulated Claude outage", failed=True, error_message="Claude CLI exited with code 17")
        return WorkerResult(raw="type: report\nbody: completed despite an upstream failure\n")


def test_run_persists_claude_failure_in_error_log_task_output_and_final_report(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine()
    engine.initialize(project)
    outcome = engine.run(project, "审计这个项目", worker_factory=FailingClaudeWorker)

    assert outcome.has_failures
    assert outcome.task_statuses["scan"] == "failed_but_continued"
    assert outcome.error_log == outcome.report_dir / "error.log"
    assert outcome.error_log.is_file()
    assert "Claude CLI exited with code 17" in outcome.error_log.read_text(encoding="utf-8")
    assert "Claude CLI failure: yes" in (outcome.report_dir / "agent_outputs" / "scan.md").read_text(encoding="utf-8")
    final = outcome.final_report.read_text(encoding="utf-8")
    assert "`scan`: Claude CLI exited with code 17" in final
    assert "Full failure evidence: `error.log`" in final
    assert (outcome.report_dir / "deliverables" / "FINAL_DELIVERY.md").is_file()
    assert (outcome.report_dir / "artifacts" / "task_results.yaml").is_file()
    assert (outcome.report_dir / "artifacts" / "execution_manifest.yaml").is_file()


def test_run_accepts_markdown_input_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    brief = tmp_path / "brief.md"
    brief.write_text("# Improve the checkout\n\nKeep the interface accessible.", encoding="utf-8")
    engine = Engine()
    engine.initialize(project)
    outcome = engine.run(project, "", input_file=brief, worker_factory=lambda: MockWorker(), intent_override="PLAN")
    normalized = (outcome.report_dir / "normalized_requirement.yaml").read_text(encoding="utf-8")
    assert "source_type: file" in normalized
    assert "Improve the checkout" in normalized


class TrackingWorker:
    def __init__(self, state):
        self.state = state

    def run(self, prompt, task, project_root):
        with self.state["lock"]:
            self.state["active"] += 1
            self.state["max_active"] = max(self.state["max_active"], self.state["active"])
        time.sleep(0.04)
        with self.state["lock"]:
            self.state["active"] -= 1
        return WorkerResult(raw="type: plan\nbody: finished\n")


def test_scheduler_runs_independent_tasks_in_parallel(tmp_path):
    graph = {
        "tasks": [
            {
                "id": "alpha",
                "title": "Independent work alpha",
                "skill": "researcher",
                "depends_on": [],
                "expected_output": {"type": "report", "path": "artifacts/alpha.md"},
            },
            {
                "id": "beta",
                "title": "Independent work beta",
                "skill": "writer",
                "depends_on": [],
                "expected_output": {"type": "doc", "path": "deliverables/beta.md"},
            },
        ]
    }
    state = {"lock": threading.Lock(), "active": 0, "max_active": 0}
    output_dir = tmp_path / "outputs"
    results = ParallelScheduler(4).run(
        graph,
        lambda task, dependencies: "prompt for {0}".format(task["id"]),
        lambda: TrackingWorker(state),
        tmp_path,
        output_dir,
    )
    assert len(results) == 2
    assert state["max_active"] >= 2
    assert (output_dir / "beta.sip.yaml").is_file()
