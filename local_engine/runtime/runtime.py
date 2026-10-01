"""Runtime-centered orchestration entry.

Phases 2–4: Runtime.execute owns plan → schedule → finalize. Engine is the
compatibility adapter for existing callers.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from local_engine.runtime.contracts import RuntimeInput, RuntimeOutput
from local_engine.runtime.phases.finalize import apply_existing_run, finalize_run
from local_engine.runtime.phases.plan import plan_run
from local_engine.runtime.phases.schedule import schedule_run
from local_engine.runtime.run_guard import indexed_run

if TYPE_CHECKING:
    from local_engine.runtime.artifact_applier import ApplyResult
    from local_engine.runtime.outcome import RunOutcome


class Runtime:
    """Contract entry for a single run."""

    def __init__(self, engine: Any = None) -> None:
        self.engine = engine

    @indexed_run
    def execute(self, runtime_input: RuntimeInput) -> RunOutcome:
        session = plan_run(runtime_input)
        schedule_run(session)
        return finalize_run(session)

    def run(self, runtime_input: RuntimeInput) -> RuntimeOutput:
        outcome = self.execute(runtime_input)
        return RuntimeOutput(
            run_id=outcome.run_id,
            status="failed" if outcome.has_failures else "completed",
            run_dir=outcome.report_dir,
            final_report_path=outcome.final_report,
            deliverables_dir=outcome.report_dir / "deliverables",
            state={},
            task_graph_status=outcome.task_graph_status,
            delivery_status=outcome.delivery_status,
            user_goal_satisfied=outcome.user_goal_satisfied,
        )

    def apply_run(
        self,
        project_root: Path,
        run_id: str,
        apply_approved: bool = False,
        verify: bool = True,
    ) -> ApplyResult:
        return apply_existing_run(project_root, run_id, apply_approved=apply_approved, verify=verify)
