"""Small deterministic classifier backed by declarative intent YAML."""

from local_engine.intents.registry import IntentRegistry


AUDIT = "AUDIT"
PLAN = "PLAN"
LEARN = "LEARN"
BUILD = "BUILD"
TEST = "TEST"
DOCUMENT = "DOCUMENT"
REFACTOR = "REFACTOR"
RESEARCH = "RESEARCH"


class IntentClassifier:
    """Classify text without making an external model call."""

    def __init__(self, registry: IntentRegistry = None) -> None:
        self.registry = registry or IntentRegistry.load()

    def classify(self, user_input: str) -> str:
        return self.registry.classify(user_input)


def classify(user_input: str) -> str:
    """Convenience API for ``IntentClassifier().classify(user_input)``."""
    return IntentClassifier().classify(user_input)
