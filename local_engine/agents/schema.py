"""Validated data contracts for agent YAML definitions."""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List


class AgentSchemaError(ValueError):
    """Raised when an agent YAML file does not satisfy the runtime contract."""


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentSchemaError("agent `{0}` must be a non-empty string".format(field_name))
    return value.strip()


def _string_list(value: Any, field_name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise AgentSchemaError("agent `{0}` must be a list of non-empty strings".format(field_name))
    return [item.strip() for item in value]


@dataclass(frozen=True)
class AgentModel:
    primary: str
    fallback: str = ""

    @classmethod
    def from_mapping(cls, value: Any) -> "AgentModel":
        if not isinstance(value, dict):
            raise AgentSchemaError("agent `model` must be a mapping")
        primary = _non_empty_string(value.get("primary"), "model.primary")
        fallback = value.get("fallback", "")
        if fallback is None:
            fallback = ""
        if not isinstance(fallback, str):
            raise AgentSchemaError("agent `model.fallback` must be a string")
        return cls(primary=primary, fallback=fallback.strip())


@dataclass(frozen=True)
class AgentLimits:
    max_retries: int = 2

    @classmethod
    def from_mapping(cls, value: Any) -> "AgentLimits":
        if value is None:
            return cls()
        if not isinstance(value, dict):
            raise AgentSchemaError("agent `limits` must be a mapping")
        max_retries = value.get("max_retries", 2)
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise AgentSchemaError("agent `limits.max_retries` must be a non-negative integer")
        return cls(max_retries=max_retries)


@dataclass(frozen=True)
class AgentDefinition:
    name: str
    description: str
    skills: List[str] = field(default_factory=list)
    model: AgentModel = field(default_factory=lambda: AgentModel("claude"))
    limits: AgentLimits = field(default_factory=AgentLimits)

    @classmethod
    def from_mapping(cls, value: Any) -> "AgentDefinition":
        if not isinstance(value, dict):
            raise AgentSchemaError("agent definition must be a mapping")
        return cls(
            name=_non_empty_string(value.get("name"), "name"),
            description=_non_empty_string(value.get("description"), "description"),
            skills=_string_list(value.get("skills", []), "skills"),
            model=AgentModel.from_mapping(value.get("model")),
            limits=AgentLimits.from_mapping(value.get("limits")),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "skills": list(self.skills),
            "model": {"primary": self.model.primary, "fallback": self.model.fallback},
            "limits": {"max_retries": self.limits.max_retries},
        }


def validate_agent_skill_references(agent: AgentDefinition, known_skills: Iterable[str]) -> None:
    """Optionally validate an agent's advertised skills against a skill registry."""
    missing = sorted(set(agent.skills) - set(known_skills))
    if missing:
        raise AgentSchemaError("agent `{0}` references unknown skills: {1}".format(agent.name, ", ".join(missing)))
