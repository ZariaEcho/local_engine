"""Validated data contracts for skill directories."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


class SkillSchemaError(ValueError):
    """Raised when a skill definition is malformed."""


def _string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillSchemaError("skill `{0}` must be a non-empty string".format(field_name))
    return value.strip()


def _strings(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise SkillSchemaError("skill `{0}` must be a list of non-empty strings".format(field_name))
    return [item.strip() for item in value]


@dataclass(frozen=True)
class SkillCache:
    enabled: bool = False
    watched_paths: List[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Any) -> "SkillCache":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise SkillSchemaError("skill `cache` must be a mapping")
        paths = _strings(value.get("watched_paths", []), "cache.watched_paths")
        enabled = value.get("enabled", bool(paths))
        if not isinstance(enabled, bool):
            raise SkillSchemaError("skill `cache.enabled` must be boolean")
        return cls(enabled=enabled, watched_paths=paths)


@dataclass(frozen=True)
class SkillDefinition:
    name: str
    description: str
    task_type: str = ""
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    default_agent: str = ""
    review_required: bool = False
    priority: int = 100
    cache: SkillCache = field(default_factory=SkillCache)
    directory: Path = field(default_factory=Path)
    prompt_path: Path = field(default_factory=Path)

    @classmethod
    def from_mapping(cls, value: Any, directory: Path) -> "SkillDefinition":
        if not isinstance(value, dict):
            raise SkillSchemaError("skill definition must be a mapping")
        prompt_path = directory / "prompt.md"
        if not prompt_path.is_file():
            raise SkillSchemaError("skill `{0}` is missing prompt.md".format(directory.name))
        name = _string(value.get("name"), "name")
        review_required = value.get("review_required", False)
        priority = value.get("priority", 100)
        if not isinstance(review_required, bool):
            raise SkillSchemaError("skill `review_required` must be boolean")
        if isinstance(priority, bool) or not isinstance(priority, int) or priority < 0:
            raise SkillSchemaError("skill `priority` must be a non-negative integer")
        return cls(
            name=name,
            description=_string(value.get("description"), "description"),
            task_type=_string(value.get("task_type", name), "task_type"),
            inputs=_strings(value.get("inputs", []), "inputs"),
            outputs=_strings(value.get("outputs", []), "outputs"),
            default_agent=_string(value.get("default_agent"), "default_agent"),
            review_required=review_required,
            priority=priority,
            cache=SkillCache.from_mapping(value.get("cache")),
            directory=directory,
            prompt_path=prompt_path,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "task_type": self.task_type,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "default_agent": self.default_agent,
            "review_required": self.review_required,
            "priority": self.priority,
            "cache": {"enabled": self.cache.enabled, "watched_paths": list(self.cache.watched_paths)},
            "prompt_path": str(self.prompt_path),
        }
