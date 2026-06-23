"""Small, strict renderer for prompt.md variable placeholders."""

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

from local_engine.skills.schema import SkillDefinition


_PLACEHOLDER = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.]*)\s*}}")


def _lookup(values: Mapping[str, Any], path: str) -> Any:
    current: Any = values
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def _render_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()
    return str(value)


def render_prompt(template: str, variables: Mapping[str, Any]) -> str:
    """Replace ``{{ dotted.variable }}`` values and fail clearly when one is absent."""
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        try:
            return _render_value(_lookup(variables, name))
        except KeyError as exc:
            raise ValueError("prompt variable `{0}` was not provided".format(name)) from exc

    return _PLACEHOLDER.sub(replace, template)


class SkillRenderer:
    def render(self, skill: SkillDefinition, variables: Mapping[str, Any]) -> str:
        try:
            template = skill.prompt_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError("could not read prompt for skill `{0}`: {1}".format(skill.name, exc)) from exc
        return render_prompt(template, variables).strip()
