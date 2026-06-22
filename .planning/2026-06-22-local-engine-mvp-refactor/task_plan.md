# Task Plan: local_engine Task Graph Runtime MVP

## Goal
Deliver a tested repository-aware Context Engine: classify a user request, scan and summarize the target repository, build an intent-appropriate task graph, inject that context into every worker prompt, execute the resulting DAG safely, and persist durable artifacts without implementing a Codex adapter.

## Current Phase
Complete

## Phases

### Phase 1: Requirements & Discovery
- [x] Read the full goal objective
- [x] Identify the non-negotiable runtime, safety, output, and testing boundaries
- [x] Inspect the current workspace and existing reusable implementation (if any)
- [x] Document findings and choose the smallest compliant architecture
- **Status:** complete

### Phase 2: Package Scaffold & Core Contracts
- [x] Create the package, Typer entry point, templates, and project/global state layout
- [x] Define graph and SIP schemas plus configuration and run-context helpers
- [x] Implement file/text intake and deterministic MVP graph construction
- **Status:** complete

### Phase 3: Runtime Implementation
- [x] Compile contract-complete worker prompts with dependency output warnings
- [x] Implement Claude CLI and mock workers, tolerant SIP parsing, and dependency-aware parallel scheduler
- [x] Implement artifacts, integration/conflict review, memory updates, permissions, and plan/apply flow
- [x] Wire the complete `init` and `run` CLI commands
- **Status:** complete

### Phase 4: Testing & Verification
- [x] Add focused SIP parser coverage for every required fallback/type-normalization case
- [x] Add CLI/runtime tests using MockWorker (including parallel dependencies and nonblocking malformed outputs)
- [x] Run pytest and manual smoke tests against a temporary demo project
- [x] Review generated report tree and safety invariants
- **Status:** complete

### Phase 5: Delivery
- [x] Review the implementation, README, and acceptance criteria
- [x] Summarize completed MVP and remaining known limits
- **Status:** complete

### Phase 6: Dynamic Graph Design & Contracts
- [x] Read the Dynamic Task Graph task brief and inspect the MVP runtime
- [x] Define planner, validator, repair, fallback, and report contracts
- [x] Update run-context layout for internal evidence and deliverables
- **Status:** complete

### Phase 7: Dynamic Planning and Runtime Integration
- [x] Implement the Graph Planner prompt/execution/persistence path
- [x] Implement validator/repair/fallback selection before scheduling
- [x] Adapt compiler, scheduler status handling, integration, artifacts, and final reporting
- **Status:** complete

### Phase 8: Dynamic Graph Test Suite & Verification
- [x] Add graph validation, repair, builder/planner, prompt, scheduler, and end-to-end tests
- [x] Run the full suite and a representative plan-mode smoke test
- [x] Inspect output tree and acceptance invariants
- **Status:** complete

### Phase 9: Context Engine Design & Contracts
- [x] Read the Context Engine V1 brief and inspect the current runtime boundaries
- [x] Define repository-map, project-context, intent, artifact-store, and graph-preview contracts
- [x] Decide how deterministic intent graphs coexist with the existing Graph Planner
- **Status:** complete

### Phase 10: Context Pipeline Implementation
- [x] Implement repository scanning, context building, intent classification, and dynamic intent graph construction
- [x] Wire context injection, artifact persistence, and the Context Engine run path
- [x] Add `scan`, `inspect`, and `graph` CLI commands
- **Status:** complete

### Phase 11: Context Engine Tests & Verification
- [x] Add scanner, context, intent, graph, artifact, engine, and CLI coverage
- [x] Run the full suite and Context Engine CLI smoke commands
- [x] Inspect cache, project context, artifacts, and generated reports
- **Status:** complete

### Phase 12: Execution Engine P0 Discovery & Design
- [x] Trace the current run lifecycle, filesystem layout, worker failure paths, and CLI entry points
- [x] Define the run-manifest, failure-reporting, report lookup, doctor, and Rich status contracts
- **Status:** complete

### Phase 13: Execution Engine P0 Implementation
- [x] Persist every task prompt, Claude result, structured artifact, final deliverable, and final report
- [x] Ensure task failures are surfaced in terminal, error.log, status state, and final_report without blocking independent work
- [x] Add `local-engine report --latest`, `local-engine doctor`, and Rich progress/status reporting
- **Status:** complete

### Phase 14: P0 Regression Coverage & Verification
- [x] Add focused unit/CLI/end-to-end coverage for the complete execution contract
- [x] Run the full suite and isolated fake-Claude smoke tests for success and failure paths
- **Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Build a deterministic seven-task MVP graph | The objective specifies this fixed default topology and it makes the engine testable without LLM graph-planning behavior. |
| Inject a worker factory for tests | Runtime must call Claude CLI in production, while test runs need a reliable MockWorker without a Claude installation. |
| Make all worker results pass through `parse_sip` | SIP must be a normalization layer, never an execution gate. |
| Keep apply approval explicit and noninteractive by default | The safety boundary requires confirmation before applying patches. |
| Normalize a planner-supplied run ID to the active run context | Prevents stale or malformed planner output from creating mismatched report identities while retaining the raw candidate for audit. |
| Use deterministic intent graphs as the Context Engine primary path | The requested pipeline places intent classification before graph construction; Claude remains a task executor, while the prior Graph Planner stays available as a separate compatible component. |
| Treat Markdown/YAML/JSON as supported files but not programming languages | `inspect` therefore reports a Python algorithms repository as `Languages: Python` even when it also contains README or configuration files. |

## Errors Encountered
| Error | Resolution |
|-------|------------|
| `create_goal` reported an unfinished goal | The app has already activated the submitted `/goal`; continue under that goal. |
| Only Python 3.9.6 is on PATH, while the target requires 3.11+ | Declare Python 3.11+ in package metadata but keep implementation syntax 3.9-compatible, then use an isolated test environment if possible. |
| Initial bulk patch was malformed because of a stray character in prompt text | Split the patch into focused file groups and re-applied the corrected compiler. |
| `git status` cannot run because the supplied empty workspace is not a Git repository | Use direct filesystem, test, and smoke-run checks rather than a diff-based review. |
| Dynamic graph task brief is substantially broader than the completed MVP | Extend the existing scoped plan with Phases 6–8; retain the software-development graph only as a tested fallback. |
| Two initial large patches were rejected by `apply_patch` formatting validation | Split the migration into smaller, independently applied patches; no code was changed by the rejected attempts. |
| Direct CLI smoke initially resolved an installed `claude` executable | Terminated the run before model work and used an isolated fake local CLI via the normal configured-worker path instead. |
| A combined plan/progress update used a stale progress-file hunk | Re-read the active files and split the completion update into focused patches. |
| Two legacy regression assertions expected Graph Planner and the seven-node fallback topology | Updated them to test the new intent-first AUDIT and PLAN graph contracts. |
| Initial P0 planning-file patch used a stale anchor in `findings.md` | Re-read the active plan files and apply a focused patch with verified anchors. |
| First P0 engine patch left an unmatched closing parenthesis | Inspected the precise source lines, removed the stray token, then re-ran compilation and the complete test suite. |
