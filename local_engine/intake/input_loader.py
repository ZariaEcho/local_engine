"""Combine command text and optional requirement-file content."""

from pathlib import Path
from typing import Any, Dict, Optional

from local_engine.intake.file_reader import read_requirement_file


def load_input(task_text: str, input_file: Optional[Path] = None) -> Dict[str, Any]:
    text = (task_text or "").strip()
    file_content = ""
    if input_file is not None:
        file_content = read_requirement_file(input_file).strip()
    if text and file_content:
        source_type = "mixed"
        raw = "# Command requirement\n\n{0}\n\n# Attached requirement\n\n{1}\n".format(text, file_content)
    elif file_content:
        source_type = "file"
        raw = file_content
    else:
        source_type = "text"
        raw = text
    if not raw.strip():
        raise ValueError("provide task text, an input file, or both")
    return {"source_type": source_type, "raw": raw, "task_text": text, "input_file": str(input_file) if input_file else None}
