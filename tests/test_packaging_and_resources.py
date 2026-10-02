import zipfile

from local_engine.agents.registry import AgentRegistry, default_agents_dir
from local_engine.intents.registry import IntentRegistry, default_intents_dir
from local_engine.skills.registry import SkillRegistry, default_skills_dir
from local_engine.task_templates.registry import TaskTemplateRegistry, default_task_templates_dir
from scripts.package_release import build_zip, should_exclude


def test_builtin_registries_load_from_package_resources():
    assert "local_engine/resources/agents" in default_agents_dir().as_posix()
    assert "local_engine/resources/skills" in default_skills_dir().as_posix()
    assert "local_engine/resources/intents" in default_intents_dir().as_posix()
    assert "local_engine/resources/task_templates" in default_task_templates_dir().as_posix()

    assert "backend" in AgentRegistry.load().names
    assert "audit_repo" in SkillRegistry.load().names
    assert "AUDIT" in IntentRegistry.load().names
    assert "audit_scan" in TaskTemplateRegistry.load().names


def test_package_release_excludes_dev_and_runtime_trash(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nversion = '9.9.9'\n", encoding="utf-8")
    (root / "local_engine").mkdir()
    (root / "local_engine" / "__init__.py").write_text("", encoding="utf-8")
    (root / "local_engine" / "resources").mkdir()
    (root / "local_engine" / "resources" / "__init__.py").write_text("", encoding="utf-8")
    (root / "local_engine" / "resources" / "agents").mkdir()
    (root / "local_engine" / "resources" / "agents" / "backend.yaml").write_text("name: backend\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: main\n", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "bin").mkdir()
    (root / ".pytest_cache").mkdir()
    (root / "local_engine.egg-info").mkdir()
    (root / "__pycache__").mkdir()
    (root / ".local_engine").mkdir()
    (root / "task_plan.md").write_text("scratch\n", encoding="utf-8")
    (root / "README.md").write_text("# release\n", encoding="utf-8")

    assert should_exclude(root.joinpath(".venv").relative_to(root))
    archive = build_zip(root, tmp_path / "release.zip")

    with zipfile.ZipFile(archive) as release:
        names = set(release.namelist())

    assert "README.md" in names
    assert not any(name.startswith(".git/") for name in names)
    assert not any(name.startswith(".venv/") for name in names)
    assert not any(name.startswith(".pytest_cache/") for name in names)
    assert not any(name.startswith(".local_engine/") for name in names)
    assert not any(".egg-info/" in name for name in names)
    assert "local_engine/resources/agents/backend.yaml" in names
    assert "task_plan.md" not in names
