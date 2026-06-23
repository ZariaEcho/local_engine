# Task Plan: Local Engine P1 Agent Runtime

## Goal

Upgrade Local Engine into a closed-loop Agent Runtime with a durable run index, declarative intent/task registries, typed recovery, conditional review, feedback patches, and verified cross-run task reuse.

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

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Preserve existing task-graph workflow | P1 should extend rather than replace existing behavior. |
| Use repository-root `agents/` and `skills/` directories | Matches requested dynamic loading model and makes definitions project-local. |
| Migrate all eight built-in intents to YAML | Keeps current behavior while removing the hard-coded intent-to-graph map. |
| Use Skill watched-path globs for cache invalidation | Makes unrelated-file reuse deterministic and auditable. |
| Use deterministic failure classification with optional explicit worker hints | Avoids an extra model call and keeps retry behavior bounded. |

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
