"""Wide-input, strict-output parser for unreliable Claude CLI responses."""

import json
import re
from typing import Any, Dict, Optional

import yaml


_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.IGNORECASE | re.DOTALL)
_YAML_TYPE_FRAGMENT = re.compile(r"(?m)^type\s*:\s*.+$")


def _try_yaml(text: str) -> Optional[Dict[str, Any]]:
    try:
        value = yaml.safe_load(text)
    except (yaml.YAMLError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _try_json(text: str) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _end = decoder.raw_decode(text[match.start() :])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _as_body(value: Any, raw: str) -> str:
    if value is None:
        return raw
    if isinstance(value, (dict, list)):
        try:
            return yaml.safe_dump(value, sort_keys=False, allow_unicode=True).strip()
        except (yaml.YAMLError, TypeError, ValueError):
            return str(value)
    return str(value)


def _normalise(candidate: Dict[str, Any], raw: str, skill: str, task_id: str) -> Dict[str, Any]:
    try:
        confidence = float(candidate.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    value_type = candidate.get("type") or "unstructured"
    return {
        "type": str(value_type),
        "skill": skill,
        "task_id": task_id,
        "confidence": confidence,
        "assumptions": _as_list(candidate.get("assumptions")),
        "unknowns": _as_list(candidate.get("unknowns")),
        "risks": _as_list(candidate.get("risks")),
        "dependencies": _as_list(candidate.get("dependencies")),
        "artifacts": _as_list(candidate.get("artifacts")),
        "findings": _as_list(candidate.get("findings")),
        "recommendations": _as_list(candidate.get("recommendations")),
        "decisions": _as_list(candidate.get("decisions")),
        "body": _as_body(candidate.get("body"), raw),
        "warnings": _as_list(candidate.get("warnings")),
        "failure_type": str(candidate.get("failure_type") or ""),
    }


def _empty_error(skill: str, task_id: str) -> Dict[str, Any]:
    return {
        "type": "error",
        "skill": skill,
        "task_id": task_id,
        "confidence": 0.0,
        "assumptions": [],
        "unknowns": ["Claude returned empty output"],
        "risks": ["Task produced no usable output"],
        "dependencies": [],
        "artifacts": [],
        "findings": [],
        "recommendations": [],
        "decisions": [],
        "body": "",
        "warnings": [],
        "failure_type": "format",
    }


def _unstructured(raw: str, skill: str, task_id: str) -> Dict[str, Any]:
    return {
        "type": "unstructured",
        "skill": skill,
        "task_id": task_id,
        "confidence": 0.3,
        "assumptions": [],
        "unknowns": ["Claude output was not valid SIP, but contains text"],
        "risks": ["Downstream tasks may need manual review"],
        "dependencies": [],
        "artifacts": [],
        "findings": [],
        "recommendations": [],
        "decisions": [],
        "body": raw,
        "warnings": [],
        "failure_type": "format",
    }


def parse_sip(raw: str, skill: str, task_id: str) -> Dict[str, Any]:
    """Normalize arbitrary worker text into a valid SIP dictionary.

    This function intentionally never propagates parsing failures into the scheduler.
    """
    try:
        text = "" if raw is None else str(raw)
        if not text.strip():
            return _empty_error(skill, task_id)

        for parser in (_try_yaml, _try_json):
            candidate = parser(text)
            if candidate is not None:
                return _normalise(candidate, text, skill, task_id)

        for block in _FENCED_BLOCK.findall(text):
            for parser in (_try_yaml, _try_json):
                candidate = parser(block)
                if candidate is not None:
                    return _normalise(candidate, text, skill, task_id)

        fragment = _YAML_TYPE_FRAGMENT.search(text)
        if fragment:
            candidate = _try_yaml(text[fragment.start() :])
            if candidate is not None:
                return _normalise(candidate, text, skill, task_id)

        candidate = _first_json_object(text)
        if candidate is not None:
            return _normalise(candidate, text, skill, task_id)
        return _unstructured(text, skill, task_id)
    except Exception:
        # A parser bug is still data handling; preserve the response and continue.
        return _unstructured("" if raw is None else str(raw), skill, task_id)
