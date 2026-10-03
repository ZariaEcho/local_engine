"""Load intent definitions and classify text using their declared keywords."""

import os
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional

import yaml

from local_engine.intents.classification import ClassificationCandidate, ClassificationResult
from local_engine.intents.schema import IntentDefinition, IntentSchemaError
from local_engine.resources import resource_directory


def default_intents_dir() -> Path:
    configured = os.environ.get("LOCAL_ENGINE_INTENTS_DIR")
    return Path(configured).expanduser() if configured else resource_directory("intents")


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

    def classify_result(self, text: str) -> ClassificationResult:
        """Rank YAML keyword matches and make uncertainty explicit.

        One distinct keyword produces a confidence of ``0.70``; two or more
        produce ``0.95``. A tied first place is capped at ``0.55``; a narrow
        first/second-place margin is capped at ``0.75``; and a no-keyword
        fallback is ``0.20`` so neither can silently select a graph.
        """
        lowered = str(text or "").casefold()
        candidates = []
        for intent in self:
            matched = sorted(
                {keyword for keyword in intent.keywords if keyword.casefold() in lowered},
                key=str.casefold,
            )
            score = 0.0 if not matched else min(0.95, 0.40 + (0.30 * len(matched)))
            candidates.append(ClassificationCandidate(intent.name, round(score, 2), matched))
        # Prefer a specific non-default intent for the compatibility string API;
        # the tie still receives low confidence and therefore cannot build a graph
        # without an explicit clarification.
        candidates.sort(key=lambda candidate: (-candidate.score, self._intents[candidate.intent].default, candidate.intent))

        default = self.get(self.default_name)
        if not any(candidate.score > 0.0 for candidate in candidates):
            candidates.sort(key=lambda candidate: (candidate.intent != default.name, candidate.intent))
            return ClassificationResult(
                default.name,
                0.2,
                "no configured intent keyword matched; default candidate is {0}".format(default.name),
                candidates,
            )

        selected = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        confidence = selected.score
        if runner_up is not None and runner_up.score == selected.score:
            confidence = min(confidence, 0.55)
            reason = "matched keywords: {0}; tied with {1}".format(
                ", ".join(selected.matched_keywords), runner_up.intent
            )
        else:
            runner_text = "none" if runner_up is None else "{0} ({1:.2f})".format(runner_up.intent, runner_up.score)
            margin = selected.score - (runner_up.score if runner_up is not None else 0.0)
            if runner_up is not None and margin < 0.30:
                confidence = min(confidence, 0.75)
            reason = "matched keywords: {0}; runner-up: {1}; margin: {2:.2f}".format(
                ", ".join(selected.matched_keywords), runner_text, margin
            )
        return ClassificationResult(selected.intent, round(confidence, 2), reason, candidates)

    def classify(self, text: str) -> str:
        """Compatibility API returning just the selected intent name."""
        return self.classify_result(text).intent
