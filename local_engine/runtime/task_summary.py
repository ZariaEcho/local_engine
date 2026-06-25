"""Compact, deterministic summaries passed along direct DAG dependencies."""

from typing import Any, Dict, List

from local_engine.kernel.schemas import TaskResult


SUMMARY_FIELDS = ("findings", "risks", "decisions")
MAX_ITEMS = 5
MAX_ITEM_CHARACTERS = 500


def build_task_summary(result: TaskResult) -> Dict[str, List[str]]:
    sip = result.sip if isinstance(result.sip, dict) else {}
    return {field: _values(sip.get(field)) for field in SUMMARY_FIELDS}


def _values(value: Any) -> List[str]:
    values = value if isinstance(value, list) else [value]
    compact: List[str] = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        text = text[:MAX_ITEM_CHARACTERS]
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        compact.append(text)
        if len(compact) == MAX_ITEMS:
            break
    return compact
