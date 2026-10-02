import threading
import time

import pytest
import yaml

from local_engine.compiler.prompt_compiler import compile_task_prompt
from local_engine.graph.graph_repair import repair_graph
from local_engine.graph.graph_validator import validate_graph
from local_engine.kernel.schemas import TaskResult, WorkerResult
from local_engine.runtime.engine import Engine
from local_engine.scheduler.parallel_scheduler import ParallelScheduler
from local_engine.workers.mock_worker import MockWorker


def requirement():
    return {
        "source_type": "text",
        "raw_summary": "Generate a short video script without changing code.",
        "user_goal": "Generate a short video script without changing code.",
        "real_goal": "Deliver a reviewed short video script.",
        "success_definition": "A reviewable short video script is available.",
    }


def dynamic_graph(run_id="run_dynamic"):
    return {
        "run_id": run_id,
        "requirement": {"source_type": "text", "raw_summary": requirement()["raw_summary"]},
        "goal": {
            "user_goal": requirement()["user_goal"],
            "real_goal": requirement()["real_goal"],
            "success_definition": requirement()["success_definition"],
        },
        "tasks": [
            {
                "id": "source_research",
                "title": "Research audience references",
                "skill": "researcher",
                "depends_on": [],
                "can_parallel": True,
                "expected_output": {"type": "research", "path": "artifacts/research.md"},
                "constraints": {"must": ["Use supplied context only."], "must_not": []},
            },
            {
                "id": "script_writer",
                "title": "Write the short video script",
                "skill": "writer",
                "depends_on": ["source_research"],
                "can_parallel": False,
                "expected_output": {"type": "doc", "path": "deliverables/video_script.md"},
                "constraints": {"must": ["Keep the script under 60 seconds."], "must_not": ["Do not modify code."]},
            },
            {
                "id": "quality_review",
                "title": "Review the script",
                "skill": "reviewer",
                "depends_on": ["script_writer"],
                "can_parallel": False,
                "expected_output": {"type": "review", "path": "artifacts/quality_review.md"},
                "constraints": {"must": [], "must_not": []},
            },
            {
                "id": "integration_review",
                "title": "Summarize the delivery",
                "skill": "integrator",
                "depends_on": ["quality_review"],
                "can_parallel": False,
                "expected_output": {"type": "review", "path": "integration_review.md"},
                "constraints": {"must": [], "must_not": []},
            },
            {
                "id": "memory_update",
                "title": "Record useful decisions",
                "skill": "memory_manager",
                "depends_on": ["integration_review"],
                "can_parallel": False,
                "expected_output": {"type": "memory_update", "path": "memory_update.md"},
                "constraints": {"must": [], "must_not": []},
            },
        ],
        "eval": {"checklist": ["Script is present."]},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda graph: graph["tasks"][0].pop("title"),
        lambda graph: graph["tasks"][0].update(skill="not_a_skill"),
        lambda graph: graph["tasks"][1].update(id="source_research"),
        lambda graph: graph["tasks"][1].update(depends_on=["missing_task"]),
        lambda graph: graph["tasks"][0].update(depends_on=["script_writer"]),
        lambda graph: graph["tasks"].pop(),
        lambda graph: graph.__setitem__("tasks", [task for task in graph["tasks"] if task["id"] != "memory_update"]),
        lambda graph: graph["tasks"][0]["expected_output"].update(path="/tmp/report.md"),
        lambda graph: graph["tasks"][0]["expected_output"].update(path="artifacts/../report.md"),
        lambda graph: graph["tasks"][0]["expected_output"].update(type="patch", path="artifacts/not_a_patch.md"),
    ],
)
def test_graph_validator_rejects_invalid_candidates(mutate):
    graph = dynamic_graph()
    mutate(graph)
    with pytest.raises(ValueError):
        validate_graph(graph)


def test_graph_validator_accepts_valid_dynamic_graph():
    validate_graph(dynamic_graph())


def test_graph_repair_fills_omissions_repairs_paths_skills_and_cycles():
    graph = dynamic_graph()
    graph.pop("run_id")
    graph.pop("requirement")
    graph.pop("goal")
    graph["tasks"][0].pop("depends_on")
    graph["tasks"][0].pop("expected_output")
    graph["tasks"][1]["skill"] = "unsupported"
    graph["tasks"][1]["expected_output"]["path"] = "/unsafe/path.md"
    graph["tasks"][0]["expected_output"] = {"type": "patch", "path": "artifacts/not_a_patch.md"}
    graph["tasks"][0]["depends_on"] = ["script_writer"]
    graph["tasks"] = [task for task in graph["tasks"] if task["id"] not in {"integration_review", "memory_update"}]
    repaired = repair_graph(graph, "repaired_run", requirement())
    assert repaired.graph is not None
    validate_graph(repaired.graph)
    assert repaired.graph["run_id"] == "repaired_run"
    assert any(task["skill"] == "integrator" for task in repaired.graph["tasks"])
    assert any(task["skill"] == "memory_manager" for task in repaired.graph["tasks"])
    assert repaired.graph["tasks"][0]["expected_output"]["path"] == "patches/source_research.patch"
    assert any("cyclic dependency" in warning for warning in repaired.warnings)
    assert "Graph Repair Report" in repaired.report


def test_prompt_compiler_uses_generic_guidance_dependencies_metadata_and_constraints():
    task = {
        "id": "mystery_task",
        "title": "Handle a specialist request",
        "skill": "unknown_skill",
        "expected_output": {"type": "analysis", "path": "artifacts/mystery.md"},
        "constraints": {"must": ["Include a decision table."], "must_not": ["Do not change code."]},
    }
    upstream = TaskResult("upstream", "free text", {"type": "unstructured", "body": "free text"}, status="warning")
    prompt = compile_task_prompt(task, requirement(), "project", "project memory", "engine memory", {"upstream": upstream}, "plan", {"graph_source": "dynamic"})
    assert "Use disciplined reasoning" in prompt
    assert "upstream:" in prompt
    assert "graph_source: dynamic" in prompt
    assert "# Repository Summary" in prompt
    assert "Include a decision table." in prompt
    assert "Dependency Warning" in prompt


class OrderingWorker:
    def __init__(self, state, malformed=False):
        self.state = state
        self.malformed = malformed

    def run(self, prompt, task, project_root):
        with self.state["lock"]:
            self.state["active"] += 1
            self.state["max_active"] = max(self.state["max_active"], self.state["active"])
            self.state["order"].append(task["id"])
        time.sleep(0.02)
        with self.state["lock"]:
            self.state["active"] -= 1
        if self.malformed and task["id"] == "source_research":
            return WorkerResult(raw="unstructured but useful upstream text")
        return WorkerResult(raw="type: report\nbody: complete\n")


def test_scheduler_runs_dynamic_dag_in_order_and_parallel(tmp_path):
    graph = dynamic_graph()
    graph["tasks"].insert(
        1,
        {
            "id": "audience_research",
            "title": "Research the audience",
            "skill": "researcher",
            "depends_on": [],
            "can_parallel": True,
            "expected_output": {"type": "research", "path": "artifacts/audience.md"},
        },
    )
    state = {"lock": threading.Lock(), "active": 0, "max_active": 0, "order": []}
    results = ParallelScheduler(4).run(graph, lambda task, dependencies: "prompt", lambda: OrderingWorker(state), tmp_path, tmp_path / "outputs")
    assert state["max_active"] >= 2
    assert state["order"].index("source_research") < state["order"].index("script_writer")
    assert results["memory_update"].status == "completed"


def test_scheduler_releases_downstream_after_upstream_warning(tmp_path):
    graph = dynamic_graph()
    seen_prompts = {}
    state = {"lock": threading.Lock(), "active": 0, "max_active": 0, "order": []}

    def prompt(task, dependencies):
        value = "warning from upstream" if dependencies and any(result.status == "warning" for result in dependencies.values()) else "clean"
        seen_prompts[task["id"]] = value
        return value

    results = ParallelScheduler(2).run(graph, prompt, lambda: OrderingWorker(state, malformed=True), tmp_path, tmp_path / "outputs")
    assert results["source_research"].status == "warning"
    assert results["script_writer"].status == "completed"
    assert seen_prompts["script_writer"] == "warning from upstream"


def test_engine_uses_context_intent_graph_and_writes_report_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "global"))
    project = tmp_path / "project"
    project.mkdir()
    engine = Engine()
    engine.initialize(project)
    mock = MockWorker()
    outcome = engine.run(project, "审计这个项目", worker_factory=lambda: mock)
    report = outcome.report_dir
    graph = yaml.safe_load((report / "task_graph.yaml").read_text(encoding="utf-8"))
    assert graph["run_id"] == outcome.run_id
    assert graph["metadata"]["graph_source"] == "dynamic"
    assert graph["metadata"]["intent"] == "AUDIT"
    assert {task["skill"] for task in graph["tasks"]}.isdisjoint({"backend", "frontend", "tester"})
    assert (report / "deliverables" / "AUDIT_REPORT.md").is_file()
    assert (report / "internal" / "repo_map.yaml").is_file()
    assert (report / "internal" / "PROJECT_CONTEXT.md").is_file()
    assert (report / "internal" / "intent.txt").read_text(encoding="utf-8").strip() == "AUDIT"
    assert (project / ".local_engine" / "artifacts" / "audit" / "AUDIT_REPORT.md").is_file()
    final = outcome.final_report.read_text(encoding="utf-8")
    assert "## Graph Source\ndynamic" in final
    assert "## Deliverables" in final
