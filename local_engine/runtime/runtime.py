"""Compatibility Runtime facade.

The heavy orchestration still lives behind `Engine` during this staged refactor,
but new code should target the Runtime contracts in this package.
"""

from typing import Any

from local_engine.runtime.contracts import RuntimeInput, RuntimeOutput


class Runtime:
    """Thin facade for future Runtime-centered orchestration."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine

    def run(self, runtime_input: RuntimeInput) -> RuntimeOutput:
        outcome = self.engine.run(
            runtime_input.project_root,
            runtime_input.raw_input,
            worker_factory=None,
        )
        return RuntimeOutput(
            run_id=outcome.run_id,
            status="failed" if outcome.has_failures else "completed",
            run_dir=outcome.report_dir,
            final_report_path=outcome.final_report,
            deliverables_dir=outcome.report_dir / "deliverables",
            state={},
        )

