"""Safe report-directory artifact writing."""

from pathlib import Path


def write_artifact(artifacts_dir: Path, filename: str, content: str) -> Path:
    target = (artifacts_dir / filename).resolve()
    root = artifacts_dir.resolve()
    if target != root and root not in target.parents:
        raise ValueError("artifact path must remain inside run artifact directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
