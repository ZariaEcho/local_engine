"""Typed worker failure classification, bounded recovery, and evidence."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional

from local_engine.kernel.schemas import FailureType, WorkerResult
from local_engine.kernel.sip_parser import parse_sip
from local_engine.runtime.fallback import FallbackPolicy
from local_engine.runtime.errors import write_error_artifact


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 2
    continue_on_failure: bool = True
    timeout_multiplier: float = 1.5

    @classmethod
    def from_config(cls, config: Dict[str, Any], agent: Any = None) -> "RetryPolicy":
        execution = config if isinstance(config, dict) else {}
        configured = execution.get("max_retries", 2)
        agent_limit = getattr(getattr(agent, "limits", None), "max_retries", None)
        maximum = agent_limit if agent_limit is not None else configured
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            maximum = 2
        multiplier = execution.get("timeout_multiplier", 1.5)
        if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)) or multiplier < 1:
            multiplier = 1.5
        return cls(max_retries=maximum, continue_on_failure=bool(execution.get("continue_on_failure", True)), timeout_multiplier=float(multiplier))


@dataclass
class RecoveryAttempt:
    stage: str
    model: str
    number: int
    failed: bool
    failure_type: str = ""
    action: str = "execute"
    prompt_patch: str = ""
    timeout_seconds: Optional[int] = None
    error_message: str = ""
    raw: str = ""
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RecoveryResult:
    worker_result: WorkerResult
    attempts: List[RecoveryAttempt]
    model: str
    recovered: bool = False
    failure_type: str = ""


def worker_from_factory(worker_factory: Callable[..., Any], model: str) -> Any:
    """Support both new ``factory(model)`` and legacy zero-argument factories."""
    try:
        signature = inspect.signature(worker_factory)
        accepts_model = any(
            parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD) or parameter.name == "model"
            for parameter in signature.parameters.values()
        ) or len(signature.parameters) >= 1
    except (TypeError, ValueError):
        accepts_model = False
    return worker_factory(model) if accepts_model else worker_factory()


def invoke_worker(
    worker_factory: Callable[..., Any],
    model: str,
    prompt: str,
    task: Dict[str, Any],
    project_root: Path,
    timeout_seconds: Optional[int] = None,
) -> WorkerResult:
    try:
        worker = worker_from_factory(worker_factory, model)
        if timeout_seconds is not None and hasattr(worker, "timeout_seconds"):
            worker.timeout_seconds = int(timeout_seconds)
        result = worker.run(prompt, task, project_root)
        return result if isinstance(result, WorkerResult) else WorkerResult(raw=str(result))
    except Exception as exc:  # worker isolation is part of the execution contract
        return WorkerResult(raw=str(exc), failed=True, error_message="worker raised an exception", failure_type="unknown")


def classify_failure(result: WorkerResult, skill: str, task_id: str) -> Optional[FailureType]:
    explicit = str(result.failure_type or "").strip().lower()
    if explicit in {item.value for item in FailureType}:
        return FailureType(explicit)
    text = "{0}\n{1}".format(result.error_message or "", result.raw or "").lower()
    if _looks_like_permission_request(text):
        return FailureType.PERMISSION_REQUEST
    if _looks_like_clarification_request(text):
        return FailureType.CLARIFICATION_REQUEST
    if _looks_like_tool_request(text):
        return FailureType.TOOL_REQUEST
    if "timeout" in text or "timed out" in text:
        return FailureType.TIMEOUT
    if result.failed and re.search(r"\b(network|connection|dns|socket|econn|unavailable|temporarily unavailable|rate limit)\b", text):
        return FailureType.NETWORK
    if result.failed:
        return FailureType.UNKNOWN
    sip = parse_sip(result.raw or "", skill, task_id)
    sip_failure = str(sip.get("failure_type") or "").strip().lower()
    if sip_failure in {item.value for item in FailureType}:
        return FailureType(sip_failure)
    if sip.get("type") in {"unstructured", "parse_error"} or not str(result.raw or "").strip():
        return FailureType.FORMAT
    if sip.get("type") == "error":
        return FailureType.LOGIC
    return None


def _strict_format_prompt(prompt: str) -> tuple[str, str]:
    patch = "Added strict SIP schema constraint"
    return (
        prompt
        + "\n\n# Strict Format Recovery\nReturn exactly one YAML SIP mapping. Include type, skill, task_id, confidence, assumptions, unknowns, risks, warnings, dependencies, artifacts, body. Do not add Markdown fences or surrounding prose.\n",
        patch,
    )


def _execution_contract_prompt(prompt: str, execution_contract: str = "") -> tuple[str, str]:
    patch = "Reinforced apply-mode execution contract"
    contract = execution_contract.strip() or (
        "## Execution Contract\n\n"
        "Mode: apply\n"
        "User write approval: granted\n"
        "You are allowed to create, modify, and write files inside the allowed write root.\n"
        "Do not ask for additional permission for file writes inside this directory.\n"
    )
    return (
        prompt
        + "\n\n# Permission Request Recovery\n"
        + contract
        + "\n\nContinue directly with the requested SIP output. Do not ask for write approval again.\n",
        patch,
    )


def _compact_timeout_prompt(prompt: str) -> tuple[str, str]:
    limit = 12000
    compact = prompt if len(prompt) <= limit else prompt[:6000] + "\n\n[non-essential middle context removed after timeout]\n\n" + prompt[-6000:]
    patch = "Compacted context and increased timeout"
    return "# Timeout Recovery\nUse the reduced task context below.\n\n" + compact, patch


def run_with_recovery(
    invoke: Callable[..., WorkerResult],
    prompt: str,
    task_id: str,
    primary_model: str,
    retry_policy: RetryPolicy,
    fallback_policy: FallbackPolicy,
    skill: str = "",
    timeout_seconds: Optional[int] = None,
    apply_approved: bool = False,
    execution_contract: str = "",
) -> RecoveryResult:
    """Execute a bounded recovery route selected by deterministic failure type."""
    attempts: List[RecoveryAttempt] = []

    def call(stage: str, model: str, call_prompt: str, action: str = "execute", patch: str = "", timeout: Optional[int] = None) -> tuple[WorkerResult, Optional[FailureType]]:
        try:
            result = invoke(model, call_prompt, timeout)
        except TypeError:
            result = invoke(model, call_prompt)
        if not isinstance(result, WorkerResult):
            result = WorkerResult(raw=str(result))
        failure = classify_failure(result, skill, task_id)
        attempts.append(
            RecoveryAttempt(
                stage=stage,
                model=model,
                number=len(attempts) + 1,
                failed=failure is not None,
                failure_type=failure.value if failure else "",
                action=action,
                prompt_patch=patch,
                timeout_seconds=timeout,
                error_message=result.error_message or "",
                raw=result.raw or "",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        )
        return result, failure

    primary = str(primary_model or "claude")
    result, failure = call("primary", primary, prompt, timeout=timeout_seconds)
    if failure is None:
        return RecoveryResult(result, attempts, primary)

    retry_count = 1 if failure == FailureType.UNKNOWN else retry_policy.max_retries
    retry_prompt = prompt
    retry_timeout = timeout_seconds
    action = "conservative_retry"
    patch = ""
    stage_prefix = "retry"
    if failure == FailureType.FORMAT:
        retry_prompt, patch = _strict_format_prompt(prompt)
        action, stage_prefix = "retry_with_strict_format", "format_retry"
    elif failure == FailureType.TIMEOUT:
        retry_prompt, patch = _compact_timeout_prompt(prompt)
        action, stage_prefix = "retry_with_compact_context", "timeout_retry"
        if timeout_seconds is not None:
            retry_timeout = max(timeout_seconds + 1, int(timeout_seconds * retry_policy.timeout_multiplier))
    elif failure == FailureType.NETWORK:
        action = "retry_original_prompt"
    elif failure == FailureType.LOGIC:
        retry_count = 0
    elif failure == FailureType.PERMISSION_REQUEST:
        if apply_approved:
            retry_count = 1
            retry_prompt, patch = _execution_contract_prompt(prompt, execution_contract)
            action, stage_prefix = "retry_with_execution_contract", "permission_retry"
        else:
            retry_count = 0
    elif failure in {FailureType.CLARIFICATION_REQUEST, FailureType.TOOL_REQUEST}:
        retry_count = 0

    for retry_number in range(1, retry_count + 1):
        stage = "retry_{0}".format(retry_number) if failure == FailureType.NETWORK else "{0}_{1}".format(stage_prefix, retry_number)
        result, next_failure = call(stage, primary, retry_prompt, action, patch, retry_timeout)
        if next_failure is None:
            return RecoveryResult(result, attempts, primary, recovered=True, failure_type=attempts[0].failure_type)
        failure = next_failure
        if failure in {FailureType.LOGIC, FailureType.UNKNOWN}:
            break

    if failure in {FailureType.NETWORK, FailureType.TIMEOUT} and fallback_policy.model:
        fallback_model = fallback_policy.model
        result, next_failure = call("fallback_model", fallback_model, retry_prompt, "fallback_model", patch, retry_timeout)
        if next_failure is None:
            return RecoveryResult(result, attempts, fallback_model, recovered=True, failure_type=attempts[0].failure_type)
        failure = next_failure
        final_model = fallback_model
    else:
        final_model = primary

    failure = failure or FailureType.UNKNOWN
    if failure == FailureType.FORMAT:
        result.failure_type = failure.value
        return RecoveryResult(result, attempts, final_model, recovered=False, failure_type=failure.value)
    primary_error = next((attempt.error_message for attempt in attempts if attempt.error_message), "")
    result = WorkerResult(
        raw=result.raw or primary_error,
        failed=True,
        error_message=primary_error or result.error_message or "{0} failure".format(failure.value),
        failure_type=failure.value,
    )
    return RecoveryResult(result, attempts, final_model, recovered=False, failure_type=failure.value)


def _looks_like_permission_request(text: str) -> bool:
    patterns = (
        r"需要.{0,12}写入权限",
        r"请.{0,8}批准",
        r"批准写入",
        r"是否可以继续",
        r"是否批准",
        r"请求权限",
        r"写入权限",
        r"\bwrite permission\b",
        r"\bpermission to write\b",
        r"\bneed approval\b",
        r"\bplease approve\b",
        r"\bapproval required\b",
        r"\bcan i proceed\b",
        r"\bmay i proceed\b",
        r"\bpermission\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _looks_like_clarification_request(text: str) -> bool:
    patterns = (
        r"需要.{0,8}澄清",
        r"请.{0,8}澄清",
        r"需要更多信息",
        r"\bclarification\b",
        r"\bplease clarify\b",
        r"\bplease specify\b",
        r"\bwhich option\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _looks_like_tool_request(text: str) -> bool:
    patterns = (
        r"需要.{0,8}工具",
        r"请运行",
        r"\btool request\b",
        r"\bneed to use (a )?tool\b",
        r"\bplease run\b",
        r"\brun (this )?command\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def write_recovery_artifacts(artifacts_dir: Path, task_id: str, recovery: RecoveryResult) -> None:
    """Persist structured errors, retry decisions, and a legacy readable log."""
    failures = [attempt for attempt in recovery.attempts if attempt.failed]
    retries_dir = artifacts_dir / "retries"
    retries_dir.mkdir(parents=True, exist_ok=True)
    retry_payload = {
        "task_id": task_id,
        "final_model": recovery.model,
        "recovered": recovery.recovered,
        "failure_type": recovery.failure_type,
        "attempts": [attempt.to_dict() for attempt in recovery.attempts],
    }
    (retries_dir / "{0}.json".format(task_id)).write_text(json.dumps(retry_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not failures:
        return
    errors_dir = artifacts_dir / "errors"
    errors_dir.mkdir(parents=True, exist_ok=True)
    final_failure = recovery.failure_type or failures[-1].failure_type or "unknown"
    write_error_artifact(
        artifacts_dir,
        task_id,
        "compiler" if final_failure == FailureType.FORMAT.value else "worker",
        final_failure,
        failures[-1].error_message or "worker/output failure",
        recovery.recovered,
        final_failure_type=final_failure,
        recovered=recovery.recovered,
        failures=[attempt.to_dict() for attempt in failures],
    )
    lines = ["# Task Recovery Errors", "", "Task: `{0}`".format(task_id), ""]
    for attempt in failures:
        lines.extend([
            "## Attempt {0}: {1}".format(attempt.number, attempt.stage),
            "- Model: `{0}`".format(attempt.model),
            "- Failure type: `{0}`".format(attempt.failure_type),
            "- Action: `{0}`".format(attempt.action),
            "- Error: {0}".format(attempt.error_message or "worker/output failure"),
            "",
            attempt.raw or "(empty response)",
            "",
        ])
    (errors_dir / "{0}.log".format(task_id)).write_text("\n".join(lines), encoding="utf-8")
