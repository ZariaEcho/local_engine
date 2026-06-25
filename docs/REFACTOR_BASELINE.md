# Refactor Baseline

This baseline records the current implementation shape before the staged Runtime
refactor. It is descriptive, not aspirational.

## Current Center Of Gravity

`local_engine/runtime/engine.py` currently owns most Runtime responsibilities.
It coordinates request classification, graph creation, scheduling, worker
selection, recovery, quality, review, artifact writing, durable artifact storage,
patch application, report generation, and run-index finalization.

## Current `Engine` Responsibilities

- Create project-local state through `initialize`.
- Create a run and reserve a global run-index record.
- Save `raw_input.md`, `normalized_requirement.yaml`, repository context, graph
  quality, context quality, and project type artifacts.
- Classify intent and handle clarification.
- Build intent or direct-skill task graphs.
- Resolve task capabilities from agent and skill registries.
- Build prompts for each task.
- Choose Claude/mock workers through worker factories.
- Invoke the scheduler and worker recovery path.
- Run quality evaluation, review, and revision before dependency release.
- Write task summaries, context patches, quality artifacts, retry evidence, and
  review evidence.
- Collect patches and optionally apply them in apply mode.
- Save deliverables and durable project artifacts.
- Generate `integration_review.md`, `eval_report.md`, `memory_update.md`,
  `error.log`, `deliverables/FINAL_DELIVERY.md`, and `final_report.md`.
- Finalize global run-index records.

## Current Run Directories

Project-local run output currently defaults to:

```text
<project>/.local_engine/task_reports/<run_id>/
```

Global run records currently live under:

```text
~/.local_engine/runs/
```

The staged refactor changes new project-local run output to:

```text
<project>/.local_engine/runs/<run_id>/
```

Legacy `task_reports` directories remain readable.

## Current Worker And Fallback Model

The current worker interface is named `Worker` and exposes:

```python
run(prompt, task, project_root) -> WorkerResult
```

The current default config includes `claude_command`, `model_commands.codex`,
and `execution.fallback_model: codex`. The refactor removes Codex from defaults
and introduces executor-oriented configuration. Codex remains possible only when
the user explicitly configures it.

## Current Recovery, Quality, And Cache

- Recovery is deterministic by failure type and writes retry/error artifacts.
- Quality evaluation is non-blocking but controls review, context patches, and
  cache verification.
- Review/revision is already performed before downstream task release.
- Verified task cache uses repository and watched-path fingerprints.

## Current CLI Surface

Top-level commands include `init`, `scan`, `inspect`, `graph`, `report`,
`doctor`, and `run`. A `runs` command group provides global index inspection and
recovery. The refactor adds top-level `status` and `resume`, plus `--version`.

## Current Smoke Baseline

The required smoke path is:

```bash
local-engine run --project <demo> "审计这个项目"
local-engine report --latest --project <demo>
```

