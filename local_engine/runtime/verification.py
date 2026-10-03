"""Detect and run a project's local verification command after an apply."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional


class VerificationStatus(str, Enum):
    NOT_RUN = "not_run"
    SKIPPED = "skipped"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class VerificationResult:
    status: str = VerificationStatus.NOT_RUN.value
    command: List[str] = field(default_factory=list)
    exit_code: Optional[int] = None
    output: str = ""


def detect_verification_command(project_root: Path) -> List[str]:
    if (
        (project_root / "tests").is_dir()
        or any(project_root.glob("test_*.py"))
        or any(project_root.glob("*_test.py"))
        or (project_root / "pytest.ini").is_file()
        or (project_root / "pyproject.toml").is_file()
    ):
        return [sys.executable, "-m", "pytest", "-q"]
    if (project_root / "package.json").is_file():
        return ["npm", "test", "--", "--watch=false"]
    return []


def run_verification(project_root: Path, timeout: int = 120) -> VerificationResult:
    command = detect_verification_command(project_root)
    if not command:
        return VerificationResult(status=VerificationStatus.SKIPPED.value)
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:
        return VerificationResult(status=VerificationStatus.ERROR.value, command=command, output=str(exc))
    output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    return VerificationResult(
        status=VerificationStatus.PASSED.value if completed.returncode == 0 else VerificationStatus.FAILED.value,
        command=command,
        exit_code=completed.returncode,
        output=output,
    )
