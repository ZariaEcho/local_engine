"""Validated contract for one intent YAML definition."""

from dataclasses import dataclass, field
from typing import Any, Dict, List


class IntentSchemaError(ValueError):
    pass


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntentSchemaError("intent `{0}` must be a non-empty string".format(name))
    return value.strip()


def _strings(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise IntentSchemaError("intent `{0}` must be a list of non-empty strings".format(name))
    return [item.strip() for item in value]


@dataclass(frozen=True)
class IntentDefinition:
    name: str
    keywords: List[str] = field(default_factory=list)
    required_tasks: List[str] = field(default_factory=list)
    optional_tasks: List[str] = field(default_factory=list)
    context_sensitive: bool = True
    default: bool = False

    @classmethod
    def from_mapping(cls, value: Any) -> "IntentDefinition":
        if not isinstance(value, dict):
            raise IntentSchemaError("intent definition must be a mapping")
        name = _string(value.get("name"), "name").upper()
        required = _strings(value.get("required_tasks"), "required_tasks")
        if not required:
            raise IntentSchemaError("intent `required_tasks` cannot be empty")
        context_sensitive = value.get("context_sensitive", True)
        default = value.get("default", False)
        if not isinstance(context_sensitive, bool) or not isinstance(default, bool):
            raise IntentSchemaError("intent boolean fields must be true or false")
        return cls(
            name=name,
            keywords=_strings(value.get("keywords", []), "keywords"),
            required_tasks=required,
            optional_tasks=_strings(value.get("optional_tasks", []), "optional_tasks"),
            context_sensitive=context_sensitive,
            default=default,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "keywords": list(self.keywords),
            "required_tasks": list(self.required_tasks),
            "optional_tasks": list(self.optional_tasks),
            "context_sensitive": self.context_sensitive,
            "default": self.default,
        }
