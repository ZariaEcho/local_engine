"""Deterministic, non-blocking evaluation of final worker outputs."""

from dataclasses import dataclass
from typing import Any, Dict, List

from local_engine.kernel.schemas import TaskResult


DEFAULT_QUALITY_POLICY = {
    "min_body_characters": 80,
    "max_body_characters": 12000,
    "low_confidence_threshold": 0.5,
}


@dataclass(frozen=True)
class OutputQualityReport:
    task: str
    quality: float
    complete: bool
    confidence: float
    warnings: List[str]
    checks: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "quality": self.quality,
            "complete": self.complete,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "checks": dict(self.checks),
        }


class QualityEvaluator:
    """Score output contract adherence without suppressing useful results."""

    def __init__(self, policy: Dict[str, Any] = None) -> None:
        values = dict(DEFAULT_QUALITY_POLICY)
        if isinstance(policy, dict):
            values.update(policy)
        self.min_body_characters = _positive_int(values.get("min_body_characters"), 80)
        self.max_body_characters = max(
            self.min_body_characters, _positive_int(values.get("max_body_characters"), 12000)
        )
        self.low_confidence_threshold = _threshold(values.get("low_confidence_threshold"), 0.5)

    def evaluate(self, task: Dict[str, Any], result: TaskResult) -> OutputQualityReport:
        sip = result.sip if isinstance(result.sip, dict) else {}
        warnings: List[str] = []
        findings = _has_content(sip.get("findings"))
        recommendations = _has_content(sip.get("recommendations"))
        complete = findings and recommendations
        if not complete:
            missing = []
            if not findings:
                missing.append("findings")
            if not recommendations:
                missing.append("recommendations")
            warnings.append("incomplete_output: missing non-empty {0}".format(", ".join(missing)))

        body_length = len(str(sip.get("body", "")).strip())
        if body_length < self.min_body_characters:
            warnings.append("body_too_short: {0} < {1} characters".format(body_length, self.min_body_characters))
        elif body_length > self.max_body_characters:
            warnings.append("body_too_long: {0} > {1} characters".format(body_length, self.max_body_characters))

        confidence = _threshold(sip.get("confidence"), 0.0)
        if confidence < self.low_confidence_threshold:
            warnings.append(
                "low_confidence: {0:.2f} < {1:.2f}".format(confidence, self.low_confidence_threshold)
            )

        quality = 1.0
        if not complete:
            quality -= 0.45
        if body_length < self.min_body_characters or body_length > self.max_body_characters:
            quality -= 0.15
        if confidence < self.low_confidence_threshold:
            quality -= 0.20
        return OutputQualityReport(
            task=str(task.get("id", result.task_id)),
            quality=round(max(0.0, min(1.0, quality)), 2),
            complete=complete,
            confidence=confidence,
            warnings=warnings,
            checks={
                "required_fields": {"findings": findings, "recommendations": recommendations},
                "body_characters": {
                    "actual": body_length,
                    "min": self.min_body_characters,
                    "max": self.max_body_characters,
                },
                "confidence": {"actual": confidence, "minimum": self.low_confidence_threshold},
            },
        )


def _has_content(value: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    return any(str(item).strip() for item in values if item is not None)


def _positive_int(value: Any, default: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return default
    return numeric if numeric > 0 else default


def _threshold(value: Any, default: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, numeric))
