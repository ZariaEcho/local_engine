# Progress Log

## Session: 2026-07-18 — Current status and SIP audit

### Phase 38: Current status and SIP audit

- **Status:** complete
- Actions taken:
  - Restored root planning context and compared it with the older scoped MVP/P0 plan under `.planning/2026-06-22-local-engine-mvp-refactor`.
  - Inspected the dirty worktree, README, pyproject metadata, v0.3.1 runtime changes, packaged resources, ExecutorManager routing, `ArtifactApplier`, SIP parser/prompt contract, run delivery status, apply/resume behavior, and artifact tests.
  - Verified current baseline with Python 3.11.15: `local-engine --version` reports `0.3.1`; full pytest suite passes; integration and non-integration splits pass; release zip generation passes; `git diff --check` passes.
  - Recorded the main current gaps: resumed apply can mark user goals satisfied without accounting for original task failures, duplicate artifact warnings are not surfaced, patch-only not-applied file evidence is incomplete, auto-verification is hard-coded, and the README run-output block has a formatting error.
- Files modified:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

## Session: 2026-06-26 — v0.3.1 Runtime boundary and packaging refactor

### Phase 34: Discovery

- **Status:** complete
- Actions taken:
  - Restored existing planning-with-files context.
  - Added Phases 34-37 for discovery, packaging/resources, executor/runtime boundaries, artifact protocol, and test tiering.
  - Inspected packaging metadata, `Engine.run()`, ExecutorManager, registries, scheduler, artifact applier, prompt compiler, CLI, and relevant tests.
  - Confirmed built-in registries were repository-root paths and configured execution bypassed ExecutorManager.
- Files created/modified:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Phase 35-37: Implementation and verification

- **Status:** complete
- Actions taken:
  - Copied built-in registry definitions into `local_engine/resources/` and changed registry defaults to packaged resources while preserving environment overrides.
  - Added `scripts/package_release.py` with exclusions for VCS metadata, virtualenvs, caches, egg-info, build/runtime output, planning scratch files, and old top-level registry folders.
  - Added `local_engine/runtime/pipeline.py` and moved registry loading, run metadata construction, definition hashing, and result summarization out of `Engine.run()`.
  - Routed configured worker creation through `ExecutorManager.worker_factory()` and `ExecutorManager.execute()`.
  - Added explicit `artifact_protocol: local-engine.artifacts.v1` support while retaining legacy fenced-block parsing.
  - Added pytest markers for fast vs integration tests and marked the CLI smoke test as integration.
- Files created/modified:
  - `local_engine/resources/`
  - `local_engine/runtime/pipeline.py`
  - `scripts/package_release.py`
  - Runtime registry, executor, engine, parser, prompt, docs, and test files.
  - Verification passed: focused packaging/artifact/executor/SIP tests, `pytest -m "not integration"`, `pytest -m integration`, full `pytest`, release zip smoke, and `git diff --check`.

## Session: 2026-06-26 — Artifact apply and delivery-status fix

### Phase 29: Artifact apply and delivery-status discovery

- **Status:** complete
- Actions taken:
  - Read the attached failure analysis and requested implementation scope.
  - Restored planning-with-files context from `task_plan.md`, `findings.md`, and `progress.md`.
  - Added Phases 29-33 for ArtifactApplier, CLI semantics, DeliveryStatus, permission/resume behavior, and regression verification.
  - Inspected CLI, Engine, report builder, run store/index/state, retry classification, scheduler persistence, patch collection, safety guards, task templates, skill prompts, and existing MVP/report tests.
  - Confirmed the current implementation only applies unified diff patches in `mode=apply`; generated file-code blocks in SIP bodies are preserved as text and never written to the project.

### Phase 30: ArtifactApplier and manifest

- **Status:** complete
- Actions taken:
  - Chosen approach: add an ArtifactApplier under `local_engine/runtime/` and wire it into the existing post-generation apply point before final report rendering.
  - Added `local_engine/runtime/artifact_applier.py` with file-block extraction, patch collection/application, project-root path validation, explicit dry-run, manifest persistence, and optional verification.
  - Added `DeliveryStatus` and `VerificationStatus` to record whether generated artifacts were only produced, applied, verified, failed, or need human approval.

### Phase 31-32: CLI delivery semantics and resume apply

- **Status:** complete
- Actions taken:
  - Added `local-engine plan`, `local-engine apply --run-id`, `run --plan-only`, `run --yes`, and `run --auto-approve project`.
  - Changed CLI `run` to default to apply delivery semantics while preserving `Engine.run` and `--mode plan/apply` compatibility.
  - Extended final reports, run metadata, run state, CLI summary, and `status` output with `task_graph_status`, `delivery_status`, `files_created`, `files_modified`, `files_not_applied`, `verification_status`, and `user_goal_satisfied`.
  - Made missing approval produce explicit incomplete delivery rather than blocking non-code tasks with an early prompt.

### Phase 33: Regression and smoke verification

- **Status:** complete
- Actions taken:
  - Added `tests/test_artifact_applier.py` covering file-block extraction, manifest writing, verified BUILD apply, and `apply --run-id` resume from plan-only output.
  - Updated README and refactor baseline documentation for the new run/plan/apply semantics.
  - Verification passed: focused artifact/MVP/report tests, full regression suite (`105 passed`), and `git diff --check`.

## Session: 2026-06-26 — Staged Runtime refactor implementation

### Phase 24: P0 architecture and contract baseline

- **Status:** complete
- Actions taken:
  - Read the approved staged refactor plan and restored planning-with-files context.
  - Confirmed the current branch is `codex/staged-refactor`.
  - Inspected current config, run context, CLI, and references to Codex fallback and legacy `task_reports`.
  - Added Phases 24-28 to track P0-P3 implementation and verification.
  - Added `docs/ARCHITECTURE.md`, `docs/RUNTIME_CONTRACTS.md`, and `docs/REFACTOR_BASELINE.md`.
  - Added Runtime contract/state/store/events/executor facade modules and moved new runs to `.local_engine/runs/<run_id>/`.
  - Added built-in hook event recording, loop state fields, `status`, `resume`, `--version`, version/changelog, and default-disabled local telemetry.
  - Removed Codex from default config and built-in agent fallback definitions while retaining explicit fallback compatibility.
  - Updated README, config template, and regression tests for the staged Runtime contract.

## Staged Refactor Verification

| Check | Result |
|------|--------|
| New P0-P3 acceptance tests | passed |
| Full regression suite | 96 passed |
| CLI smoke run/report/status/resume | passed |
| `local-engine --version` | 0.3.0 |
| `git diff --check` | passed |

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
| v0.3.1 focused tests | `./.venv/bin/python -m pytest tests/test_packaging_and_resources.py tests/test_artifact_applier.py tests/test_staged_refactor.py tests/test_sip_parser.py -q` | New packaging/artifact/executor/SIP coverage passes | 29 passed | ✓ |
| v0.3.1 fast split | `./.venv/bin/python -m pytest -m "not integration" -q` | Fast tests pass without integration smoke | Passed | ✓ |
| v0.3.1 integration split | `./.venv/bin/python -m pytest -m integration -q` | Integration smoke passes | 1 passed | ✓ |
| v0.3.1 full regression | `./.venv/bin/python -m pytest -q` | Full suite passes | Passed | ✓ |
| v0.3.1 release zip smoke | `./.venv/bin/python scripts/package_release.py --output /tmp/local-engine-test-release.zip` | Zip excludes dev/runtime trash and ships package resources | Passed | ✓ |
| v0.3.1 whitespace | `git diff --check` | No whitespace errors | Passed | ✓ |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-06-23 | `python: command not found` | 1 | Use `python3` for verification. |
| 2026-06-23 | Primary model routed to installed `claude` in a CLI test | 1 | Restored `claude_command` as the primary command override. |
| 2026-06-23 | Retry-status callback missing in threaded scheduler execution | 1 | Added callback parameter and verified recovery artifacts again. |
| 2026-06-26 | Focused tests used system Python 3.9 and failed on project 3.11 syntax/`tomllib` | 1 | Reran all checks with `./.venv/bin/python`. |

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
