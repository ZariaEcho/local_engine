"""Environment diagnostics for local_engine."""

from dataclasses import dataclass
from pathlib import Path
import shutil
import sys
from typing import List

from local_engine.runtime.config import ensure_engine_home, load_engine_config


@dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str


def run_doctor() -> List[DoctorCheck]:
    """Return non-destructive readiness checks for a future Claude-backed run."""
    home = ensure_engine_home()
    config = load_engine_config()
    command = config.get("claude_command", ["claude"])
    command = [command] if isinstance(command, str) else list(command or [])
    executable = str(command[0]) if command else ""
    command_path = Path(executable).expanduser()
    command_found = command_path.is_file() if command_path.parent != Path(".") else bool(shutil.which(executable))
    try:
        import rich  # noqa: F401

        rich_detail = "available"
        rich_status = "ok"
    except ImportError:
        rich_detail = "missing; install local-engine dependencies"
        rich_status = "error"
    checks = [
        DoctorCheck(
            "Python",
            "ok" if sys.version_info >= (3, 11) else "warn",
            "{0}.{1}.{2} (requires 3.11+)".format(*sys.version_info[:3]),
        ),
        DoctorCheck("Runtime home", "ok" if home.is_dir() else "error", str(home)),
        DoctorCheck("Configuration", "ok" if command else "error", "claude_command: {0}".format(" ".join(command) or "missing")),
        DoctorCheck("Rich", rich_status, rich_detail),
        DoctorCheck(
            "Claude CLI",
            "ok" if command_found else "warn",
            "{0} ({1})".format(executable or "missing", "available" if command_found else "not found"),
        ),
    ]
    return checks
