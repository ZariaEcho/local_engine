# Progress Log

## Session: 2026-06-25 — MVP real-run fixes

### Phase 19: MVP real-run fix discovery

- **Status:** in progress
- Actions taken:
  - Restored existing planning context and read the attached MVP fix plan.
  - Added Phases 19–23 to track project-aware BUILD graphs, apply-mode execution contract, typed permission classification, report recovery, progress visibility, and acceptance verification.
  - Recorded the new real-run requirements in findings.
  - Added deterministic project type detection plus new `generate_code` and `test_generation` skills and a `python_coder` agent.
  - Added algorithm-repository BUILD task templates and routed `BUILD + algorithm_repository` to `generate_algorithm_files` and `test_algorithm_files`.
  - Added a graph-quality compatibility check to reject generic web-app tasks for algorithm repositories unless the user explicitly requested web/app/database work.
  - Added run execution context rendering and wired apply-mode write approval into worker prompts and permission-request recovery.
  - Extended failure classification for permission, clarification, and tool requests before format classification.
  - Updated CLI graph preview and progress display with project type, task/attempt/worker details, last output age, and long-running warnings.
  - Fixed BUILD intent keyword coverage for the real request `生成多个详细的算法文件` without stealing `生成项目文档` from DOCUMENT.
  - Verified the requested graph command against `/Users/echo/Desktop/code/algorithms/graphs`; it now reports `intent: BUILD`, `project_type: algorithm_repository`, and the algorithm-specific tasks.

## MVP Real-Run Fix Verification

| Check | Result |
|------|--------|
| Focused MVP tests | passed |
| Intent classifier regression | passed |
| Full regression suite | 90 passed |
| `git diff --check` | passed |
| Requested graph smoke | passed |

## Session: 2026-06-23 — P1-5 / P1-6 final control pass

### Phase 15: Discover context and stability integration points

- **Status:** in progress
- Actions taken:
  - Restored the existing Local Engine P1 plan and recorded the new Context Quality / Stability scope as Phases 15–18.
  - Confirmed the worktree contains prior P1-Control changes; these are in scope and will be preserved while extending the implementation.
  - Located the main control boundaries: direct filesystem/context analysis after scan, centralized prompt compilation, run-scoped artifacts, engine-level warning aggregation, CLI inspection, and the Doctor readiness table.
  - Baseline command `PYTHONPATH=. pytest -q` could not start because `pytest` is not on PATH; the repository `.venv` provides Python 3.11.15 and pytest 9.1.1.
  - Baseline verification passed: `PYTHONPATH=. ./.venv/bin/python -m pytest -q` (72 tests).
  - Completed architecture discovery and began Phase 16 implementation: added context manifest visibility, a typed filesystem-backed ContextQualityReport, run artifacts, task-prompt injection, final-report context evidence, and `inspect` output.
  - Began Phase 17 hardening: expanded Doctor checks for project/global writable locations and dynamic registry/load-index readiness.
  - Added focused Context Quality coverage (missing tests, manifest omission, large project, threshold, prompt/report artifacts) and a CLI smoke run that asserts the complete run layout.
  - Focused verification passed: 8 new tests. Full regression passed before the final two stability tests: 78 tests.
  - Updated README and configuration template with MVP boundaries, lifecycle, output artifacts, prior-run discovery, warning handling, Doctor, and troubleshooting guidance.
  - Added coverage for the normalized error JSON shape/final-report warning propagation and expanded Doctor control points.
  - Final verification passed: `PYTHONPATH=. ./.venv/bin/python -m pytest -q` (80 tests) and `git diff --check`.
  - Ran `./.venv/bin/local-engine doctor --project .`; all Python, worker, writable-path, registry, run-index, and cache checks passed.
  - Ran an isolated real CLI lifecycle with `local-engine run --project ./demo "审计这个项目"` against a deterministic fake Claude CLI. It generated raw input, normalized requirement, task graph, prompts, agent outputs, Context Quality artifacts, quality artifacts, deliverables, and final report; all audit tasks completed.

## Session: 2026-06-23 — P1-Control

### Phase 11: Control contracts and classification gate

- **Status:** in progress
- Actions taken:
  - Restored the completed P1 Revised state and confirmed a clean worktree.
  - Re-inspected engine, prompt compiler, scheduler, graph validator, cache, CLI, and regression fixtures.
  - Confirmed the existing quality artifact is review evidence and that task finalization is the correct hook for final output evaluation and summary persistence.
  - First regression collection found a circular import caused by placing shared classification contracts under `planner/`; moved them to `intents/classification.py` so Registry can own them without loading Planner.
  - Implemented deterministic confidence scoring with first/second candidate margin, explicit `--intent` overrides, interactive CLI selection, and non-interactive clarification exit code 2.
  - Added template-backed graph quality coverage/dependency checks with persisted failure evidence and Run Index failure finalization.
  - Added final task summaries, direct-dependency prompt injection, unified output-quality reports, and cache rejection for incomplete or low-confidence outputs.
  - Added P1-Control acceptance coverage and updated legacy SIP/cache fixtures for the additive output contract.

## P1-Control Verification

| Test | Result |
|------|--------|
| P1-Control focused acceptance suite | 7 passed |
| Full regression suite | 72 passed |
| Python bytecode compilation | passed |
| `git diff --check` | passed |

## Session: 2026-06-23

### Phase 1–5: Discover, implement, verify, and deliver

- **Status:** complete
- Actions taken:
  - Initialized durable planning files for the Local Engine P1 implementation.
  - Inspected CLI, runtime engine/config/context, scheduler, graph validation/repair, prompt compiler, tests, and docs.
  - Identified the hard-coded agent catalog and the scheduler extension points for retry/fallback and review.
  - Added YAML agent and skill registries, default definitions, prompt rendering, retry/fallback utilities, dynamic capability validation, direct-skill graph support, CLI inspection commands, and review-loop scaffolding.
  - Added retry evidence, review evidence, review summary JSON, lifecycle/reporting fields, README/config documentation, and P1 regression coverage.
  - Verified registry CLI inspection, direct `run --skill`, fallback recovery, and review revision behavior.
- Files created/modified:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`
  - P1 runtime, CLI, registry, config, documentation, and test files.

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Registry CLI | `agents show backend`, `skills show audit_repo` | YAML definitions resolve | Resolved expected agent and skill | ✓ |
| Direct skill CLI | `run --skill audit_repo` | Selected skill runs and deliverable persists | Passed | ✓ |
| Retry/fallback | Simulated Claude failure | Retries then Codex fallback with evidence | Passed | ✓ |
| Review loop | Explicit `VERDICT: FAIL` then pass | Revision and second review created | Passed | ✓ |
| Full regression suite | `python3 -m pytest -q` | All tests pass | 59 passed | ✓ |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-06-23 | `python: command not found` | 1 | Use `python3` for verification. |
| 2026-06-23 | Primary model routed to installed `claude` in a CLI test | 1 | Restored `claude_command` as the primary command override. |
| 2026-06-23 | Retry-status callback missing in threaded scheduler execution | 1 | Added callback parameter and verified recovery artifacts again. |

## Session: 2026-06-23 — P1 Revised

### Phase 6: Run index and revised runtime groundwork

- **Status:** in progress
- Actions taken:
  - Restored the completed first P1 implementation and confirmed the working tree contains those in-progress user changes.
  - Reviewed the approved P1 Revised implementation plan against the current CLI, engine, scheduler, registries, parser, and repository scanner.
  - Confirmed the existing regression baseline is 59 passing tests.
  - Added the JSON `RunIndex`, UTC readable ID allocation, atomic index/latest views, and legacy YAML metadata import.
  - Routed existing report discovery and metadata writes through the new index while retaining the old helper interfaces.
  - Added validated Intent and Task Template schemas/registries with environment-based directory overrides.
  - Declared all eight built-in intents and their current DAG/output shapes in YAML.
  - Extended Skill definitions with task type, review policy, deterministic priority, and watched-path cache policy.
  - Replaced the hard-coded intent graph routes with deterministic four-registry composition and verified all eight generated DAGs.
  - Focused tests found one compatibility mismatch (`passed` vs legacy `completed`); the implementation will keep both execution and lifecycle views.
  - Added shared failure types, Worker/SIP failure hints, warnings, quality/cache provenance fields, and default timeout/review policy settings.
  - Implemented deterministic NETWORK/FORMAT/TIMEOUT/LOGIC/UNKNOWN routes, structured recovery evidence, timeout compaction, and strict-format prompt patches.
  - Recovery-focused tests exposed two intended compatibility adjustments: preserve legacy execution status views and retain exhausted FORMAT text as warning data.
  - Moved conditional quality gating and review/revision before dependency release, and injected generated context patches into downstream prompts.
  - Focused runtime/recovery tests pass except for a graph-source label; retained the legacy `dynamic` value and added `registry_composed: true` metadata.
  - Added unexpected-run failure finalization and startup validation of every Agent-to-Skill reference.
  - Added whole-tree SHA-256 fingerprints plus conservative verified-task cache storage and watched-path lookup.
  - Integrated cache lookup before prompt/worker execution, persisted cache provenance, and verified exact-match reuse in a two-run smoke test.
  - All existing focused runtime, dynamic-graph, and first-P1 tests now pass.
  - Added P1 Revised acceptance tests for run indexing, registry composition, all recovery types, conditional review/context feedback, and watched-path cache invalidation.
  - Corrected legacy timestamp normalization and verified all six new P1 Revised acceptance tests pass.
  - Full regression reached three expected SIP contract failures caused by the two approved additive fields; runtime behavior otherwise passed.
  - Updated the SIP contract tests/template, runtime prompt contract, configuration examples, run-index/cache documentation, typed recovery, and conditional review guidance.
  - Added Registry-definition cache invalidation and intermediate `format_retrying`/`logic_failed` progress events.
  - Re-ran syntax checks, `git diff --check`, and the full suite: 65 tests pass.
  - Verified package compilation and smoke-tested top-level help, `runs --help`, `agents list`, and `skills list`.
  - Completed all P1 Revised phases.
