"""Run-level execution permissions passed into worker prompts."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


@dataclass(frozen=True)
class RunExecutionContext:
    mode: str
    apply_approved: bool
    project_root: Path
    allowed_write_root: Path

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "apply_approved": self.apply_approved,
            "project_root": str(self.project_root),
            "allowed_write_root": str(self.allowed_write_root),
        }

    def to_prompt_section(self) -> str:
        approval = "granted" if self.mode == "apply" and self.apply_approved else "not granted"
        lines = [
            "## Execution Contract",
            "",
            "Mode: {0}".format(self.mode),
            "User write approval: {0}".format(approval),
            "Allowed write root: {0}".format(self.allowed_write_root),
            "",
        ]
        if self.mode == "apply" and self.apply_approved:
            lines.extend(
                [
                    "You are allowed to create, modify, and write files inside the allowed write root.",
                    "Do not ask for additional permission for file writes inside this directory.",
                    "If writing outside this directory is required, stop and report.",
                    "",
                    "用户已经批准本次 run 在项目目录内写入文件。",
                    "不要再询问“是否可以继续”“是否批准写入”。",
                    "直接输出符合 SIP 的 patch / artifact。",
                ]
            )
        else:
            lines.extend(
                [
                    "Do not write files. Return plans, reports, or patches according to the task contract.",
                    "If file writes are required, report that apply mode approval is needed.",
                ]
            )
        return "\n".join(lines)

