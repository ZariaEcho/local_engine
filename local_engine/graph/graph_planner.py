"""Pre-scheduler planning of an untrusted requirement-driven task graph."""

from pathlib import Path
from typing import Any, Callable, Dict, List

import yaml

from local_engine.kernel.schemas import WorkerResult, make_error_sip
from local_engine.kernel.sip_parser import parse_sip
from local_engine.runtime.run_context import RunContext


class GraphPlanner:
    """Ask a worker for a graph candidate and preserve every planning artifact."""

    def __init__(self, worker_factory: Callable[[], Any]) -> None:
        self.worker_factory = worker_factory
        self.warnings: List[str] = []
        self.confidence = 0.0

    def plan(
        self,
        requirement: Dict[str, Any],
        project_context: str,
        project_memory: str,
        engine_memory: str,
        run_context: RunContext,
    ) -> Dict[str, Any]:
        """Return a candidate graph, or an empty mapping when planning cannot be used.

        Planning is deliberately failure-tolerant: caller-side fallback selection must
        remain possible after worker, parsing, or candidate-shape failures.
        """
        self.warnings = []
        self.confidence = 0.0
        prompt = self._render_prompt(requirement, project_context, project_memory, engine_memory, run_context.run_id)
        run_context.write_text("internal/graph_planner_prompt.md", prompt)
        task = {"id": "graph_planning", "title": "Plan a requirement-driven task graph", "skill": "graph_planner"}
        raw = ""
        try:
            worker_result = self.worker_factory().run(prompt, task, run_context.project_root)
            if not isinstance(worker_result, WorkerResult):
                worker_result = WorkerResult(raw=str(worker_result))
            raw = worker_result.raw or ""
            sip = (
                make_error_sip("graph_planner", "graph_planning", raw or worker_result.error_message)
                if worker_result.failed
                else parse_sip(raw, "graph_planner", "graph_planning")
            )
            if worker_result.failed:
                self.warnings.append("Graph Planner worker failed: {0}".format(worker_result.error_message or "unknown error"))
        except Exception as exc:
            raw = str(exc)
            sip = make_error_sip("graph_planner", "graph_planning", raw)
            self.warnings.append("Graph Planner worker raised an exception: {0}".format(exc))

        self.confidence = float(sip.get("confidence", 0.0) or 0.0)
        candidate = self._candidate_from_sip(sip)
        if not candidate:
            self.warnings.append("Graph Planner did not produce a usable task graph candidate.")
        run_context.write_text("internal/graph_planner.raw.txt", raw)
        run_context.write_yaml("internal/graph_planner.sip.yaml", sip)
        run_context.write_yaml("internal/graph_planner_candidates.yaml", candidate)
        return candidate

    @staticmethod
    def _candidate_from_sip(sip: Dict[str, Any]) -> Dict[str, Any]:
        if str(sip.get("type")) != "task_graph":
            return {}
        body = sip.get("body")
        if isinstance(body, dict):
            return body
        if not isinstance(body, str) or not body.strip():
            return {}
        try:
            candidate = yaml.safe_load(body)
        except yaml.YAMLError:
            return {}
        return candidate if isinstance(candidate, dict) else {}

    @staticmethod
    def _render_prompt(
        requirement: Dict[str, Any],
        project_context: str,
        project_memory: str,
        engine_memory: str,
        run_id: str,
    ) -> str:
        template = Path(__file__).with_name("graph_planner_prompt.md").read_text(encoding="utf-8")
        inputs = {
            "run_id": run_id,
            "normalized_requirement": requirement,
            "project_context": project_context or "No project context was recorded.",
            "project_memory": project_memory or "No project memory was recorded.",
            "engine_memory": engine_memory or "No engine memory was recorded.",
            "safety_rules": [
                "Use only paths relative to the run report.",
                "Patch outputs must be under patches/.",
                "Do not generate destructive or out-of-project actions.",
            ],
        }
        return "{0}\n\n# Inputs\n\n{1}\n".format(
            template.rstrip(), yaml.safe_dump(inputs, sort_keys=False, allow_unicode=True)
        )
