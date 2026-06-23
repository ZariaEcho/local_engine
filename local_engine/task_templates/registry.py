"""Load task templates from one or more YAML files."""

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import yaml

from local_engine.task_templates.schema import TaskTemplateDefinition, TaskTemplateSchemaError


def default_task_templates_dir() -> Path:
    configured = os.environ.get("LOCAL_ENGINE_TASK_TEMPLATES_DIR")
    return Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / "task_templates"


class TaskTemplateRegistry:
    def __init__(self, templates: Iterable[TaskTemplateDefinition], source_dir: Optional[Path] = None) -> None:
        self.source_dir = Path(source_dir).resolve() if source_dir else None
        self._templates: Dict[str, TaskTemplateDefinition] = {}
        for template in templates:
            if template.name in self._templates:
                raise TaskTemplateSchemaError("duplicate task template `{0}`".format(template.name))
            self._templates[template.name] = template

    @classmethod
    def load(cls, directory: Optional[Path] = None) -> "TaskTemplateRegistry":
        source = Path(directory or default_task_templates_dir()).expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError("task template directory does not exist: {0}".format(source))
        definitions = []
        for path in sorted((*source.glob("*.yaml"), *source.glob("*.yml")), key=lambda item: item.name):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise TaskTemplateSchemaError("could not load task templates `{0}`: {1}".format(path.name, exc)) from exc
            values = payload.get("templates") if isinstance(payload, dict) else payload
            if not isinstance(values, list):
                values = [payload]
            for value in values:
                try:
                    definitions.append(TaskTemplateDefinition.from_mapping(value))
                except TaskTemplateSchemaError as exc:
                    raise TaskTemplateSchemaError("invalid task template in `{0}`: {1}".format(path.name, exc)) from exc
        if not definitions:
            raise TaskTemplateSchemaError("task template directory contains no definitions: {0}".format(source))
        return cls(definitions, source)

    def __iter__(self) -> Iterator[TaskTemplateDefinition]:
        for name in sorted(self._templates):
            yield self._templates[name]

    @property
    def names(self) -> list[str]:
        return sorted(self._templates)

    def get(self, name: str) -> TaskTemplateDefinition:
        try:
            return self._templates[name]
        except KeyError as exc:
            raise KeyError("unknown task template `{0}`; available: {1}".format(name, ", ".join(self.names))) from exc
