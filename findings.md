# Findings & Decisions

## Requirements

- 2026-06-26 staged refactor implementation:
  - Treat `local-engine` primarily as a local task-graph Runtime, not a multi-agent cluster.
  - Freeze P0 architecture, runtime contracts, engine baseline, and smoke paths before deep refactoring.
  - Split runtime responsibilities behind compatibility APIs while keeping `Engine` usable.
  - Move new project-local runs from `.local_engine/task_reports/<run_id>/` to `.local_engine/runs/<run_id>/` and preserve legacy reads.
  - Remove default Codex fallback; Codex must be explicitly configured as an optional executor.
  - Rename worker semantics toward executor contracts and add typed request/result interfaces.
  - Add top-level lifecycle commands: `status`, `resume`, and `--version`.
  - Implement only built-in Hook events for P2, with no plugin marketplace or arbitrary shell hook.
  - Implement Loop as a quality/review-driven closed loop with state/final-report evidence.
  - Add default-disabled local-only telemetry that never captures prompts, source code, raw user requests, paths, environment, or model output.

- 2026-06-25 MVP real-run fixes:
  - Detect algorithm repositories from project files/keywords and persist `artifacts/project_type.json`.
  - Build BUILD graphs from intent, project type, and requirement; algorithm repositories must not receive generic backend/frontend/api/ui/database tasks unless explicitly requested.
  - Use capability-oriented task/skill names for new graphs (`code_generation`, `test_generation`, implementation semantics) while keeping old aliases compatible.
  - In apply mode, propagate approved write permission into every executable prompt through a run execution contract.
  - Classify permission/clarification/tool requests distinctly from FORMAT errors and route approved permission requests to execution-contract retry.
  - Always produce recoverable partial/failure/interrupted reports; `report --latest` should recover the latest run even if it is not completed.
  - Improve progress visibility with task ID, elapsed time, attempt, worker, last output age, and a long-running warning after five minutes.
- P1-Control adds classification confidence/clarification, graph semantic checks, direct dependency summaries, and non-blocking output-quality evaluation.
- P1-5 adds a non-blocking Context Quality Gate: detect the relevant core paths, dependency/entry/test coverage, and size risk; persist artifacts and inject warnings into all task prompts.
- P1-6 standardizes error evidence, makes warnings visible in the final report, expands Doctor diagnostics, and verifies the complete local run lifecycle.

- Dynamically load agent YAML files from `agents/`.
- Dynamically load reusable skill directories from `skills/`.
- Add registry inspection CLI commands and direct `run --skill` support.
- Add retry, model and prompt fallback, persisted failure artifacts, and graph continuation.
- Add configurable review/revision loop, files, states, and final reporting.
- Add a JSON global run index and `runs` CLI without losing legacy run metadata.
- Move all eight built-in intents and their task shapes into validated YAML registries.
- Replace generic retries with deterministic typed recovery.
- Gate review conditionally before downstream tasks are released.
- Reuse only verified task results using repository and watched-path fingerprints.

## Research Findings

- The latest real-run failure mode is a semantic graph mismatch: algorithm repositories can be misrouted into a generic BUILD graph containing backend/frontend tasks.
- Apply-mode worker prompts need an explicit write-permission contract because the external Claude worker can otherwise ask for permission even after the CLI approval flow.
- Permission requests are meaningful worker-control signals and should not be collapsed into SIP/format parsing errors.
- Report recovery must tolerate runs that end outside the normal completed path and should use the latest run index entry even when `final_report.md` is missing.
- `intents/build.yaml` currently requires `build_backend`, `build_frontend`, and `build_test`; `task_templates/builtin.yaml` maps them to task IDs `backend`, `frontend`, and `test`, with the test task depending on both web-style implementation tasks.
- `Engine.run` already accepts `apply_approved`, but the value is only checked immediately before applying collected patches; it is not injected into worker prompts.
- `compile_task_prompt` is the central insertion point for an execution contract because main tasks, reviews, and revisions all call it.
- `FailureType` currently only supports network/format/logic/timeout/unknown, and `classify_failure` checks unstructured output after SIP parsing, causing plain permission requests to become `format`.
- Report recovery already exists in `runtime/reporting.py` and `_finalize_recoverable_run`, but this run will verify it still handles running records with blank report paths.
- `RunProgress` currently displays only `task_id running` plus Rich elapsed time; scheduler events do not include attempt number, worker/model, or last output age.

- `RepoInfo.files` is intentionally bounded to source/document formats and currently does not make dependency-manifest coverage explicit in `PROJECT_CONTEXT.md`; the new gate should inspect project-relative paths directly and add a manifest inventory to the context builder.
- Prompt compilation is centralized in `compile_task_prompt`; adding an optional context-warning argument there covers primary task, review, and revision prompts without changing worker contracts.
- A run has one `RunContext` and already owns `artifacts/`, while retry code already writes per-task JSON under `artifacts/errors/`; it needs the requested normalized top-level error fields rather than a second persistence system.
- Final-report warnings are assembled in `Engine.run`, so classification, graph, context, task quality, retry, skipped, and failed signals should be normalized into that one list before rendering.
- `inspect` uses `Engine.scan` and `doctor` currently only checks runtime home/config/Rich/Claude. Both have clear integration points for the requested diagnostics.

- Current intent selection returns only a string and silently defaults to PLAN; the Engine has no pre-graph clarification boundary.
- Current prompt compilation includes raw direct dependency evidence and review context patches, but does not persist or inject a concise task-summary artifact.
- Existing `artifacts/quality/<task_id>.json` is review-trigger evidence, so P1-Control must merge rather than overwrite the review-compatible fields.
- Task-cache verification currently relies on successful lifecycle only; it must additionally require complete, sufficiently confident output quality.
- Classification evidence now lives under `intents/` (rather than `planner/`) so the Registry can return typed results without a package import cycle.
- The semantic graph gate compares generated task templates with intent-required templates and their declared dependencies; it intentionally leaves duplicate work as a warning rather than blocking execution.
- Output quality remains separate from the pre-existing review trigger score: it is persisted in the unified quality artifact and gates cache reuse without blocking the current run.

- `Engine.run` builds an intent-specific graph, compiles prompts, delegates dependency scheduling to `ParallelScheduler`, then writes final artifacts and reports.
- Graph task `skill` currently doubles as the hard-coded role identifier. `graph_validator.ALLOWED_SKILLS`, `prompt_compiler.ROLE_BY_SKILL`, and `templates/skills/` encode the fixed catalog.
- `ParallelScheduler` already continues downstream tasks after worker failure and persists readable/raw/SIP task evidence, but lacks retry/fallback hooks.
- Global config is YAML and uses defaults in `runtime/config.py`; per-run output directory handling is centralized in `RunContext`.
- Existing tests directly call graph validation and scheduler APIs, so P1 must retain their existing task shape and status semantics where possible.
- P1 now uses YAML registries for default and custom capabilities (`LOCAL_ENGINE_AGENTS_DIR` / `LOCAL_ENGINE_SKILLS_DIR` can override them), while graph tasks resolve their executing agent from the selected skill's `default_agent`.
- Recovery attempts create `artifacts/errors/<task_id>.log` on failure and always preserve a structured `artifacts/retries/<task_id>.json` trace.
- Review records are written per round, with `artifacts/review_summary.json` and `artifacts/task_results.yaml` exposing lifecycle status, rounds, and unresolved issues.
- Current global run discovery stores one `run_metadata.yaml` under each run directory; there is no `index.json`, `latest.json`, or `runs` command group.
- Intent classification and graph shapes remain hard-coded in `intent_classifier.py` and `dynamic_builder.py`.
- Current recovery treats all worker failures identically, while FORMAT is only recognized after retries have already finished.
- Current review runs after the entire scheduler completes, so revisions cannot affect downstream prompts.
- The repository scanner records structure but not content hashes; cache invalidation requires a new full-tree fingerprint.
- `RunIndex` can reserve readable IDs safely with exclusive JSON creation and regenerate `index.json`/`latest.json` from canonical per-run records, avoiding dependence on a mutable counter.
- Intent task entries can refer to named Task Templates while each template exposes a semantic `task_type`; this preserves intent-specific IDs/paths and still keeps Skill matching decoupled.
- Typed recovery needs to classify successful-but-malformed responses as failures before a task is released; `failure_type` therefore belongs on both `WorkerResult` and normalized SIP data.
- A Scheduler result-finalizer hook lets Engine run quality/review/revision inside the worker future; dependents remain blocked until the finalized result and context patch are persisted.
- Cache lookup must reject a dependent task whenever any direct upstream task executed in the current run; otherwise a stale downstream result could be reused despite changed dependency output.
- Per-task cache entries can store raw/SIP/quality evidence and be materialized into a fresh run, preserving auditability while avoiding worker execution.
- The completed runtime preserves legacy execution statuses while publishing the richer lifecycle, quality, failure, and cache provenance alongside them.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Low-confidence non-interactive calls fail with candidates | Prevents a silent wrong graph; callers can retry with `--intent`. |
| Only direct dependency summaries are injected | Preserves DAG causality and avoids nondeterministic context from parallel siblings. |
| Graph errors block; output-quality issues warn | Matches the P1-Control control boundary without discarding useful worker output. |

| Decision | Rationale |
|----------|-----------|
| Additive P1 changes | Existing tests and command usage should stay valid. |
| Keep graph task `skill`; add optional `agent` | Avoid a breaking schema change while separating selected skill from the agent that executes it. |
| Keep legacy task graph skill aliases available | Existing intent graphs and their tests remain valid while direct `--skill` runs use the new skill registry. |
| Preserve `TaskResult.status` for existing API users; add lifecycle and review fields | The new review state machine is visible without breaking callers expecting `completed` / `failed_but_continued`. |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| Shell does not expose a `python` executable | Use `python3` for compilation and pytest. |
| New model command defaults shadowed legacy `claude_command` | Preserve `claude_command` precedence for model `claude`, so existing config and tests continue to route primary calls correctly. |
| Retry status callback omitted from scheduler execution method | Pass it through the worker-execution call so failed-attempt stderr can be shown safely. |

## Resources

- `local_engine/cli.py`
- `local_engine/runtime/engine.py`

## Visual/Browser Findings

- Not applicable.
