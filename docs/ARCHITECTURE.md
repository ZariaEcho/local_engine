# local-engine Architecture

`local-engine` is a local task-graph Runtime.

The Runtime is the center of the system. Agents, executors, planners, reports,
and artifacts exist to support deterministic local task-graph execution.

## Core Principle

```text
Natural language requirement
-> Planner builds a TaskGraph
-> Runtime executes the TaskGraph
-> Executor runs one task
-> Artifact layer saves evidence and deliverables
-> Report layer explains the result
```

The center is not a multi-agent society. The center is the local Runtime that
turns one request into a traceable, recoverable, deliverable execution result.

## Responsibilities

Planner:

- Normalizes the user requirement.
- Selects or builds a task graph.
- Produces a validated `TaskGraph`.

Runtime:

- Creates and resumes runs.
- Owns run state, event emission, scheduling, recovery, quality gates, and final
  run status.
- Coordinates executors without leaking graph/report responsibilities into them.

Executor:

- Receives one `ExecutorRequest`.
- Runs one external or in-process task.
- Returns one `ExecutorResult`.
- Does not know the full `TaskGraph`, global Runtime state, final report shape,
  or cache policy.

Agent:

- A declarative execution profile: executor, role, prompt, tools, skills, and
  constraints.
- Agents are configuration for execution, not the architectural center.

Artifact:

- Persists prompts, raw outputs, parsed outputs, errors, quality records,
  delivery documents, and state snapshots.

Report:

- Reads run evidence and explains what happened.
- Reports do not own task execution.

## v1 Non-Goals

The v1 Runtime does not include:

- Web UI or dashboards.
- Agent society simulation.
- Knowledge-base systems beyond simple local memory files.
- Complex plugin marketplaces.
- Remote telemetry or automatic upload.
- Default Codex fallback.

## Runtime-Centered Directory Model

New runs are written under:

```text
<project>/.local_engine/runs/<run_id>/
```

Legacy reports under `.local_engine/task_reports/<run_id>/` remain readable for
compatibility, but new run creation should use `runs/<run_id>/`.

