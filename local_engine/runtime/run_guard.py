"""Best-effort run-index recovery around a Runtime execute call."""

from pathlib import Path
from typing import Any, Callable, Optional

from local_engine.artifacts.recover_report import detect_status, recover_report
from local_engine.runtime.errors import write_error_artifact
from local_engine.runtime.run_context import active_run_id, clear_active_run
from local_engine.runtime.run_index import RunIndex


def indexed_run(method: Callable[..., Any]) -> Callable[..., Any]:
    """Finalize a reserved run record if execution raises unexpectedly."""

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        clear_active_run()
        try:
            outcome = method(*args, **kwargs)
            clear_active_run()
            return outcome
        except KeyboardInterrupt:
            run_id = active_run_id()
            if run_id:
                try:
                    finalize_recoverable_run(run_id, "KeyboardInterrupt", forced_status="interrupted")
                finally:
                    clear_active_run()
            raise
        except Exception as exc:
            run_id = active_run_id()
            if run_id:
                try:
                    finalize_recoverable_run(run_id, str(exc))
                finally:
                    clear_active_run()
            raise

    return wrapped


def finalize_recoverable_run(run_id: str, error: str = "", forced_status: Optional[str] = None) -> None:
    """Best-effort report recovery used when the runtime exits before normal finalization."""
    index = RunIndex()
    record = index.get(run_id) or {}
    report_dir_text = str(record.get("report_dir", "")).strip()
    report_dir = Path(report_dir_text).expanduser() if report_dir_text else None
    if report_dir is None or not report_dir.is_dir():
        index.finalize(run_id, status=forced_status or "failed", error=error)
        return

    final_report = report_dir / "final_report.md"
    if forced_status == "interrupted":
        write_error_artifact(
            report_dir / "artifacts",
            "interrupted",
            "context",
            "KeyboardInterrupt",
            "Run interrupted by KeyboardInterrupt",
            False,
        )
    elif error and not final_report.is_file():
        write_error_artifact(report_dir / "artifacts", "run", "context", "RuntimeError", error, False)

    if not final_report.is_file():
        final_report = recover_report(report_dir)
    status = forced_status or detect_status(report_dir)
    index.finalize(run_id, status=status, report_path=final_report, report_dir=str(report_dir), error=error)
