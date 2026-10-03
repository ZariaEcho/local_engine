"""Safe Markdown/TXT input reading."""

from pathlib import Path

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}


def read_requirement_file(path: Path) -> str:
    candidate = path.expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError("input file does not exist: {0}".format(candidate))
    if candidate.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError("input must be Markdown or TXT: {0}".format(candidate))
    return candidate.read_text(encoding="utf-8")
