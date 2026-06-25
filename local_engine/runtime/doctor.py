"""Environment diagnostics for local_engine."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import sys
from typing import List, Optional

from local_engine.agents.registry import AgentRegistry
from local_engine.runtime.config import ensure_engine_home, load_engine_config
from local_engine.runtime.run_index import RunIndex
from local_engine.skills.registry import SkillRegistry


@dataclass
class DoctorCheck:
    name: str
    status: str
    detail: str


def run_doctor(project_root: Optional[Path] = None) -> List[DoctorCheck]:
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
    project = Path(project_root or Path.cwd()).expanduser().resolve()
    state = project / ".local_engine"
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
        DoctorCheck("Project path writable", "ok" if _is_writable(project) else "error", str(project)),
        DoctorCheck(
            ".local_engine writable",
            "ok" if _is_writable(state if state.exists() else project) else "error",
            str(state),
        ),
        _registry_check("Agents", AgentRegistry.load),
        _registry_check("Skills", SkillRegistry.load),
        DoctorCheck("Runs index writable", "ok" if _is_writable(home / "runs") else "error", str(home / "runs")),
        DoctorCheck("Cache writable", "ok" if _is_writable(home / "cache") else "error", str(home / "cache")),
    ]
    try:
        RunIndex().list()
        checks.append(DoctorCheck("Runs index loadable", "ok", str(home / "runs" / "index.json")))
    except Exception as exc:
        checks.append(DoctorCheck("Runs index loadable", "error", str(exc)))
    return checks


def _is_writable(path: Path) -> bool:
    """Check the nearest existing location without creating or changing user files."""
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.exists() and os.access(candidate, os.W_OK | os.X_OK)


def _registry_check(name: str, loader) -> DoctorCheck:
    try:
        registry = loader()
        return DoctorCheck("{0} loadable".format(name), "ok", "{0} definitions".format(len(registry.names)))
    except Exception as exc:
        return DoctorCheck("{0} loadable".format(name), "error", str(exc))
