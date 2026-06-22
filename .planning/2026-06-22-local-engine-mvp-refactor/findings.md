# Findings & Decisions

## Requirements
- The package is named `local_engine`; runtime uses only the local `claude` CLI and must not contain a Codex adapter.
- The core is a dependency-aware Task Graph Runtime, not a serial workflow runner.
- `local-engine init --project <path>` initializes both project-local `.local_engine/` state and user-level `~/.local_engine/` state.
- `local-engine run` accepts task text plus optional Markdown/TXT input, defaults to `plan`, builds the specified seven-task graph, writes the complete report tree, and can schedule independent tasks in parallel.
- SIP parsing is deliberately permissive: malformed Claude output remains saved and becomes normalized `unstructured` or `error`, never a workflow-failing exception.
- Apply mode needs an explicit confirmation path and must reject dangerous/out-of-root patch effects.
- Tests must cover all listed SIP parsing cases and runtime nonblocking behavior.
- Dynamic Task Graph work replaces the fixed seven-task template as the primary graph source. A pre-scheduler Graph Planner may call the configured Claude worker, but its untrusted output must be persisted, parsed, validated, repaired, and only then scheduled; every planning failure must fall back safely.
- Dynamic graphs support the allowed skills `product`, `backend`, `frontend`, `tester`, `reviewer`, `integrator`, `memory_manager`, `researcher`, `writer`, `designer`, and `data_analyst`. Every valid graph requires integration and durable memory tasks, safe relative output paths, and an acyclic dependency graph.
- The run report gains `internal/` evidence and a user-facing `deliverables/` directory. The final report must disclose graph provenance, planner confidence, task status, outputs, warnings, errors, and next steps.

## Research Findings
- No project files or previous planning files existed in the workspace at the start of this run.
- The available `python3` is 3.9.6; no `python3.10+` executable is on PATH. Typer and PyYAML are installed, but pytest is not.
- The workspace is not a Git repository, so no Git diff/status review is available.
- The CLI smoke run (`init`, then `run --mode plan --workers 4`) created the complete required report tree: seven prompts, fourteen raw/SIP worker files, normalized requirement/graph files, artifacts, integration/eval/memory reports, and final report.
- Parser review found and closed the final fence-handling gap: any labelled code fence now goes through YAML/JSON fallback parsing.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use PyYAML-safe parsing/dumping and plain dataclasses/typed dictionaries | Matches the specified stack and keeps the MVP simple and inspectable. |
| Treat graph task outputs as filesystem paths relative to a run report directory | The objective fixes the output tree and allows stable artifact references. |
| Keep parsers, workers, scheduler, and integration independently testable | This is the cleanest way to enforce tolerance at system boundaries. |
| Declare the requested Python `>=3.11`, but avoid newer syntax internally | The deliverable meets its target contract while the available 3.9 interpreter can still execute tests after dependencies are installed. |
| Use one `ThreadPoolExecutor` batch for each dependency frontier | This executes independent graph nodes concurrently while ensuring downstream prompt generation sees persisted upstream SIP/raw outputs. |
| Retain the seven-node software-development graph as `fallback_template` only | Planning is never allowed to make a run fail; a deterministic graph keeps the MVP usable when the configured worker or its output fails. |
| Keep graph repair deterministic and local | Repairs are auditable, bounded, and avoid a second unreliable LLM call before validation. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Existing active Codex goal prevented creating a second goal | Use the active goal; no goal state was altered. |
| First bulk `apply_patch` rejected an invalid hunk caused by a typo | Corrected the text and split the changes into smaller, verified patches. |
| Git review command returned `fatal: not a git repository` | This is an empty supplied workspace, not an implementation defect; verification used pytest and a temporary CLI project. |

## Dynamic Graph Discovery
- The existing engine writes graph inputs into the report root and invokes `build_task_graph` directly; Graph Planner integration must move input evidence to `internal/`, persist planner raw/SIP/candidate data, and call planner selection before `validate_graph` and scheduling.
- Existing scheduler already executes generic DAG frontiers and preserves error SIPs for dependents. It lacks explicit task states, so the smallest extension is to add status to `TaskResult`/`ExecutionState` without changing the nonblocking behavior.
- Existing integration and reporting make fixed assumptions about reports and list only artifacts/patches. They require graph-aware expected-output classification and deliverable selection.

## Dynamic Graph Implementation Decisions
- `GraphPlanner` is a pre-scheduler worker invocation. It saves prompt/raw/SIP/candidate data below `internal/` and returns an empty candidate rather than raising on failure.
- `validate_graph` remains strict and exception-based for focused unit tests; `validate_and_repair_graph` wraps it with exactly one deterministic repair attempt so the builder can choose a fallback instead of terminating a run.
- `select_task_graph` is the sole graph-source policy boundary: valid candidates get `dynamic`, repaired candidates get `repaired`, and only non-viable candidates get `fallback_template`.
- Existing report-root paths are retained for compatibility. `internal/` now contains planner evidence and `deliverables/` is always provisioned; dynamic tasks may direct their output to either `artifacts/` or `deliverables/`.
- `TaskResult.status` records `completed`, `warning`, or `failed_but_continued`; scheduler dependency release still depends only on execution completion, never output shape.

## Dynamic Graph Verification
- The focused dynamic suite covers valid/invalid validation, deterministic repair, graph-source selection, generic skill prompting, dynamic DAG ordering/parallelism, warning-tolerant downstream scheduling, and the GraphPlanner-to-Engine report path.
- `compileall` and the complete test suite pass: 36 tests.
- A module-invoked CLI smoke run with an isolated fake configured worker generated a `dynamic` three-node analysis graph (`data_analyst → integrator → memory_manager`), normalized the candidate run ID to the active context, and created `deliverables/`, planner evidence under `internal/`, and `final_report.md`.
- A repository scan found no `CodexAdapter` or `codex_adapter` references.

## Context Engine V1 Discovery
- The current engine loads a bounded `context.md` written by `initialize`, then calls the LLM-backed Graph Planner before scheduling. Context Engine V1 instead needs a fresh repository scan, a durable `.local_engine/cache/repo_map.json`, and a deterministic intent graph before workers run.
- Existing graph validation, generic scheduling, report persistence, and prompt compilation are reusable once intent graphs preserve required integrator and memory tasks plus safe output paths.
- The current `artifact_writer` only writes within a run report. Context Engine requires a separate project-local `.local_engine/artifacts/<type>/` store for durable audit, plan, review, test, and documentation outputs.
- `scan` must work on an existing project without a previous `init`; `run` will retain its existing initialized-project safety requirement.

## Context Engine V1 Verification
- `scan_project` persists `.local_engine/cache/repo_map.json` and `write_project_context` persists `.local_engine/PROJECT_CONTEXT.md`; scanning recognizes the requested source, Markdown, YAML, and JSON extensions while reporting only programming languages in the language summary.
- Intent graphs cover `AUDIT`, `PLAN`, `LEARN`, `BUILD`, `TEST`, `DOCUMENT`, `REFACTOR`, and `RESEARCH`; all receive integration and memory tasks required by the existing DAG validator.
- `Engine.run` now uses the intent pipeline as its primary graph source and injects both `PROJECT_CONTEXT.md` and a compact repository summary into worker prompts. The prior Graph Planner code is retained but no longer determines the normal Context Engine topology.
- Durable artifacts are copied to `.local_engine/artifacts/<type>/` in addition to run-level deliverables. Audit and learn requests produce `AUDIT_REPORT.md` and `KNOWLEDGE_GAP.md` respectively.
- Full compilation and tests pass: 50 tests. CLI tests invoke `scan`, `inspect`, `graph`, and isolated fake-worker `run` commands without calling an external Claude process.

## Resources
- Goal objective: `/Users/echo/.codex/attachments/784af4ec-e9fb-4d5f-bd91-ef7c09e65016/goal-objective.md`

## Execution Engine P0 Intake (2026-06-23)
- The requested P0 scope is a complete observable run lifecycle: Dynamic Task Graph execution, one prompt per task, one Claude CLI invocation per task, task-level Markdown output, user-facing deliverables, structured intermediate artifacts, a final report, terminal summary/status, durable failure logs, report lookup, health diagnostics, and Rich live status.
- Existing Context Engine functionality already constructs and validates deterministic Dynamic Task Graphs, persists prompts and `agent_outputs`, writes task-derived files under `artifacts/`/`deliverables/`, and produces a final report. The P0 work must strengthen contracts and observability rather than replace the graph pipeline.

## Execution Engine P0 Design
- Keep the validated intent graph and frontier scheduler. Add only an event callback to the run/scheduler seam so CLI rendering observes real scheduler transitions without becoming a dependency of execution.
- Preserve the raw (`.raw.txt`) and normalized (`.sip.yaml`) worker evidence, and additionally write `agent_outputs/<task_id>.md` as a readable per-task record. This makes the requested output contract explicit without weakening downstream parsing.
- Treat a `WorkerResult.failed` as a terminal task failure but not a DAG failure: continue independent/downstream work, persist `error.log`, expose the message in `final_report.md`, and return statuses to the CLI. A malformed but successful Claude response remains a warning, not a CLI failure.
- Generate two durable run-level products after scheduling: `artifacts/task_results.yaml` as structured intermediate evidence, and `deliverables/FINAL_DELIVERY.md` as an always-present final handoff index. This closes the empty-deliverables gap for graph shapes such as BUILD.
- Store final global run metadata with report paths and statuses. `report --latest` resolves it without requiring the original project path; an optional project scope can instead search that project’s reports.
- `doctor` is intentionally read-only apart from idempotent runtime-home initialization. It reports Python, configuration, filesystem, Rich, and configured Claude-command availability; it surfaces warnings but remains usable even before Claude is installed.

## Execution Engine P0 Verification
- The scheduler still completes the validated intent-generated Dynamic Task Graph in dependency order, while emitting non-blocking `graph_ready`, `task_started`, and `task_finished` events to the CLI renderer.
- Every executed task writes `prompts/<task_id>.prompt.md`, invokes a worker instance, and now persists readable `agent_outputs/<task_id>.md` alongside retained raw and SIP evidence. Focused coverage asserts the worker/prompt/Markdown-output task-id sets are identical.
- Every run writes `artifacts/task_results.yaml` and `artifacts/execution_manifest.yaml`; every run also writes `deliverables/FINAL_DELIVERY.md` even if no task individually declares a user-facing document.
- A simulated Claude CLI exit persists a per-task failure record, root `error.log`, detailed final-report error entry, terminal Rich failure notice, all task statuses, and exits the CLI with code 1 only after the graph/report lifecycle finishes.
- `report --latest` reads completed report metadata from the global run index and `doctor` renders readiness for Python, runtime home, config, Rich, and the configured Claude command.
- Final verification: `.venv/bin/python -m compileall -q local_engine tests` and `.venv/bin/python -m pytest -q` both succeed; the suite reports **53 passed**. A direct `doctor` invocation under an isolated runtime home reported all checks as OK in the local test environment.
