import json
import textwrap

import yaml
from typer.testing import CliRunner

from local_engine.cli import app
from local_engine.kernel.schemas import WorkerResult
from local_engine.runtime.artifact_applier import ARTIFACT_PROTOCOL_VERSION, ArtifactApplier
from local_engine.runtime.engine import Engine


def algorithm_project(tmp_path):
    project = tmp_path / "graphs"
    project.mkdir()
    (project / "bfs.py").write_text("def bfs(graph, start):\n    return [start]\n", encoding="utf-8")
    (project / "dfs.py").write_text("def dfs(graph, start):\n    return [start]\n", encoding="utf-8")
    (project / "README.md").write_text("# Graph Algorithms\n", encoding="utf-8")
    return project


def write_run_with_file_block(project):
    run_dir = project / ".local_engine" / "runs" / "2026-06-26-001"
    (run_dir / "agent_outputs").mkdir(parents=True)
    (run_dir / "patches").mkdir()
    (run_dir / "artifacts").mkdir()
    graph = {
        "tasks": [
            {
                "id": "generate_algorithm_files",
                "title": "Generate algorithm implementation files",
                "task_type": "code_generation",
                "skill": "generate_code",
                "risk_tags": ["code_change"],
                "expected_output": {"type": "patch", "path": "patches/ALGORITHM_FILES.patch"},
            }
        ]
    }
    (run_dir / "task_graph.yaml").write_text(yaml.safe_dump(graph, sort_keys=False), encoding="utf-8")
    body = textwrap.dedent(
        """
        ### graph_utils.py
        ```python
        def add_edge(graph, u, v, directed=False):
            graph.setdefault(u, []).append(v)
            if not directed:
                graph.setdefault(v, []).append(u)
            return graph
        ```
        """
    ).strip()
    sip = {
        "type": "patch",
        "confidence": 0.95,
        "body": body,
        "warnings": [],
        "artifacts": [],
    }
    (run_dir / "agent_outputs" / "generate_algorithm_files.sip.yaml").write_text(
        yaml.safe_dump(sip, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (run_dir / "agent_outputs" / "generate_algorithm_files.raw.txt").write_text(
        yaml.safe_dump(sip, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return run_dir


def test_artifact_applier_extracts_file_blocks_and_writes_manifest(tmp_path):
    project = algorithm_project(tmp_path)
    run_dir = write_run_with_file_block(project)

    applier = ArtifactApplier(project, run_dir)
    dry_run = applier.dry_run()

    assert dry_run.delivery_status == "artifacts_generated"
    assert dry_run.files_not_applied == ["graph_utils.py"]
    assert not (project / "graph_utils.py").exists()

    result = applier.apply(approved=True, mode="apply", verify=False)

    assert result.delivery_status == "applied"
    assert result.files_created == ["graph_utils.py"]
    assert result.files_not_applied == []
    assert (project / "graph_utils.py").read_text(encoding="utf-8").startswith("def add_edge")
    manifest = json.loads((run_dir / "apply_manifest.json").read_text(encoding="utf-8"))
    assert manifest["delivery_status"] == "applied"
    assert manifest["files_created"] == ["graph_utils.py"]


def test_artifact_applier_prefers_explicit_artifact_protocol(tmp_path):
    project = algorithm_project(tmp_path)
    run_dir = write_run_with_file_block(project)
    sip_path = run_dir / "agent_outputs" / "generate_algorithm_files.sip.yaml"
    sip = yaml.safe_load(sip_path.read_text(encoding="utf-8"))
    sip["artifact_protocol"] = ARTIFACT_PROTOCOL_VERSION
    sip["artifacts"] = [
        {
            "type": "file",
            "path": "graph_utils.py",
            "content": "def explicit_protocol():\n    return True\n",
        }
    ]
    sip_path.write_text(yaml.safe_dump(sip, sort_keys=False, allow_unicode=True), encoding="utf-8")

    result = ArtifactApplier(project, run_dir).apply(approved=True, mode="apply", verify=False)

    assert result.delivery_status == "applied"
    assert (project / "graph_utils.py").read_text(encoding="utf-8") == "def explicit_protocol():\n    return True\n"
    manifest = json.loads((run_dir / "apply_manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact_protocol"] == ARTIFACT_PROTOCOL_VERSION


class AlgorithmFileWorker:
    def run(self, prompt, task, project_root):
        if task["skill"] == "review_code":
            return WorkerResult("type: review\nconfidence: 0.95\nbody: 'VERDICT: PASS'\n")
        if task["id"] == "generate_algorithm_files":
            return WorkerResult(
                textwrap.dedent(
                    """
                    type: patch
                    confidence: 0.95
                    body: |
                      ### graph_utils.py
                      ```python
                      def add_edge(graph, u, v, directed=False):
                          graph.setdefault(u, []).append(v)
                          if not directed:
                              graph.setdefault(v, []).append(u)
                          return graph
                      ```
                    """
                )
            )
        if task["id"] == "test_algorithm_files":
            return WorkerResult(
                textwrap.dedent(
                    """
                    type: patch
                    confidence: 0.95
                    body: |
                      ### test_algorithms.py
                      ```python
                      from graph_utils import add_edge


                      def test_add_edge_undirected():
                          graph = {}
                          add_edge(graph, "a", "b")
                          assert graph == {"a": ["b"], "b": ["a"]}
                      ```
                    """
                )
            )
        return WorkerResult("type: report\nconfidence: 0.95\nbody: ok\n")


def test_engine_apply_run_writes_generated_build_files_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = algorithm_project(tmp_path)
    engine = Engine()
    engine.initialize(project)

    outcome = engine.run(
        project,
        "生成多个算法文件",
        mode="apply",
        apply_approved=True,
        intent_override="BUILD",
        worker_factory=lambda: AlgorithmFileWorker(),
    )

    assert outcome.user_goal_satisfied
    assert outcome.delivery_status == "verified"
    assert (project / "graph_utils.py").is_file()
    assert (project / "test_algorithms.py").is_file()
    final = outcome.final_report.read_text(encoding="utf-8")
    assert "delivery_status: verified" in final
    assert "user_goal_satisfied: true" in final
    manifest = json.loads((outcome.report_dir / "apply_manifest.json").read_text(encoding="utf-8"))
    assert manifest["files_created"] == ["graph_utils.py", "test_algorithms.py"]


def test_cli_apply_resumes_plan_only_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = algorithm_project(tmp_path)
    engine = Engine()
    engine.initialize(project)
    outcome = engine.run(
        project,
        "生成多个算法文件",
        mode="plan",
        apply_approved=False,
        intent_override="BUILD",
        worker_factory=lambda: AlgorithmFileWorker(),
    )
    assert not (project / "graph_utils.py").exists()
    assert "delivery_status: plan_only" in outcome.final_report.read_text(encoding="utf-8")
    assert "user_goal_satisfied: false" in outcome.final_report.read_text(encoding="utf-8")

    result = CliRunner().invoke(app, ["apply", "--project", str(project), "--run-id", outcome.run_id, "--yes", "--no-verify"])

    assert result.exit_code == 0, result.output
    assert "delivery_status: applied" in result.output
    assert (project / "graph_utils.py").is_file()
    updated = outcome.final_report.read_text(encoding="utf-8")
    assert "delivery_status: applied" in updated
    assert "user_goal_satisfied: true" in updated
