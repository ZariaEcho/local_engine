# Task Plan: Local Engine P1 Agent Runtime

## Goal

Upgrade Local Engine into a closed-loop Agent Runtime with a durable run index, declarative intent/task registries, typed recovery, conditional review, feedback patches, verified cross-run task reuse, and a final Context Quality / Stability control pass.

## Current Phase

Complete

## Phases

### Phase 1: Discover the current architecture

- [x] Inspect CLI, engine, graph, workers, config, and tests.
- [x] Identify compatibility constraints and existing run layout.
- [x] Record findings.
- **Status:** complete

### Phase 2: Dynamic registries and CLI

- [x] Add validated Agent and Skill schemas and YAML registries.
- [x] Add default `agents/` and `skills/` definitions.
- [x] Add `agents list/show` and `skills list/show` commands.
- **Status:** complete

### Phase 3: Runtime execution resilience

- [x] Add retry/fallback policies and artifacts.
- [x] Allow `run --skill` and prompt rendering.
- [x] Preserve graph continuation on failure.
- **Status:** complete

### Phase 4: Review/revision loop and reporting

- [x] Add review/revise task states and loop.
- [x] Persist task outputs, reviews, and review summary.
- [x] Extend final report.
- **Status:** complete

### Phase 5: Test and deliver

- [x] Add focused regression and acceptance tests.
- [x] Run full test suite and CLI smoke checks.
- [x] Inspect changes and provide handoff.
- **Status:** complete

### Phase 6: Durable run index and CLI

- [x] Add JSON-backed global run index with legacy metadata import.
- [x] Add human-readable run IDs and `runs list/latest/show/open`.
- [x] Finalize running and failed records reliably.
- **Status:** complete

### Phase 7: Declarative intent and task registries

- [x] Add validated Intent and Task Template schemas/registries.
- [x] Migrate all eight built-in intents and task templates to YAML.
- [x] Build graphs from intent, template, skill, and agent registries.
- **Status:** complete

### Phase 8: Typed recovery and conditional quality gate

- [x] Classify failures and apply bounded type-specific recovery.
- [x] Move review/revision into the per-task completion path.
- [x] Persist and verify quality decisions, context patches, and lifecycle states.
- **Status:** complete

### Phase 9: Repository fingerprint and verified task cache

- [x] Add whole-repository and watched-path fingerprints.
- [x] Reuse only verified outputs with dependency-aware invalidation.
- [x] Persist cache provenance into each run.
- **Status:** complete

### Phase 10: Documentation and acceptance verification

- [x] Add focused P1 Revised tests and update existing compatibility tests.
- [x] Update configuration and README documentation.
- [x] Run full regression and CLI smoke verification.
- **Status:** complete

### Phase 11: Control contracts and classification gate

- [x] Add structured classification results, clarification handling, explicit overrides, and persisted classification evidence.
- [x] Extend the SIP contract for findings, recommendations, and decisions.
- **Status:** complete

### Phase 12: Graph quality gate

- [x] Validate semantic template coverage and declared dependency quality after structural graph validation.
- [x] Persist graph-quality evidence and fail invalid graphs before scheduling.
- **Status:** complete

### Phase 13: Task context propagation and output quality

- [x] Persist direct-dependency task summaries and inject them into compiled prompts.
- [x] Evaluate final task outputs, unify quality artifacts, and tighten cache verification.
- **Status:** complete

### Phase 14: Regression coverage and documentation

- [x] Add P1-Control acceptance coverage and update existing worker fixtures.
- [x] Update CLI/config/SIP documentation, run checks, and record verification.
- **Status:** complete

### Phase 15: Discover context and stability integration points

- [x] Inspect scanner, context builder, prompt compiler, report paths, doctor, and error handling.
- [x] Establish the current regression baseline and retain compatibility constraints.
- **Status:** complete

### Phase 16: Context Quality Gate

- [x] Implement typed context-quality analysis and JSON/Markdown artifacts.
- [x] Inject non-blocking context warnings into every task prompt and show them in inspect/report output.
- [x] Add focused coverage for core paths, dependencies, tests, size risk, and completion thresholds.
- **Status:** complete

### Phase 17: Stability hardening

- [x] Standardize structured error artifacts and surface all run warnings in the final report.
- [x] Extend Doctor checks and add a deterministic end-to-end smoke test.
- **Status:** complete

### Phase 18: Documentation and final verification

- [x] Update MVP lifecycle, run-directory, warning, doctor, and troubleshooting documentation.
- [x] Run `PYTHONPATH=. pytest -q`, CLI Doctor, and a local demo run; resolve regressions.
- **Status:** complete

### Phase 19: MVP real-run fix discovery

- [x] Inspect project type, graph builder, prompt compiler, scheduler, failure classifier, reports, and progress display.
- [x] Map the attached MVP fix plan to existing runtime extension points.
- [x] Record compatibility constraints and current test coverage.
- **Status:** complete

### Phase 20: Project-aware BUILD graph

- [x] Add deterministic project type detection and persist `artifacts/project_type.json`.
- [x] Feed project type into graph generation and block generic web-app tasks for algorithm repositories.
- [x] Update skill/task naming so new BUILD graphs use implementation/test-generation semantics rather than backend/frontend roles.
- **Status:** complete

### Phase 21: Execution contract and failure recovery

- [x] Add run execution context and inject apply-mode write approval into executable prompts.
- [x] Classify permission, clarification, and tool requests separately from format errors.
- [x] Retry approved permission requests with the execution contract and route unapproved/clarification requests to human attention.
- **Status:** complete

### Phase 22: Report recovery and progress visibility

- [x] Ensure all exits can produce partial/failure/interrupted reports and run-index states.
- [x] Make `report --latest` recover partial reports from latest runs, not just completed runs.
- [x] Add task ID, elapsed time, attempt, worker, last output age, and long-running warnings to progress output.
- **Status:** complete

### Phase 23: Acceptance verification

- [x] Add focused regression tests for project detection, graph compatibility, execution contract, permission classification, report recovery, and progress output.
- [x] Run focused tests and the full regression suite.
- [x] Smoke-check the requested graph flow against `/Users/echo/Desktop/code/algorithms/graphs`.
- **Status:** complete

### Phase 24: P0 architecture and contract baseline

- [x] Add architecture, runtime contract, and refactor baseline docs.
- [x] Preserve a smoke baseline for `run` and `report --latest`.
- [x] Keep P0 free of large runtime moves.
- **Status:** complete

### Phase 25: P1 Runtime kernel refactor

- [x] Introduce Runtime input/output, executor request/result, state, store, events, scheduler, recovery, and quality-gate modules.
- [x] Move new run output to `.local_engine/runs/<run_id>/` while preserving legacy `task_reports` reads.
- [x] Remove Codex from default configuration and make executor routing explicit.
- [x] Add top-level `status` and `resume` lifecycle commands.
- **Status:** complete

### Phase 26: P2 Hook and Loop control

- [x] Add built-in runtime hook events and persist hook evidence.
- [x] Add quality/review-driven loop state with max-round control.
- [x] Surface hook and loop state in `state.json` and final reports.
- **Status:** complete

### Phase 27: P3 version, Git, and telemetry preparation

- [x] Add package version module, `local-engine --version`, and changelog.
- [x] Add local-only telemetry schema and default-disabled writer.
- [x] Document branch/tag/version rules.
- **Status:** complete

### Phase 28: Refactor verification

- [x] Add and update regression tests for P0-P3 acceptance criteria.
- [x] Run the full test suite and smoke checks.
- [x] Record final verification.
- **Status:** complete

### Phase 29: Artifact apply and delivery-status discovery

- [x] Inspect current run/apply/report/status paths and generated agent output formats.
- [x] Identify the smallest compatibility-safe place to introduce real artifact application.
- [x] Record current behavior and constraints.
- **Status:** complete

### Phase 30: ArtifactApplier and manifest

- [x] Add a runtime ArtifactApplier that extracts file artifacts from run outputs.
- [x] Validate project-root-contained paths, support dry-run, and write `apply_manifest.json`.
- [x] Preserve generated artifacts when apply cannot proceed.
- **Status:** complete

### Phase 31: CLI semantics and delivery status

- [x] Add `plan`, `apply`, and `run --plan-only/--yes` semantics.
- [x] Add DeliveryStatus evidence and final-report fields for applied files and user-goal satisfaction.
- [x] Keep progress, task status, and final reports on a single status source.
- **Status:** complete

### Phase 32: Permission and resume behavior

- [x] Route permission-request failures to `needs_human`/blocked delivery outcomes when not approved.
- [x] Allow safe project-root apply with `--yes`.
- [x] Support resuming apply from a stored `run_id`.
- **Status:** complete

### Phase 33: Regression and smoke verification

- [x] Add focused tests for artifact extraction, apply command, delivery reports, and status consistency.
- [x] Run focused tests and the full regression suite.
- [x] Smoke-check the graph BUILD flow through a deterministic fake worker.
- **Status:** complete

### Phase 34: v0.3.1 refactor discovery

- [x] Inspect current packaging, resource loading, Engine.run pipeline, executor manager, artifact parsing, and test layout.
- [x] Identify compatibility constraints for all existing CLI commands.
- [x] Record findings.
- **Status:** complete

### Phase 35: Packaging hygiene and package resources

- [x] Add release packaging script or ignore manifest that excludes dev/runtime trash.
- [x] Move or expose agents, skills, intents, and task templates as package resources with project override compatibility.
- [x] Add tests for resource discovery and clean release packaging.
- **Status:** complete

### Phase 36: Executor and runtime pipeline boundaries

- [x] Make ExecutorManager the single execution adapter entry point.
- [x] Extract smaller Engine.run pipeline components while preserving Runtime orchestration.
- [x] Keep existing command behavior and reports stable.
- **Status:** complete

### Phase 37: Explicit artifact protocol and test tiering

- [x] Replace artifact-output heuristics with an explicit protocol while retaining legacy fallback parsing.
- [x] Split fast unit tests from slow integration tests with markers/config and documentation.
- [x] Verify focused and full test suites.
- **Status:** complete

### Phase 38: Current status and SIP audit

- [x] Reconcile root planning files with the older scoped MVP/P0 plan.
- [x] Review current dirty worktree, v0.3.1 implementation scope, SIP artifact protocol, delivery/apply behavior, and test coverage.
- [x] Verify the current regression, integration split, release packaging, and whitespace checks.
- **Status:** complete

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Preserve existing task-graph workflow | P1 should extend rather than replace existing behavior. |
| Use repository-root `agents/` and `skills/` directories | Matches requested dynamic loading model and makes definitions project-local. |
| Migrate all eight built-in intents to YAML | Keeps current behavior while removing the hard-coded intent-to-graph map. |
| Use Skill watched-path globs for cache invalidation | Makes unrelated-file reuse deterministic and auditable. |
| Use deterministic failure classification with optional explicit worker hints | Avoids an extra model call and keeps retry behavior bounded. |
| Treat delivery as a separate status from task execution | A task can generate valid SIP text while still failing to write or verify project files. |
| Preserve CLI behavior during v0.3.1 refactor | The refactor must reduce runtime concentration without changing user-facing command semantics. |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| `python` executable unavailable | 1 | Use the repository's available `python3` interpreter for checks. |
| Legacy CLI test used installed `claude` instead of configured fake worker | 1 | Make `claude_command` the canonical primary-model command; reserve `model_commands` for fallback/other models. |
| Retry-status callback was not passed into scheduler worker execution | 1 | Threaded the callback through `_execute`; focused and full tests now pass. |
| Focused registry regression expected legacy `completed` status but metadata exposed `passed` | 1 | Preserve the legacy `TaskResult.status`/RunOutcome view and expose the new lifecycle separately in artifacts and run-index accounting. |
| Exhausted FORMAT recovery converted useful unstructured output into a hard worker failure | 1 | Keep the preserved output as a warning after bounded format retries so quality/review feedback can still use it. |
| Legacy YAML timestamps deserialize as `datetime` and are not directly JSON serializable | 1 | Normalize imported timestamp values to strings before writing the JSON index. |
| Quality test inspected a review prompt that reused the task ID and overwrote the worker's debug map | 1 | Assert against the canonical persisted execution prompt instead. |
| Legacy SIP contract tests required an exact key set | 1 | Update the contract expectation for the approved additive `warnings` and `failure_type` fields. |
| Focused tests accidentally ran under system Python 3.9 | 1 | Reran with `./.venv/bin/python`, matching the project `>=3.11` requirement. |
| `pytest` command is unavailable on PATH | 1 | Check available Python interpreters and invoke pytest as a module where installed. |
| P1-Control classification contract imported through `planner.__init__` | 1 | Move shared result/error types under `intents/` to remove the Registry ↔ Planner import cycle. |
