# local_engine

`local_engine` is a local repository-aware Context Engine. It classifies a text or Markdown/TXT requirement, scans the target repository, builds `PROJECT_CONTEXT.md`, selects an intent-specific task graph, injects that context into each worker prompt, and writes reviewable reports plus durable project artifacts.

Codex develops this engine; at runtime, the engine calls only the local `claude` CLI. There is deliberately no Codex adapter.

## Install

Requires Python 3.11+.

```bash
python -m pip install -e '.[dev]'
```

The `claude` executable must be installed and available on `PATH` for production runs.

## Usage

Initialize a target project once:

```bash
local-engine init --project ./demo_project
```

Inspect repository context without invoking a worker:

```bash
local-engine scan --project ./demo_project
local-engine inspect --project ./demo_project
local-engine graph --project ./demo_project "审计这个项目"
local-engine doctor
```

`scan` writes `.local_engine/cache/repo_map.json` and `.local_engine/PROJECT_CONTEXT.md`. `graph` previews the deterministic graph for intents such as `AUDIT`, `PLAN`, `LEARN`, `BUILD`, `TEST`, `DOCUMENT`, `REFACTOR`, and `RESEARCH`.

Run a text requirement (plan mode is the default):

```bash
local-engine run --project ./demo_project "Generate an optimization plan"
local-engine run --project ./demo_project "Generate an optimization plan" --mode plan
```

Add a Markdown or TXT input file:

```bash
local-engine run --project ./demo_project --input ./requirement.md "Prioritize the attached brief"
```

Choose worker concurrency:

```bash
local-engine run --project ./demo_project "Implement the requested feature" --workers 4
```

Every task is shown in a Rich progress/status stream. The final terminal summary always includes the `run_id`, `report_dir`, `final_report`, and each task status. A failed Claude invocation completes the rest of the runnable graph, writes failure evidence, and makes `run` exit non-zero after the summary.

Open the latest completed report from any initialized project without remembering its path:

```bash
local-engine report --latest
```

`--mode apply` collects all generated diffs and asks for confirmation before applying them. Use `--yes` only when approval has already been granted:

```bash
local-engine run --project ./demo_project "Implement the requested feature" --mode apply --yes
```

## Run output

Each run is stored at `<project>/.local_engine/task_reports/<run_id>/`:

```text
raw_input.md
normalized_requirement.yaml
task_graph.yaml
deliverables/
internal/graph_planner_prompt.md
internal/graph_planner.raw.txt
internal/graph_planner.sip.yaml
internal/graph_planner_candidates.yaml
internal/graph_repair_report.md
prompts/<task_id>.prompt.md
agent_outputs/<task_id>.raw.txt
agent_outputs/<task_id>.sip.yaml
agent_outputs/<task_id>.md
patches/<task_id>.patch
artifacts/<task_id>.md
artifacts/task_results.yaml
artifacts/execution_manifest.yaml
integration_review.md
eval_report.md
memory_update.md
final_report.md
error.log                         # present only when a Claude CLI call fails
```

The run pipeline is:

```text
User Input → Intent Classifier → Repository Scanner → Context Builder
→ Dynamic Task Graph → Scheduler → Claude Workers → Artifact Store → Final Delivery
```

`agent_outputs/<task_id>.md` is the readable task record; the matching `.raw.txt` and `.sip.yaml` files preserve raw and normalized evidence. `deliverables/FINAL_DELIVERY.md` is generated for every run, including graph types whose only requested output is a patch. `artifacts/task_results.yaml` and `artifacts/execution_manifest.yaml` keep structured intermediate execution state.

Durable curated artifacts are also stored in `<project>/.local_engine/artifacts/` under `audit/`, `plan/`, `review/`, `test/`, and `docs/`. For example, an audit run produces `deliverables/AUDIT_REPORT.md` in its run report and `.local_engine/artifacts/audit/AUDIT_REPORT.md` in project-local storage.

Global runtime state lives under `~/.local_engine/` (override with `LOCAL_ENGINE_HOME` for isolated automation).

## Safety boundaries

- Plan mode never applies generated patches to project source.
- Apply mode requires explicit approval.
- Workers run with the target project as their current directory.
- Patches outside the project root, `.git/` changes, file deletions, `rm -rf`, `git push`, and destructive SQL are rejected.
- The engine writes only its project-local `.local_engine/` directory and its global `~/.local_engine/` directory.

## SIP: wide input, strict internal output

Claude output is never treated as trustworthy. The parser accepts direct YAML/JSON, fenced blocks, and embedded objects; if none parse, it saves the original response and emits a valid `unstructured` SIP object. Empty or subprocess-failed responses become valid `error` SIP objects. Formatting mistakes are warnings in `final_report.md`, not workflow failures.
