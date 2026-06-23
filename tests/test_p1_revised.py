import json
from pathlib import Path

from typer.testing import CliRunner

from local_engine.cli import app
from local_engine.intents.registry import IntentRegistry
from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.engine import Engine
from local_engine.runtime.fallback import FallbackPolicy
from local_engine.runtime.retry import RetryPolicy, run_with_recovery
from local_engine.runtime.run_index import RunIndex
from local_engine.task_templates.registry import TaskTemplateRegistry


class QualityWorker:
    def __init__(self, confidence=0.9):
        self.confidence = confidence
        self.calls = []
        self.prompts = {}

    def run(self, prompt, task, project_root):
        self.calls.append((task["id"], task["skill"]))
        self.prompts[task["id"]] = prompt
        if task["skill"] == "review_code":
            return WorkerResult(raw="type: review\nconfidence: 1\nbody: 'VERDICT: PASS'\n")
        return WorkerResult(
            raw="type: report\nconfidence: {0}\nwarnings: []\nbody: completed\n".format(self.confidence)
        )


def initialized_project(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "project"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    Engine().initialize(project)
    return project


def test_run_index_json_cli_and_failed_finalization(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    worker = QualityWorker()
    outcome = Engine().run(project, "给我一个计划", worker_factory=lambda: worker)
    runs = tmp_path / "home" / "runs"
    record = json.loads((runs / (outcome.run_id + ".json")).read_text(encoding="utf-8"))

    assert outcome.run_id.endswith("-001")
    assert record["status"] == "completed"
    assert record["report_path"] == str(outcome.final_report)
    assert record["task_count"] == 4
    assert (runs / "index.json").is_file()
    assert json.loads((runs / "latest.json").read_text(encoding="utf-8"))["run_id"] == outcome.run_id

    runner = CliRunner()
    assert outcome.run_id in runner.invoke(app, ["runs", "list"]).output
    assert outcome.run_id in runner.invoke(app, ["runs", "latest"]).output
    assert record["project"] in runner.invoke(app, ["runs", "show", outcome.run_id]).output
    opened = []
    monkeypatch.setattr("local_engine.cli.webbrowser.open", lambda uri: opened.append(uri) or True)
    result = runner.invoke(app, ["runs", "open", outcome.run_id])
    assert result.exit_code == 0
    assert opened and opened[0].startswith("file:")

    result = runner.invoke(app, ["run", "--project", str(project), "--skill", "missing", "x"])
    assert result.exit_code == 1
    assert RunIndex().latest()["status"] == "failed"


def test_run_index_imports_legacy_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    legacy = tmp_path / "home" / "runs" / "legacy-id"
    legacy.mkdir(parents=True)
    report = tmp_path / "report.md"
    report.write_text("# old\n", encoding="utf-8")
    (legacy / "run_metadata.yaml").write_text(
        "run_id: legacy-id\nproject_root: /tmp/project\nstatus: completed\nfinal_report: {0}\nupdated_at: 2026-01-01T00:00:00Z\n".format(report),
        encoding="utf-8",
    )

    record = RunIndex().get("legacy-id")
    assert record is not None
    assert record["report_path"] == str(report)
    assert (tmp_path / "home" / "runs" / "legacy-id.json").is_file()


def test_all_builtin_intents_and_templates_are_yaml_backed():
    intents = IntentRegistry.load()
    templates = TaskTemplateRegistry.load()
    assert set(intents.names) == {"AUDIT", "PLAN", "LEARN", "BUILD", "TEST", "DOCUMENT", "REFACTOR", "RESEARCH"}
    for intent in intents:
        for name in intent.required_tasks + intent.optional_tasks:
            assert templates.get(name).name == name


def test_typed_recovery_routes_are_bounded_and_record_actions():
    network_calls = []

    def network(model, prompt, timeout=None):
        network_calls.append((model, prompt, timeout))
        if len(network_calls) < 3:
            return WorkerResult("offline", failed=True, error_message="network unavailable", failure_type="network")
        return WorkerResult("type: report\nbody: ok\n")

    recovered = run_with_recovery(network, "original", "task", "claude", RetryPolicy(2), FallbackPolicy("codex"), skill="scan")
    assert recovered.recovered
    assert [attempt.action for attempt in recovered.attempts[1:]] == ["retry_original_prompt", "retry_original_prompt"]

    format_calls = []

    def malformed(model, prompt, timeout=None):
        format_calls.append(prompt)
        return WorkerResult("free text") if len(format_calls) == 1 else WorkerResult("type: report\nbody: fixed\n")

    formatted = run_with_recovery(malformed, "original", "task", "claude", RetryPolicy(2), FallbackPolicy("codex"), skill="scan")
    assert formatted.attempts[1].failure_type == ""
    assert "Strict Format Recovery" in format_calls[1]

    timeout_values = []

    def timeout(model, prompt, timeout_seconds=None):
        timeout_values.append(timeout_seconds)
        if len(timeout_values) == 1:
            return WorkerResult("late", failed=True, failure_type="timeout")
        return WorkerResult("type: report\nbody: recovered\n")

    timed = run_with_recovery(timeout, "x" * 15000, "task", "claude", RetryPolicy(2, timeout_multiplier=2), FallbackPolicy("codex"), skill="scan", timeout_seconds=10)
    assert timed.recovered and timeout_values == [10, 20]

    logic_calls = []
    logic = run_with_recovery(
        lambda model, prompt, timeout=None: logic_calls.append(1) or WorkerResult("type: error\nfailure_type: logic\nbody: invalid conclusion\n"),
        "prompt",
        "task",
        "claude",
        RetryPolicy(5),
        FallbackPolicy("codex"),
        skill="scan",
    )
    assert logic.failure_type == "logic" and len(logic_calls) == 1

    unknown_calls = []
    unknown = run_with_recovery(
        lambda model, prompt, timeout=None: unknown_calls.append(1) or WorkerResult("boom", failed=True, failure_type="unknown"),
        "prompt",
        "task",
        "claude",
        RetryPolicy(5),
        FallbackPolicy("codex"),
        skill="scan",
    )
    assert unknown.failure_type == "unknown" and len(unknown_calls) == 2


def test_low_quality_patch_reaches_downstream_and_review_is_conditional(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    worker = QualityWorker(confidence=0.2)
    outcome = Engine().run(project, "给我一个计划", worker_factory=lambda: worker)

    assert (outcome.report_dir / "artifacts" / "quality" / "scan.json").is_file()
    assert (outcome.report_dir / "artifacts" / "context_patches" / "scan.md").is_file()
    assert "Context Patch: scan" in (outcome.report_dir / "prompts" / "plan.prompt.md").read_text(encoding="utf-8")
    quality = json.loads((outcome.report_dir / "artifacts" / "quality" / "scan.json").read_text(encoding="utf-8"))
    assert "low_confidence" in quality["triggers"]

    high = QualityWorker(confidence=0.95)
    second = Engine().run(project, "另一个计划", worker_factory=lambda: high)
    scan_review = (second.report_dir / "reviews" / "scan.round1.md").read_text(encoding="utf-8")
    assert "Status: skipped" in scan_review
    assert ("scan", "review_code") not in high.calls
    assert ("plan", "review_code") in high.calls  # architecture risk still requires review


def test_verified_cache_reuses_exact_and_unrelated_changes_but_revalidates_watched_files(tmp_path, monkeypatch):
    project = initialized_project(tmp_path, monkeypatch)
    first = QualityWorker()
    Engine().run(project, "给我一个计划", worker_factory=lambda: first)

    exact = QualityWorker()
    exact_outcome = Engine().run(project, "给我一个计划", worker_factory=lambda: exact)
    exact_manifest = (exact_outcome.report_dir / "artifacts" / "task_results.yaml").read_text(encoding="utf-8")
    assert "cache_action: reuse" in exact_manifest
    assert ("scan", "inspect_repo") not in exact.calls
    assert ("plan", "product") not in exact.calls

    (project / "notes.txt").write_text("unrelated\n", encoding="utf-8")
    unrelated = QualityWorker()
    Engine().run(project, "给我一个计划", worker_factory=lambda: unrelated)
    assert ("scan", "inspect_repo") not in unrelated.calls
    assert ("plan", "product") not in unrelated.calls

    (project / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    changed = QualityWorker()
    Engine().run(project, "给我一个计划", worker_factory=lambda: changed)
    assert ("scan", "inspect_repo") in changed.calls
    assert ("plan", "product") in changed.calls
    assert (project / ".local_engine" / "cache" / "repo_hash.json").is_file()
    assert (project / ".local_engine" / "cache" / "verified_tasks.json").is_file()
