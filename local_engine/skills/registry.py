"""Discover reusable skill folders at runtime."""

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import yaml

from local_engine.skills.schema import SkillDefinition, SkillSchemaError


def default_skills_dir() -> Path:
    configured = os.environ.get("LOCAL_ENGINE_SKILLS_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(__file__).resolve().parents[2] / "skills"


class SkillRegistry:
    def __init__(self, skills: Iterable[SkillDefinition] = (), source_dir: Optional[Path] = None) -> None:
        self.source_dir = Path(source_dir).resolve() if source_dir else None
        self._skills: Dict[str, SkillDefinition] = {}
        for skill in skills:
            if skill.name in self._skills:
                raise SkillSchemaError("duplicate skill definition `{0}`".format(skill.name))
            self._skills[skill.name] = skill

    @classmethod
    def load(cls, directory: Optional[Path] = None) -> "SkillRegistry":
        source = Path(directory or default_skills_dir()).expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError("skill directory does not exist: {0}".format(source))
        definitions = []
        for child in sorted((path for path in source.iterdir() if path.is_dir()), key=lambda item: item.name):
            definition_path = child / "skill.yaml"
            if not definition_path.is_file():
                continue
            try:
                payload = yaml.safe_load(definition_path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise SkillSchemaError("could not load skill definition `{0}`: {1}".format(definition_path, exc)) from exc
            try:
                definitions.append(SkillDefinition.from_mapping(payload, child))
            except SkillSchemaError as exc:
                raise SkillSchemaError("invalid skill definition `{0}`: {1}".format(child.name, exc)) from exc
        if not definitions:
            raise SkillSchemaError("skill directory contains no skill.yaml files: {0}".format(source))
        return cls(definitions, source)

    def __contains__(self, name: str) -> bool:
        return name in self._skills

    def __iter__(self) -> Iterator[SkillDefinition]:
        for name in sorted(self._skills):
            yield self._skills[name]

    @property
    def names(self) -> list[str]:
        return sorted(self._skills)

    def get(self, name: str) -> SkillDefinition:
        try:
            return self._skills[name]
        except KeyError as exc:
            available = ", ".join(self.names) or "(none)"
            raise KeyError("unknown skill `{0}`; available skills: {1}".format(name, available)) from exc

    def for_task_type(self, task_type: str) -> list[SkillDefinition]:
        """Return deterministic candidates for a declared task capability."""
        return sorted(
            (skill for skill in self._skills.values() if skill.task_type == task_type),
            key=lambda skill: (skill.priority, skill.name),
        )

    def to_dict(self) -> Dict[str, dict]:
        return {skill.name: skill.to_dict() for skill in self}
