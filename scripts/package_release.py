#!/usr/bin/env python3
"""Build a clean local_engine release zip from the working tree."""

from __future__ import annotations

import argparse
from fnmatch import fnmatch
from pathlib import Path
import tomllib
import zipfile


EXCLUDED_NAMES = {
    ".git",
    ".local_engine",
    ".mypy_cache",
    ".planning",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "findings.md",
    "progress.md",
    "task_plan.md",
    "venv",
}
EXCLUDED_TOP_LEVEL = {"agents", "intents", "skills", "task_templates"}
EXCLUDED_PATTERNS = {
    "*.egg-info",
    "*.pyc",
    "*.pyo",
    ".DS_Store",
    ".local_engine",
    ".local_engine/*",
    "task_reports",
    "task_reports/*",
}


def should_exclude(relative: Path) -> bool:
    parts = relative.parts
    if parts and parts[0] in EXCLUDED_TOP_LEVEL:
        return True
    if any(part in EXCLUDED_NAMES for part in parts):
        return True
    if any(fnmatch(part, "*.egg-info") for part in parts):
        return True
    text = relative.as_posix()
    return any(fnmatch(text, pattern) or fnmatch(relative.name, pattern) for pattern in EXCLUDED_PATTERNS)


def project_version(root: Path) -> str:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data.get("project", {}).get("version", "0.0.0"))


def iter_release_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if should_exclude(relative):
            continue
        if path.is_file():
            yield path, relative


def build_zip(root: Path, output: Path | None = None) -> Path:
    root = root.expanduser().resolve()
    version = project_version(root)
    output = output or root / "dist" / "local-engine-{0}.zip".format(version)
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, relative in iter_release_files(root):
            if path.resolve() == output:
                continue
            archive.write(path, relative.as_posix())
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    print(build_zip(args.root, args.output))


if __name__ == "__main__":
    main()
