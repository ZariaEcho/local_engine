import json
import sys

from typer.testing import CliRunner

from local_engine.cli import app
from local_engine.runtime.engine import Engine


def test_cli_smoke_run_creates_the_complete_run_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCAL_ENGINE_HOME", str(tmp_path / "home"))
    project = tmp_path / "demo"
    project.mkdir()
    (project / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (project / "tests").mkdir()
    (project / "tests" / "test_app.py").write_text("def test_value():\n    assert True\n", encoding="utf-8")
    Engine().initialize(project)
    worker = tmp_path / "worker.py"
    worker.write_text(
        "print('type: report\\nconfidence: 0.9\\nfindings: [smoke]\\nrecommendations: [review]\\nbody: smoke worker output')\n",
        encoding="utf-8",
    )
    (tmp_path / "home" / "config.yaml").write_text(
        json.dumps({"workers": 2, "claude_command": [sys.executable, str(worker)], "timeout_seconds": 10}),
        encoding="utf-8",
    )

    result = CliRunner().invoke(app, ["run", "--project", str(project), "审计这个项目"])

    assert result.exit_code == 0, result.output
    report = next((project / ".local_engine" / "runs").iterdir())
    for relative_path in (
        "raw_input.md",
        "normalized_requirement.yaml",
        "task_graph.yaml",
        "prompts",
        "agent_outputs",
        "artifacts/context_quality.json",
        "artifacts/quality",
        "deliverables",
        "final_report.md",
    ):
        assert (report / relative_path).exists(), relative_path
