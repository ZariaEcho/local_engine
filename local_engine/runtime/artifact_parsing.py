"""Parse model output into file artifacts: explicit SIP artifacts and legacy fenced blocks."""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

ARTIFACT_PROTOCOL_VERSION = "local-engine.artifacts.v1"


@dataclass
class FileArtifact:
    relative_path: str
    content: str
    source: str
    kind: str = "file"
    protocol: str = ARTIFACT_PROTOCOL_VERSION


_FENCE = re.compile(r"```(?P<info>[^\n`]*)\n(?P<code>.*?)```", re.DOTALL)
_PATH_EXTENSIONS = {
    ".cfg",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".py",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
_SPECIAL_FILENAMES = {"Dockerfile", "Makefile", "README", "LICENSE"}


def parse_file_blocks(text: str, source: str = "") -> List[FileArtifact]:
    artifacts: List[FileArtifact] = []
    value = text or ""
    for match in _FENCE.finditer(value):
        info = match.group("info").strip()
        code = match.group("code")
        relative_path = _path_from_info(info)
        content = code
        if not relative_path:
            relative_path = _path_from_context(value[: match.start()])
        if not relative_path:
            relative_path, content = _path_from_code_header(code)
        if not relative_path or not looks_like_file_path(relative_path):
            continue
        artifacts.append(FileArtifact(clean_path(relative_path), normalise_content(content), source))
    return artifacts


def artifacts_from_sip_list(value: Any, source: str, protocol: str = "") -> List[FileArtifact]:
    if not isinstance(value, list):
        return []
    explicit = protocol == ARTIFACT_PROTOCOL_VERSION
    artifacts: List[FileArtifact] = []
    for item in value:
        if not isinstance(item, dict):
            if explicit:
                raise ValueError("explicit artifact entries must be mappings")
            continue
        kind = str(item.get("type") or item.get("kind") or "").strip()
        if explicit and kind != "file":
            raise ValueError("unsupported explicit artifact type: {0}".format(kind or "(missing)"))
        path = first_string(item, ("path", "relative_path")) if explicit else first_string(item, ("path", "file", "filename", "relative_path", "target"))
        content = first_string(item, ("content",)) if explicit else first_string(item, ("content", "body", "text", "source"))
        if explicit and (not path or not content):
            raise ValueError("explicit file artifacts require `path` and `content`")
        if explicit and not looks_like_file_path(path):
            raise ValueError("explicit file artifact path is not a supported file path: {0}".format(path))
        if path and content and looks_like_file_path(path):
            artifacts.append(
                FileArtifact(
                    clean_path(path),
                    normalise_content(content),
                    source + ".artifacts",
                    kind="file",
                    protocol=protocol or "legacy",
                )
            )
    return artifacts



def _path_from_info(info: str) -> str:
    tokens = [token.strip(",") for token in re.split(r"\s+", info or "") if token.strip()]
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
            if key.lower() in {"file", "filename", "path", "target"} and looks_like_file_path(value):
                return value
    for token in tokens:
        if looks_like_file_path(token):
            return token
    return info if looks_like_file_path(info) else ""


def _path_from_context(prefix: str) -> str:
    lines = [line.strip() for line in prefix.splitlines()[-8:] if line.strip()]
    for line in reversed(lines):
        cleaned = re.sub(r"^(?:#{1,6}\s*|[-*]\s*)", "", line).strip().rstrip(":")
        direct = _path_from_label(cleaned) or _path_from_backticks(cleaned)
        if direct and looks_like_file_path(direct):
            return direct
        if looks_like_file_path(cleaned):
            return cleaned
    return ""


def _path_from_code_header(code: str) -> Tuple[str, str]:
    lines = code.splitlines()
    for index, line in enumerate(lines[:3]):
        match = re.match(r"\s*(?:#|//|<!--)\s*(?:file|path|filename)\s*[:=]\s*(.+?)\s*(?:-->)?\s*$", line, re.IGNORECASE)
        if not match:
            continue
        path = match.group(1).strip()
        if looks_like_file_path(path):
            return path, "\n".join(lines[index + 1 :]) + ("\n" if code.endswith("\n") else "")
    return "", code


def _path_from_label(value: str) -> str:
    match = re.match(r"(?i)^(?:file|path|filename|target|create|update)\s*[:：]\s*(.+)$", value.strip())
    return match.group(1).strip() if match else ""


def _path_from_backticks(value: str) -> str:
    match = re.search(r"`([^`]+)`", value)
    return match.group(1).strip() if match else ""


def looks_like_file_path(path_value: str) -> bool:
    value = clean_path(path_value)
    if not value or value.endswith("/") or "\n" in value:
        return False
    name = Path(value).name
    if name in _SPECIAL_FILENAMES:
        return True
    suffix = Path(value).suffix.lower()
    return suffix in _PATH_EXTENSIONS


def clean_path(path_value: str) -> str:
    value = str(path_value or "").strip().strip("`'\"")
    value = re.sub(r"^\./", "", value)
    return value


def normalise_content(content: str) -> str:
    text = textwrap.dedent("" if content is None else str(content)).strip("\n")
    return text if text.endswith("\n") else text + "\n"


def first_string(mapping: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""
