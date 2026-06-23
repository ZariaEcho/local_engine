# Findings & Decisions

## Requirements

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
