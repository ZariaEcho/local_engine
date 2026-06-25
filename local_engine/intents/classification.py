"""Typed, explainable intent-classification results."""

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ClassificationCandidate:
    """One ranked intent candidate and its keyword evidence."""

    intent: str
    score: float
    matched_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "score": self.score,
            "matched_keywords": list(self.matched_keywords),
        }


@dataclass(frozen=True)
class ClassificationResult:
    """The selected intent plus deterministic confidence and explanation."""

    intent: str
    confidence: float
    reason: str
    candidates: List[ClassificationCandidate] = field(default_factory=list)

    @property
    def needs_clarification(self) -> bool:
        return self.confidence < 0.6

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason": self.reason,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def explicit(cls, intent: str, reason: str) -> "ClassificationResult":
        selected = str(intent).upper()
        return cls(
            intent=selected,
            confidence=1.0,
            reason=reason,
            candidates=[ClassificationCandidate(selected, 1.0, [])],
        )


class ClarificationRequired(ValueError):
    """Raised when an intent must be selected before a graph can be built."""

    def __init__(self, result: ClassificationResult) -> None:
        self.result = result
        candidates = ", ".join(
            "{0} ({1:.2f})".format(candidate.intent, candidate.score)
            for candidate in result.candidates[:3]
        )
        super().__init__(
            "intent classification is uncertain ({0:.2f}): {1}. Candidates: {2}".format(
                result.confidence, result.reason, candidates or "none"
            )
        )
