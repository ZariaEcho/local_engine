"""Fallback policy and recovery prompt construction."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class FallbackPolicy:
    model: str = ""
    prompt_enabled: bool = True

    @classmethod
    def from_config(cls, config: Dict[str, Any], agent: Any = None) -> "FallbackPolicy":
        execution = config if isinstance(config, dict) else {}
        configured = execution.get("fallback_executor")
        if not configured:
            configured = execution.get("fallback_model", "")
        agent_model = getattr(getattr(agent, "model", None), "fallback", "")
        model = str(agent_model or configured or "").strip()
        prompt_enabled = bool(execution.get("fallback_prompt", True))
        return cls(model=model, prompt_enabled=prompt_enabled)


def build_fallback_prompt(original_prompt: str, task_id: str, failure_summary: str) -> str:
    """Ask for a recovery response without losing the original task contract."""
    return """# Recovery Execution

The prior execution for task `{task_id}` did not complete successfully.
Failure summary: {failure_summary}

Return the best valid SIP response you can using the original task context below. Do not mention this recovery wrapper unless the failure materially limits the result.

--- Original Task Prompt ---
{original_prompt}
""".format(task_id=task_id, failure_summary=failure_summary or "unknown worker failure", original_prompt=original_prompt)
