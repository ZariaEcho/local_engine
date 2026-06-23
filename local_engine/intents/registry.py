"""Load intent definitions and classify text using their declared keywords."""

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import yaml

from local_engine.intents.schema import IntentDefinition, IntentSchemaError


def default_intents_dir() -> Path:
    configured = os.environ.get("LOCAL_ENGINE_INTENTS_DIR")
    return Path(configured).expanduser() if configured else Path(__file__).resolve().parents[2] / "intents"


class IntentRegistry:
    def __init__(self, intents: Iterable[IntentDefinition], source_dir: Optional[Path] = None) -> None:
        self.source_dir = Path(source_dir).resolve() if source_dir else None
        self._intents: Dict[str, IntentDefinition] = {}
        for intent in intents:
            if intent.name in self._intents:
                raise IntentSchemaError("duplicate intent definition `{0}`".format(intent.name))
            self._intents[intent.name] = intent
        defaults = [intent.name for intent in self._intents.values() if intent.default]
        if len(defaults) != 1:
            raise IntentSchemaError("intent registry must declare exactly one default intent")
        self.default_name = defaults[0]

    @classmethod
    def load(cls, directory: Optional[Path] = None) -> "IntentRegistry":
        source = Path(directory or default_intents_dir()).expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError("intent directory does not exist: {0}".format(source))
        definitions = []
        for path in sorted((*source.glob("*.yaml"), *source.glob("*.yml")), key=lambda item: item.name):
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8"))
                definitions.append(IntentDefinition.from_mapping(payload))
            except (OSError, yaml.YAMLError, IntentSchemaError) as exc:
                raise IntentSchemaError("invalid intent definition `{0}`: {1}".format(path.name, exc)) from exc
        if not definitions:
            raise IntentSchemaError("intent directory contains no YAML definitions: {0}".format(source))
        return cls(definitions, source)

    def __iter__(self) -> Iterator[IntentDefinition]:
        for name in sorted(self._intents):
            yield self._intents[name]

    @property
    def names(self) -> list[str]:
        return sorted(self._intents)

    def get(self, name: str) -> IntentDefinition:
        try:
            return self._intents[str(name).upper()]
        except KeyError as exc:
            raise KeyError("unknown intent `{0}`; available intents: {1}".format(name, ", ".join(self.names))) from exc

    def classify(self, text: str) -> str:
        lowered = str(text or "").lower()
        for intent in self:
            if intent.default:
                continue
            if any(keyword.lower() in lowered for keyword in intent.keywords):
                return intent.name
        default = self.get(self.default_name)
        if any(keyword.lower() in lowered for keyword in default.keywords):
            return default.name
        return self.default_name
