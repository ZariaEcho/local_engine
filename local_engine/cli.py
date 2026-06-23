"""Typer command-line interface for local_engine."""

from pathlib import Path
import json
from typing import Any, Dict, Optional
import webbrowser

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
import yaml

from local_engine.agents.registry import AgentRegistry
from local_engine.context.context_builder import list_modules
from local_engine.runtime.doctor import run_doctor
from local_engine.runtime.engine import Engine
from local_engine.runtime.reporting import resolve_report
from local_engine.runtime.run_index import RunIndex
from local_engine.skills.registry import SkillRegistry


app = typer.Typer(help="A local repository-aware Context Engine powered by Claude CLI workers.")
agents_app = typer.Typer(help="Inspect dynamically loaded agent definitions.")
skills_app = typer.Typer(help="Inspect dynamically loaded reusable skill definitions.")
runs_app = typer.Typer(help="Inspect and open globally indexed engine runs.")
app.add_typer(agents_app, name="agents")
app.add_typer(skills_app, name="skills")
app.add_typer(runs_app, name="runs")


def _print_registry_error(exc: Exception) -> None:
    typer.echo("Error: {0}".format(exc), err=True)
    raise typer.Exit(code=1)


@agents_app.command("list")
def agents_list() -> None:
    """List agent YAML definitions discovered from ``agents/``."""
    try:
        registry = AgentRegistry.load()
    except (FileNotFoundError, ValueError) as exc:
        _print_registry_error(exc)
    table = Table(title="local-engine agents")
    table.add_column("Name")
    table.add_column("Primary model")
    table.add_column("Skills")
    table.add_column("Description")
    for agent in registry:
        table.add_row(agent.name, agent.model.primary, ", ".join(agent.skills), agent.description)
    Console().print(table)


@agents_app.command("show")
def agents_show(name: str = typer.Argument(..., help="Agent name from agents/*.yaml.")) -> None:
    """Show one fully resolved agent definition."""
    try:
        typer.echo(yaml.safe_dump(AgentRegistry.load().get(name).to_dict(), sort_keys=False, allow_unicode=True))
    except (FileNotFoundError, ValueError, KeyError) as exc:
        _print_registry_error(exc)


@skills_app.command("list")
def skills_list() -> None:
    """List reusable skills discovered from ``skills/*/skill.yaml``."""
    try:
        registry = SkillRegistry.load()
    except (FileNotFoundError, ValueError) as exc:
        _print_registry_error(exc)
    table = Table(title="local-engine skills")
    table.add_column("Name")
    table.add_column("Default agent")
    table.add_column("Task type")
    table.add_column("Inputs")
    table.add_column("Outputs")
    table.add_column("Description")
    for skill in registry:
        table.add_row(skill.name, skill.default_agent, skill.task_type, ", ".join(skill.inputs), ", ".join(skill.outputs), skill.description)
    Console().print(table)


@skills_app.command("show")
def skills_show(name: str = typer.Argument(..., help="Skill name from skills/*/skill.yaml.")) -> None:
    """Show one fully resolved skill definition."""
    try:
        typer.echo(yaml.safe_dump(SkillRegistry.load().get(name).to_dict(), sort_keys=False, allow_unicode=True))
    except (FileNotFoundError, ValueError, KeyError) as exc:
        _print_registry_error(exc)


def _run_record_or_exit(run_id: Optional[str] = None, latest: bool = False) -> Dict[str, Any]:
    index = RunIndex()
    try:
        record = index.latest() if latest else index.get(str(run_id or ""))
    except ValueError as exc:
        typer.echo("Error: {0}".format(exc), err=True)
        raise typer.Exit(code=1)
    if record is None:
        typer.echo("Error: no matching run found", err=True)
        raise typer.Exit(code=1)
    return record


@runs_app.command("list")
def runs_list() -> None:
    """List indexed runs newest first."""
    table = Table(title="local-engine runs")
    table.add_column("Run ID")
    table.add_column("Status")
    table.add_column("Intent")
    table.add_column("Project")
    table.add_column("Passed/Failed")
    for record in RunIndex().list():
        table.add_row(
            str(record.get("run_id", "")),
            str(record.get("status", "")),
            str(record.get("intent", "")),
            str(record.get("project", "")),
            "{0}/{1}".format(record.get("passed_count", 0), record.get("failed_count", 0)),
        )
    Console().print(table)


@runs_app.command("latest")
def runs_latest() -> None:
    """Show the newest indexed run."""
    typer.echo(json.dumps(_run_record_or_exit(latest=True), ensure_ascii=False, indent=2))


@runs_app.command("show")
def runs_show(run_id: str = typer.Argument(..., help="Indexed run ID.")) -> None:
    """Show one full run record."""
    typer.echo(json.dumps(_run_record_or_exit(run_id=run_id), ensure_ascii=False, indent=2))


@runs_app.command("open")
def runs_open(run_id: str = typer.Argument(..., help="Indexed run ID.")) -> None:
    """Open a run's final Markdown report in the system default application."""
    record = _run_record_or_exit(run_id=run_id)
    path = Path(str(record.get("report_path", record.get("final_report", "")))).expanduser()
    if not path.is_file():
        typer.echo("Error: report does not exist: {0}".format(path), err=True)
        raise typer.Exit(code=1)
    try:
        opened = webbrowser.open(path.resolve().as_uri())
    except Exception as exc:
        typer.echo("Error: could not open {0}: {1}".format(path, exc), err=True)
        raise typer.Exit(code=1)
    if not opened:
        typer.echo("Error: no system handler opened {0}".format(path), err=True)
        raise typer.Exit(code=1)
    typer.echo(str(path.resolve()))


class RunProgress:
    """Render scheduler events as Rich task-level progress without affecting execution."""

    def __init__(self) -> None:
        self.console = Console(stderr=True)
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        )
        self.task_rows: Dict[str, int] = {}
        self.overall: Optional[int] = None

    def __enter__(self) -> "RunProgress":
        self.progress.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.progress.stop()

    def handle(self, event: str, payload: Dict[str, Any]) -> None:
        if event == "graph_ready":
            tasks = payload.get("tasks", [])
            self.overall = self.progress.add_task("[bold]Run task graph[/]", total=len(tasks))
            for task in tasks:
                task_id = task["id"]
                self.task_rows[task_id] = self.progress.add_task("[dim]{0}[/] pending".format(task_id), total=1)
            return
        task_id = payload.get("task_id")
        row = self.task_rows.get(task_id)
        if row is None:
            return
        if event == "task_attempt_failed":
            lifecycle = payload.get("lifecycle_status")
            if lifecycle:
                self.progress.update(row, description="[yellow]{0}[/] {1}".format(task_id, lifecycle))
            self.progress.console.print(
                "[bold red]Worker attempt failed[/] for {0} ({1}): {2}".format(
                    task_id, payload.get("stage", "attempt"), payload.get("error_message") or "unknown error"
                )
            )
            if payload.get("error_output"):
                self.progress.console.print("[red]{0}[/]".format(payload["error_output"]))
            return
        if event == "task_started":
            self.progress.update(row, description="[cyan]{0}[/] running".format(task_id))
        elif event == "task_finished":
            status = payload.get("lifecycle_status") or payload.get("status", "completed")
            if payload.get("cache_action") == "reuse":
                status = "skipped (cache reuse)"
            colour = "red" if payload.get("failed") else ("yellow" if status == "warning" else "green")
            self.progress.update(row, completed=1, description="[{0}]{1}[/{0}] {2}".format(colour, task_id, status))
            if self.overall is not None:
                self.progress.advance(self.overall)
            if payload.get("failed"):
                self.progress.console.print(
                    "[bold red]Claude CLI failed[/] for {0}: {1}".format(task_id, payload.get("error_message") or "unknown error")
                )
                if payload.get("error_output"):
                    self.progress.console.print("[red]{0}[/]".format(payload["error_output"]))


@app.command()
def init(
    project: Path = typer.Option(..., "--project", help="Existing project root to initialize."),
) -> None:
    """Create project-local and global local_engine state."""
    try:
        state = Engine().initialize(project)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo("Error: {0}".format(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo("Initialized {0}".format(state))


@app.command()
def scan(
    project: Path = typer.Option(..., "--project", help="Existing project root to scan."),
) -> None:
    """Scan a repository and write repo_map.json plus PROJECT_CONTEXT.md."""
    try:
        info = Engine().scan(project)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo("Error: {0}".format(exc), err=True)
        raise typer.Exit(code=1)
    root = project.expanduser().resolve() / ".local_engine"
    typer.echo("Scanned {0} supported files.".format(len(info.files)))
    typer.echo("repo_map.json: {0}".format(root / "cache" / "repo_map.json"))
    typer.echo("PROJECT_CONTEXT.md: {0}".format(root / "PROJECT_CONTEXT.md"))


@app.command()
def inspect(
    project: Path = typer.Option(..., "--project", help="Existing project root to inspect."),
) -> None:
    """Print languages, modules, dependencies, and directories from a fresh scan."""
    try:
        info = Engine().scan(project)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo("Error: {0}".format(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(_inspect_output(info.languages, list_modules(info), info.dependencies, info.directories))


@app.command(name="graph")
def graph_preview(
    task: str = typer.Argument("", help="Optional request used to classify the graph intent."),
    project: Path = typer.Option(..., "--project", help="Existing project root to inspect."),
) -> None:
    """Preview the deterministic Context Engine task graph without running workers."""
    try:
        graph = Engine().preview_graph(project, task)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo("Error: {0}".format(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(_graph_preview(graph))


@app.command()
def report(
    latest: bool = typer.Option(False, "--latest", help="Show the most recently completed run report."),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Show a specific run report."),
    project: Optional[Path] = typer.Option(None, "--project", help="Optional project scope for report lookup."),
) -> None:
    """Print a completed final report using the global run index or a project scope."""
    if not latest and not run_id:
        typer.echo("Error: provide --latest or --run-id", err=True)
        raise typer.Exit(code=1)
    metadata = resolve_report(latest=latest, project_root=project, run_id=run_id)
    if metadata is None:
        typer.echo("Error: no completed report found", err=True)
        raise typer.Exit(code=1)
    final_report = Path(metadata["final_report"])
    typer.echo("run_id: {0}".format(metadata["run_id"]))
    typer.echo("report_dir: {0}".format(metadata["report_dir"]))
    typer.echo("final_report: {0}".format(final_report))
    typer.echo("")
    typer.echo(final_report.read_text(encoding="utf-8"))


@app.command()
def doctor() -> None:
    """Check local runtime, Rich, configuration, and Claude CLI readiness."""
    table = Table(title="local-engine doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in run_doctor():
        colour = {"ok": "green", "warn": "yellow", "error": "red"}.get(check.status, "white")
        table.add_row(check.name, "[{0}]{1}[/{0}]".format(colour, check.status.upper()), check.detail)
    Console().print(table)


@app.command()
def run(
    task: str = typer.Argument("", help="Text requirement (optional when --input is supplied)."),
    project: Path = typer.Option(..., "--project", help="Initialized project root."),
    input: Optional[Path] = typer.Option(None, "--input", help="Markdown or TXT requirement file."),
    mode: str = typer.Option("plan", "--mode", help="plan (default) or apply."),
    workers: Optional[int] = typer.Option(None, "--workers", min=1, help="Maximum concurrent workers."),
    skill: Optional[str] = typer.Option(None, "--skill", help="Run one reusable skill instead of selecting an intent graph."),
    yes: bool = typer.Option(False, "--yes", help="Approve collected patches in apply mode."),
) -> None:
    """Classify, contextualize, and execute an intent-appropriate task graph."""
    if mode not in {"plan", "apply"}:
        typer.echo("Error: --mode must be plan or apply", err=True)
        raise typer.Exit(code=1)
    approved = yes
    if mode == "apply" and not approved:
        approved = typer.confirm("Apply patches generated by this run?")
        if not approved:
            typer.echo("Apply mode cancelled; no patches were applied.")
            raise typer.Exit(code=1)
    try:
        with RunProgress() as progress:
            outcome = Engine().run(
                project,
                task,
                input,
                mode,
                workers,
                apply_approved=approved,
                event_callback=progress.handle,
                skill=skill,
            )
    except (FileNotFoundError, ValueError, KeyError, PermissionError, RuntimeError) as exc:
        typer.echo("Error: {0}".format(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(_run_summary(outcome))
    if outcome.has_failures:
        raise typer.Exit(code=1)


def _inspect_output(languages, modules, dependencies, directories) -> str:
    def section(title, values):
        return "{0}:\n{1}".format(title, "\n".join(str(value) for value in values) if values else "None")

    return "\n\n".join(
        [section("Languages", languages), section("Modules", modules), section("Dependencies", dependencies), section("Directories", directories)]
    )


def _graph_preview(graph) -> str:
    tasks = graph["tasks"]
    children = {task["id"]: [] for task in tasks}
    for task in tasks:
        for dependency in task.get("depends_on", []):
            children.setdefault(dependency, []).append(task["id"])
    task_index = {task["id"]: task for task in tasks}
    roots = [task["id"] for task in tasks if not task.get("depends_on")]
    lines = ["Task Graph Preview ({0})".format(graph.get("metadata", {}).get("intent", "PLAN"))]

    visited = set()

    def render(task_id, prefix=""):
        if task_id in visited:
            return
        visited.add(task_id)
        task = task_index[task_id]
        lines.append("{0}- {1} [{2}]".format(prefix, task_id, task["skill"]))
        for child in children.get(task_id, []):
            render(child, prefix + "  ")

    for root in roots:
        render(root)
    return "\n".join(lines)


def _run_summary(outcome) -> str:
    lines = [
        "Run finished",
        "run_id: {0}".format(outcome.run_id),
        "report_dir: {0}".format(outcome.report_dir),
        "final_report: {0}".format(outcome.final_report),
        "task_status:",
    ]
    lines.extend("- {0}: {1}".format(task_id, status) for task_id, status in outcome.task_statuses.items())
    if outcome.error_log:
        lines.append("error_log: {0}".format(outcome.error_log))
    return "\n".join(lines)


if __name__ == "__main__":
    app()
