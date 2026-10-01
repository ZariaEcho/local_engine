"""Discover and load agent YAML files without a hard-coded agent catalog."""

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import yaml

from local_engine.resources import resource_directory
from local_engine.agents.schema import AgentDefinition, AgentSchemaError


def default_agents_dir() -> Path:
    """Return the repository-provided agent definition directory."""
    configured = os.environ.get("LOCAL_ENGINE_AGENTS_DIR")
    if configured:
        return Path(configured).expanduser()
    return resource_directory("agents")


class AgentRegistry:
    def __init__(self, agents: Iterable[AgentDefinition] = (), source_dir: Optional[Path] = None) -> None:
        self.source_dir = Path(source_dir).resolve() if source_dir else None
        self._agents: Dict[str, AgentDefinition] = {}
        for agent in agents:
            if agent.name in self._agents:
                raise AgentSchemaError("duplicate agent definition `{0}`".format(agent.name))
            self._agents[agent.name] = agent

    @classmethod
    def load(cls, directory: Optional[Path] = None) -> "AgentRegistry":
        source = Path(directory or default_agents_dir()).expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError("agent directory does not exist: {0}".format(source))
        definitions = []
        for path in sorted((*source.glob("*.yaml"), *source.glob("*.yml")), key=lambda item: item.name):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError) as exc:
                raise AgentSchemaError("could not load agent definition `{0}`: {1}".format(path, exc)) from exc
            try:
                agent = AgentDefinition.from_mapping(payload)
            except AgentSchemaError as exc:
                raise AgentSchemaError("invalid agent definition `{0}`: {1}".format(path.name, exc)) from exc
            definitions.append(agent)
        if not definitions:
            raise AgentSchemaError("agent directory contains no YAML definitions: {0}".format(source))
        return cls(definitions, source)

    def __contains__(self, name: str) -> bool:
        return name in self._agents

    def __iter__(self) -> Iterator[AgentDefinition]:
        for name in sorted(self._agents):
            yield self._agents[name]

    @property
    def names(self) -> list[str]:
        return sorted(self._agents)

    def get(self, name: str) -> AgentDefinition:
        try:
            return self._agents[name]
        except KeyError as exc:
            available = ", ".join(self.names) or "(none)"
            raise KeyError("unknown agent `{0}`; available agents: {1}".format(name, available)) from exc

    def to_dict(self) -> Dict[str, dict]:
        return {agent.name: agent.to_dict() for agent in self}
