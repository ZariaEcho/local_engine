"""Repository fingerprints and conservative verified task-result reuse."""

from dataclasses import asdict, dataclass
import fnmatch
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from local_engine.kernel.schemas import TaskResult


_IGNORED = {".git", ".local_engine", ".venv", "venv", "node_modules", "__pycache__", "target", "build", "dist", ".pytest_cache"}


def stable_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class RepositoryFingerprint:
    aggregate: str
    files: Dict[str, str]

    def watched_hash(self, patterns: Iterable[str]) -> str:
        patterns = [str(pattern) for pattern in patterns if str(pattern)]
        matched = {
            path: digest
            for path, digest in self.files.items()
            if any(_matches(path, pattern) for pattern in patterns)
        }
        return stable_hash(matched)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatch(path, pattern) or (pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]))


def fingerprint_repository(project_root: Path, cache_dir: Path) -> RepositoryFingerprint:
    root = Path(project_root).expanduser().resolve()
    files: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        if any(part in _IGNORED for part in relative.parts) or not path.is_file() or path.is_symlink():
            continue
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            continue
        files[relative.as_posix()] = digest.hexdigest()
    fingerprint = RepositoryFingerprint(stable_hash(files), files)
    _atomic_json(Path(cache_dir) / "repo_hash.json", fingerprint.to_dict())
    return fingerprint


class TaskCache:
    """Persist the most recent verified result for each stable task signature."""

    def __init__(self, cache_dir: Path, repository: RepositoryFingerprint) -> None:
        self.cache_dir = Path(cache_dir)
        self.repository = repository
        self.task_cache_path = self.cache_dir / "task_cache.json"
        self.verified_path = self.cache_dir / "verified_tasks.json"
        self.entries = self._load_entries()

    def lookup(
        self,
        task: Dict[str, Any],
        dependencies: Dict[str, TaskResult],
        input_hash: str,
        definition_hash: str,
        watched_paths: Iterable[str],
    ) -> Optional[TaskResult]:
        watched_paths = list(watched_paths)
        if not watched_paths or any(result.cache_action != "reuse" for result in dependencies.values()):
            return None
        key = self.key(task, input_hash, definition_hash)
        record = self.entries.get(key)
        if not isinstance(record, dict) or not record.get("verified"):
            return None
        current_watched = self.repository.watched_hash(watched_paths)
        if record.get("repo_hash") != self.repository.aggregate and record.get("watched_hash") != current_watched:
            return None
        result_data = record.get("result")
        if not isinstance(result_data, dict):
            return None
        return TaskResult(
            task_id=task["id"],
            raw=str(result_data.get("raw", "")),
            sip=dict(result_data.get("sip", {})),
            failed=False,
            status=str(result_data.get("status", "completed")),
            error_message="",
            model=str(result_data.get("model", "")),
            retry_history=list(result_data.get("retry_history", [])),
            lifecycle_status="skipped",
            review_rounds=int(result_data.get("review_rounds", 0)),
            review_status=str(result_data.get("review_status", "skipped")),
            unresolved_issues=[],
            failure_type="",
            warnings=list(result_data.get("warnings", [])),
            quality_score=float(result_data.get("quality_score", 0.0)),
            quality_reasons=list(result_data.get("quality_reasons", [])),
            context_patch=str(result_data.get("context_patch", "")),
            cache_action="reuse",
            source_run_id=str(record.get("source_run_id", "")),
        )

    def store(
        self,
        run_id: str,
        task: Dict[str, Any],
        result: TaskResult,
        input_hash: str,
        definition_hash: str,
        watched_paths: Iterable[str],
    ) -> None:
        watched_paths = list(watched_paths)
        if not watched_paths or result.failed or result.lifecycle_status not in {"passed", "skipped"}:
            return
        key = self.key(task, input_hash, definition_hash)
        self.entries[key] = {
            "task_id": task["id"],
            "task_type": task.get("task_type", ""),
            "skill": task.get("skill", ""),
            "input_hash": input_hash,
            "definition_hash": definition_hash,
            "repo_hash": self.repository.aggregate,
            "watched_paths": watched_paths,
            "watched_hash": self.repository.watched_hash(watched_paths),
            "source_run_id": result.source_run_id or run_id,
            "verified": True,
            "result": {
                "raw": result.raw,
                "sip": result.sip,
                "status": result.status,
                "model": result.model,
                "retry_history": result.retry_history,
                "review_rounds": result.review_rounds,
                "review_status": result.review_status,
                "warnings": result.warnings,
                "quality_score": result.quality_score,
                "quality_reasons": result.quality_reasons,
                "context_patch": result.context_patch,
            },
        }

    def flush(self) -> None:
        payload = {"version": 1, "entries": self.entries}
        _atomic_json(self.task_cache_path, payload)
        verified = {
            key: {
                "task_id": value.get("task_id"),
                "skill": value.get("skill"),
                "source_run_id": value.get("source_run_id"),
                "repo_hash": value.get("repo_hash"),
            }
            for key, value in self.entries.items()
            if isinstance(value, dict) and value.get("verified")
        }
        _atomic_json(self.verified_path, {"version": 1, "tasks": verified})

    @staticmethod
    def key(task: Dict[str, Any], input_hash: str, definition_hash: str) -> str:
        return stable_hash({"task_id": task["id"], "input_hash": input_hash, "definition_hash": definition_hash})

    def _load_entries(self) -> Dict[str, Dict[str, Any]]:
        try:
            payload = json.loads(self.task_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        return dict(entries) if isinstance(entries, dict) else {}
