# Changelog

## Unreleased

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
