import json

from local_engine.cli import RunProgress
from local_engine.context.project_type import detect_project_type
from local_engine.context.repo_scanner import scan_project
from local_engine.graph.dynamic_builder import build_graph
from local_engine.graph.graph_quality import GraphQualityError, graph_quality_check
from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.engine import Engine
from local_engine.runtime.retry import RetryPolicy, run_with_recovery
from local_engine.runtime.fallback import FallbackPolicy


def algorithm_project(tmp_path):
    project = tmp_path / "algorithms" / "graphs"
    project.mkdir(parents=True)
    (project / "bfs.py").write_text("def bfs(graph, start):\n    return [start]\n", encoding="utf-8")
    (project / "dfs.py").write_text("def dfs(graph, start):\n    return [start]\n", encoding="utf-8")
    (project / "README.md").write_text("# Graph Algorithms\n", encoding="utf-8")
    return project


def test_project_type_drives_algorithm_build_graph(tmp_path):
    project = algorithm_project(tmp_path)
    info = scan_project(project)
    requirement = {"raw_requirement": "生成多个详细的算法文件", "user_goal": "生成多个详细的算法文件"}
    project_type = detect_project_type(project, info, requirement)

    graph = build_graph("BUILD", "context", "run", requirement, project_type=project_type.to_dict())
    task_ids = {task["id"] for task in graph["tasks"]}
    rendered = json.dumps(graph, ensure_ascii=False)

    assert project_type.project_type == "algorithm_repository"
    assert {"generate_algorithm_files", "test_algorithm_files"}.issubset(task_ids)
    assert "backend" not in rendered
    assert "frontend" not in rendered
    assert graph_quality_check(graph, "BUILD").passed


def test_graph_quality_rejects_web_build_graph_for_algorithm_repository():
    graph = build_graph(
        "BUILD",
        "context",
        "run",
        {"raw_requirement": "生成多个详细的算法文件", "user_goal": "生成多个详细的算法文件"},
    )
    graph.setdefault("metadata", {})["project_type"] = {"project_type": "algorithm_repository"}
    graph["metadata"]["explicit_web_requested"] = False

    quality = graph_quality_check(graph, "BUILD")

    assert not quality.passed
    assert "Algorithm repository should not use generic web-app BUILD graph" in quality.errors[0]


def test_apply_mode_prompt_includes_write_approval_contract(tmp_path, monkeypatch):
    project = algorithm_project(tmp_path)
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    Engine().initialize(project)

    class PassingWorker:
        def run(self, prompt, task, project_root):
            return WorkerResult("type: report\nconfidence: 0.95\nbody: ok\n")

    outcome = Engine().run(
        project,
        "生成多个详细的算法文件",
        mode="apply",
        apply_approved=True,
        intent_override="BUILD",
        worker_factory=lambda: PassingWorker(),
    )
    prompt = (outcome.report_dir / "prompts" / "generate_algorithm_files.prompt.md").read_text(encoding="utf-8")
    project_type = json.loads((outcome.report_dir / "artifacts" / "project_type.json").read_text(encoding="utf-8"))

    assert "User write approval: granted" in prompt
    assert "Do not ask for additional permission" in prompt
    assert str(project) in prompt
    assert project_type["project_type"] == "algorithm_repository"


def test_permission_request_retries_with_execution_contract():
    calls = []

    def invoke(model, prompt, timeout=None):
        calls.append(prompt)
        if len(calls) == 1:
            return WorkerResult("请批准写入权限")
        return WorkerResult("type: report\nconfidence: 0.9\nbody: recovered\n")

    recovery = run_with_recovery(
        invoke,
        "original prompt",
        "generate_algorithm_files",
        "claude",
        RetryPolicy(2),
        FallbackPolicy(""),
        skill="generate_code",
        apply_approved=True,
        execution_contract="## Execution Contract\n\nMode: apply\nUser write approval: granted",
    )

    assert recovery.recovered
    assert recovery.attempts[0].failure_type == "permission_request"
    assert recovery.attempts[1].action == "retry_with_execution_contract"
    assert "User write approval: granted" in calls[1]


def test_progress_running_description_contains_observable_fields():
    progress = RunProgress()
    task_id = "generate_algorithm_files"
    progress.task_started_at[task_id] = 0.0
    progress.task_last_output_at[task_id] = 70.0
    progress.task_attempts[task_id] = (1, 2)
    progress.task_workers[task_id] = "claude"

    description = progress._running_description(task_id, "running")

    assert "task: generate_algorithm_files" in description
    assert "attempt: 1/2" in description
    assert "worker: claude" in description
    assert "last_output_age:" in description
