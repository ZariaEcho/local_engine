"""Validated contract for reusable task graph nodes."""

from dataclasses import dataclass, field
from typing import Any, Dict, List


class TaskTemplateSchemaError(ValueError):
    pass


def _string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TaskTemplateSchemaError("task template `{0}` must be a non-empty string".format(name))
    return value.strip()


def _strings(value: Any, name: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise TaskTemplateSchemaError("task template `{0}` must be a list of strings".format(name))
    return [item.strip() for item in value]


@dataclass(frozen=True)
class TaskTemplateDefinition:
    name: str
    task_type: str
    task_id: str
    title: str
    depends_on: List[str] = field(default_factory=list)
    output_type: str = "report"
    output_path: str = ""
    risk_tags: List[str] = field(default_factory=list)
    review_required: bool = False
    must: List[str] = field(default_factory=list)
    must_not: List[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, value: Any) -> "TaskTemplateDefinition":
        if not isinstance(value, dict):
            raise TaskTemplateSchemaError("task template definition must be a mapping")
        output = value.get("expected_output")
        if not isinstance(output, dict):
            raise TaskTemplateSchemaError("task template `expected_output` must be a mapping")
        constraints = value.get("constraints", {})
        if not isinstance(constraints, dict):
            raise TaskTemplateSchemaError("task template `constraints` must be a mapping")
        review_required = value.get("review_required", False)
        if not isinstance(review_required, bool):
            raise TaskTemplateSchemaError("task template `review_required` must be boolean")
        return cls(
            name=_string(value.get("name"), "name"),
            task_type=_string(value.get("task_type"), "task_type"),
            task_id=_string(value.get("id"), "id"),
            title=_string(value.get("title"), "title"),
            depends_on=_strings(value.get("depends_on", []), "depends_on"),
            output_type=_string(output.get("type"), "expected_output.type"),
            output_path=_string(output.get("path"), "expected_output.path"),
            risk_tags=_strings(value.get("risk_tags", []), "risk_tags"),
            review_required=review_required,
            must=_strings(constraints.get("must", []), "constraints.must"),
            must_not=_strings(constraints.get("must_not", []), "constraints.must_not"),
        )

    def to_task(self, skill: str, agent: str, dependency_ids: List[str]) -> Dict[str, Any]:
        return {
            "id": self.task_id,
            "title": self.title,
            "task_type": self.task_type,
            "skill": skill,
            "agent": agent,
            "depends_on": dependency_ids,
            "can_parallel": not dependency_ids,
            "expected_output": {"type": self.output_type, "path": self.output_path},
            "risk_tags": list(self.risk_tags),
            "review_required": self.review_required,
            "constraints": {"must": list(self.must), "must_not": list(self.must_not)},
        }
