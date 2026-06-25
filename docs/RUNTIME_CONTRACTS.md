# Runtime Contracts

This document freezes the public v1 Runtime contract used during the staged
refactor. Field names are intentionally conservative; compatibility adapters may
expose older names while the Runtime moves to these contracts.

## NormalizedRequirement

The normalized user request.

- `raw_requirement`: full normalized requirement text.
- `task_text`: direct CLI text, when supplied.
- `source_type`: `text`, `file`, or combined input source.
- `user_goal`: concise user-visible goal.
- `real_goal`: runtime interpretation of the requested outcome.
- `success_definition`: criteria the final run should satisfy.

## TaskGraph

The executable DAG produced by planning.

- `run_id`: target run identifier.
- `metadata`: intent, classification, graph source, project type, and warnings.
- `tasks`: ordered list of `Task` objects.

## Task

One node in the task graph.

- `id`: stable task identifier within a run.
- `title`: human-readable title.
- `skill`: capability used to compile the prompt.
- `agent`: optional agent/executor profile.
- `depends_on`: task IDs that must finish first.
- `task_type`: semantic category such as review, implementation, test, or doc.
- `expected_output`: type/path declaration for artifacts or deliverables.
- `constraints`: task-local must/must-not rules.
- `risk_tags`: optional quality/review triggers.

## RuntimeInput

Input to the Runtime entrypoint.

- `project_root`: initialized project path.
- `raw_input`: original user input or loaded file content.
- `config`: merged runtime configuration.
- `run_id`: optional caller-provided run ID.
- `resume`: whether to resume an existing state.

## RuntimeOutput

Runtime completion result.

- `run_id`: run identifier.
- `status`: terminal or current run status.
- `run_dir`: project-local run directory.
- `final_report_path`: final report path, when available.
- `deliverables_dir`: deliverables directory.
- `state`: latest `RunState`.

## ExecutorRequest

One task invocation request.

- `task_id`: task being executed.
- `prompt`: compiled prompt or command input.
- `project_root`: project working directory.
- `run_dir`: current run directory.
- `timeout_seconds`: execution timeout.
- `env`: sanitized executor environment.
- `metadata`: task, agent, skill, mode, and retry metadata.

## ExecutorResult

One task invocation result.

- `task_id`: task that ran.
- `status`: `completed`, `failed`, `timeout`, or `needs_human`.
- `stdout`: captured standard output.
- `stderr`: captured standard error.
- `raw_output`: canonical raw model/command output.
- `parsed_output`: SIP or normalized structured output.
- `artifacts`: paths created by the executor.
- `error`: normalized error detail, when present.

## RunState

Machine-readable run status persisted as `state.json`.

- `run_id`: run identifier.
- `status`: `running`, `completed`, `partial`, `failed`, `interrupted`, or
  `needs_human`.
- `phase`: current Runtime phase.
- `run_dir`: project-local run directory.
- `created_at`: creation timestamp.
- `updated_at`: latest state timestamp.
- `tasks`: per-task status, lifecycle, attempts, hooks, loop rounds, and errors.
- `hooks`: hook event records.
- `loops`: loop/revision records.
- `artifacts`: latest `ArtifactManifest` summary.

## ArtifactManifest

Structured index of run evidence.

- `run_id`: run identifier.
- `task_graph`: relative path to `task_graph.yaml`.
- `state`: relative path to `state.json`.
- `prompts_dir`: relative prompts directory.
- `agent_outputs_dir`: relative task output directory.
- `artifacts_dir`: relative artifacts directory.
- `deliverables_dir`: relative deliverables directory.
- `final_report`: relative final report path.

## FinalReport

Human-readable explanation of the run.

- Run identity, project, intent, and status.
- Task status table with lifecycle, quality, review, cache, hook, and loop state.
- Deliverables and artifact links.
- Warnings, failures, needs-human items, and recovery evidence.
- Context-quality and graph-quality evidence.

