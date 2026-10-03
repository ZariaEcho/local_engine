import os

import pytest
import yaml

from local_engine.runtime.artifact_applier import ArtifactApplier
from local_engine.safety.patch_validator import validate_patch_text
from local_engine.safety.permission_guard import is_safe_project_relative

UNSAFE_PATHS = [
    ".git/config",
    "./.git/config",
    ".git/hooks/pre-commit",
    "a/.git/hooks/pre-commit",
    ".GIT/config",
    ".Git/hooks/post-commit",
    "../outside.py",
    "a/../../outside.py",
    "a/../b.py",
    "/etc/passwd",
    "~/.ssh/authorized_keys",
    "C:\\Windows\\system.ini",
    "C:/Windows/system.ini",
    "..\\outside.py",
    ".git\\config",
    "",
    "   ",
    ".",
    "./",
    "a\x00b.py",
]

SAFE_PATHS = [
    "src/app.py",
    "./src/app.py",
    ".github/workflows/ci.yml",
    ".gitignore",
    ".gitattributes",
    "docs/.hidden/notes.md",
    "a/b/c.py",
    "src\\app.py",
]


@pytest.mark.parametrize("path", UNSAFE_PATHS)
def test_unsafe_paths_are_rejected(path):
    assert is_safe_project_relative(path) is False


@pytest.mark.parametrize("path", SAFE_PATHS)
def test_safe_paths_are_accepted(path):
    assert is_safe_project_relative(path) is True


def make_applier(tmp_path):
    project = tmp_path / "project"
    (project / ".git" / "hooks").mkdir(parents=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "task_graph.yaml").write_text(yaml.safe_dump({"tasks": []}), encoding="utf-8")
    return project, ArtifactApplier(project, run_dir)


@pytest.mark.parametrize("path", [".git/hooks/pre-commit", "./.git/config", "a/.GIT/x.py", "../escape.py"])
def test_applier_rejects_unsafe_artifact_paths(tmp_path, path):
    _, applier = make_applier(tmp_path)
    with pytest.raises(PermissionError):
        applier._validate_relative_path(path)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_applier_rejects_symlink_into_git_directory(tmp_path):
    project, applier = make_applier(tmp_path)
    (project / "link").symlink_to(project / ".git", target_is_directory=True)
    with pytest.raises(PermissionError):
        applier._validate_relative_path("link/hooks/pre-commit")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_applier_rejects_symlink_escaping_project(tmp_path):
    project, applier = make_applier(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / "out").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PermissionError):
        applier._validate_relative_path("out/evil.py")


def patch_for(path):
    return "diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n@@ -1 +1 @@\n-x\n+y\n".format(path)


@pytest.mark.parametrize("path", [".git/hooks/pre-commit", "./.git/config", "../x.py", "a/.GIT/x"])
def test_patch_validator_rejects_unsafe_targets(path):
    with pytest.raises(ValueError):
        validate_patch_text(patch_for(path))


def test_patch_validator_rejects_deletion_and_destructive_text():
    with pytest.raises(ValueError):
        validate_patch_text("diff --git a/x b/x\ndeleted file mode 100644\n--- a/x\n+++ /dev/null\n")
    with pytest.raises(ValueError):
        validate_patch_text(patch_for("ok.py") + "+rm -rf /\n")


def test_patch_validator_accepts_ordinary_patch():
    validate_patch_text(patch_for("src/app.py"))


def make_run(tmp_path, files, patches=None):
    project = tmp_path / "proj"
    project.mkdir()
    run_dir = tmp_path / "run"
    (run_dir / "agent_outputs").mkdir(parents=True)
    (run_dir / "patches").mkdir()
    graph = {"tasks": [{"id": "t", "task_type": "code_generation", "skill": "generate_code", "risk_tags": ["code_change"]}]}
    (run_dir / "task_graph.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    sip = {
        "artifact_protocol": "local-engine.artifacts.v1",
        "artifacts": [{"type": "file", "path": path, "content": content} for path, content in files],
    }
    (run_dir / "agent_outputs" / "t.sip.yaml").write_text(yaml.safe_dump(sip), encoding="utf-8")
    for name, text in (patches or {}).items():
        (run_dir / "patches" / name).write_text(text, encoding="utf-8")
    return project, ArtifactApplier(project, run_dir)


def test_unsafe_artifact_blocks_all_writes(tmp_path):
    project, applier = make_run(tmp_path, [("good.py", "x = 1\n"), (".git/hooks/pre-commit", "evil\n")])
    result = applier.apply(approved=True, verify=False)
    assert result.delivery_status == "failed"
    assert not (project / "good.py").exists()
    assert not (project / ".git").exists()


def test_unsafe_patch_blocks_file_writes(tmp_path):
    project, applier = make_run(
        tmp_path,
        [("good.py", "x = 1\n")],
        {"bad.patch": patch_for(".git/hooks/pre-commit")},
    )
    result = applier.apply(approved=True, verify=False)
    assert result.delivery_status == "failed"
    assert not (project / "good.py").exists()


def test_write_failure_rolls_back_earlier_writes(tmp_path):
    project, applier = make_run(tmp_path, [("a.py", "new\n"), ("sub/b.py", "new\n"), ("blocked/c.py", "new\n")])
    (project / "a.py").write_text("original\n", encoding="utf-8")
    (project / "blocked").write_text("i am a file, not a directory\n", encoding="utf-8")
    result = applier.apply(approved=True, verify=False)
    assert result.delivery_status == "failed"
    assert result.applied_to_project is False
    assert (project / "a.py").read_text(encoding="utf-8") == "original\n"
    assert not (project / "sub").exists()
    assert (project / "blocked").read_text(encoding="utf-8") == "i am a file, not a directory\n"
    assert result.files_created == [] and result.files_modified == []
