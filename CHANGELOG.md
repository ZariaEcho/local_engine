# Changelog

## 0.3.2

### Security

- Fixed `is_safe_project_relative`: `lstrip("./")` stripped leading dots, so
  `.git/...` paths (including `.git/hooks/*`) passed the guard. The check now
  inspects path components, is case-insensitive for `.git`, and rejects
  absolute, drive-letter, `~`, NUL, and `..` paths.
- `ArtifactApplier` now also rejects paths that resolve through a symlink into
  `.git/` or outside the project root.
- Apply now validates every artifact path, patch path, and `git apply --check`
  before writing anything, and restores all touched files (and removes
  directories it created) if any later step fails.
- Added `tests/test_safety_boundaries.py` covering path guards, patch
  validation, symlink escapes, preflight, and rollback.

### Refactoring

- Executors: removed `workers/claude_cli_worker.py`; `ClaudeExecutor` is now a
  thin `ShellExecutor` subclass, so there is one subprocess implementation.
  Failure messages are unchanged. `ShellExecutor` now merges `request.env` into
  the inherited environment instead of replacing it.
- Split `runtime/artifact_applier.py` into `artifact_parsing.py` (artifact
  extraction), `verification.py` (project verification), and
  `apply_snapshot.py` (rollback). `artifact_applier` re-exports the public names.

### Tooling

- Ruff now also selects `B`, `SIM`, `S`, and `I`; fixed the resulting findings
  (`raise ... from None` in CLI/applier, import order, simplifications).
- Added mypy to dev dependencies and CI. Modules with existing type errors are
  listed in `[[tool.mypy.overrides]]` as explicit debt to burn down.
- Version is now defined only in `local_engine/__version__.py`; `pyproject.toml`
  reads it dynamically. Project description now says "task-graph runtime".
- Renamed phase-named test files to topic names (for example
  `test_p1_control.py` -> `test_classification_and_quality_gates.py`).

### Cleanup

- Removed dead code: compatibility shims (`runtime/scheduler.py`,
  `runtime/quality_gate.py`, `runtime/recovery.py`), the unwired LLM graph
  planner (`graph/graph_planner.py`), the legacy fallback graph API
  (`graph/task_graph_builder.py`), unused `safety/approval_gate.py` and
  `artifacts/artifact_writer.py`, and the never-consumed
  `continue_on_failure` config key.
- Removed repository-root duplicates of the packaged registries
  (`agents/`, `intents/`, `skills/`, `task_templates/`, `templates/`); the
  packaged copies under `local_engine/resources/` remain the single source.
- Untracked local planning scratch files (`findings.md`, `progress.md`,
  `task_plan.md`, `.planning/`).
- Unified the package version on `local_engine/__version__.py` (the stale
  `0.1.0` constant in `local_engine/__init__.py` is gone).
- Added ruff lint configuration and a GitHub Actions CI workflow running
  lint, fast tests, and integration tests on Python 3.11 and 3.13.
- Fixed a CLI progress-row format error (`IndexError` on every worker
  attempt-failure event, silently swallowed by the event guard) and removed
  unused imports and a dead `loop_config` fetch surfaced by the new lint.

## 0.3.1

- Refactored runtime execution boundaries and packaged built-in registries as
  package resources.
- Added release zip packaging hygiene and explicit artifact protocol support.
- Split fast unit and slow integration test workflows.

## 0.3.0

- Prepared staged Runtime refactor documentation.
- Added Runtime/Executor contracts and project-local run state.
- Added built-in hook evidence, loop state, version command, and default-disabled
  local telemetry preparation.

## 0.2.0

- Hook + Loop control target version.

## 0.1.0

- Runtime v1 kernel baseline.
