# local_engine

`local_engine` is a local repository-aware, closed-loop Agent Runtime. It composes declarative intents, task templates, skills, and agents; classifies failures; gates quality before releasing downstream work; and reuses only verified results whose repository inputs remain valid.

The primary worker is the local `claude` CLI. Agents may declare another configured CLI model as a bounded fallback; there is no in-process model adapter.

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

## Declarative runtime registries

The runtime loads four independent declaration layers:

- `intents/*.yaml` selects required and optional task templates.
- `task_templates/*.yaml` defines task IDs, dependencies, output paths, constraints, and risk tags.
- `skills/*/skill.yaml` plus `prompt.md` declares a task capability, review policy, priority, and optional cache paths.
- `agents/*.yaml` declares which skills an executor supports and its model/retry limits.

Override them with `LOCAL_ENGINE_INTENTS_DIR`, `LOCAL_ENGINE_TASK_TEMPLATES_DIR`, `LOCAL_ENGINE_SKILLS_DIR`, or `LOCAL_ENGINE_AGENTS_DIR`.

```bash
local-engine agents list
local-engine agents show backend
local-engine skills list
local-engine skills show audit_repo
```

Run a selected skill directly instead of letting the intent classifier choose a graph:

```bash
local-engine run --project ./demo_project --skill audit_repo "审计这个项目"
```

`prompt.md` supports strict `{{ variable }}` substitutions. The runtime provides task, requirement, context, dependency, mode, and expected-output variables when it renders a skill prompt.

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

Recovery is selected by failure type: NETWORK reuses the original prompt, FORMAT adds a strict SIP contract, TIMEOUT compacts context and extends the timeout, LOGIC stops for human intervention, and UNKNOWN receives at most one conservative retry. NETWORK/TIMEOUT may use the configured fallback model after their retry budget.

```yaml
execution:
  max_retries: 2
  fallback_model: codex
  fallback_prompt: true
  continue_on_failure: true
  timeout_multiplier: 1.5
review:
  enabled: true
  reviewer_agent: reviewer
  review_skill: review_code
  threshold: 0.75
  max_rounds: 2
  risky_task_types: [backend, frontend, refactor]
  risky_tags: [code_change, architecture, security]
```

Review runs only when explicitly required, confidence is below the threshold, risk type/tags match, the output changes code, or SIP warnings are present. Review and revision finish before dependents run. Low-quality results produce a context patch that asks downstream tasks to revalidate affected conclusions.

Open the latest completed report from any initialized project without remembering its path:

```bash
local-engine report --latest
local-engine runs list
local-engine runs latest
local-engine runs show 2026-06-23-001
local-engine runs open 2026-06-23-001
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
repo_context.json
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
reviews/<task_id>.round1.md
reviews/<task_id>.round2.md            # when a revision is required
patches/<task_id>.patch
artifacts/<task_id>.md
artifacts/task_results.yaml
artifacts/execution_manifest.yaml
artifacts/quality/<task_id>.json
artifacts/context_patches/<task_id>.md # when compensation is required
artifacts/errors/<task_id>.json        # classified failure evidence
artifacts/errors/<task_id>.log         # present for every worker failure
artifacts/retries/<task_id>.json       # recovery attempt trace
integration_review.md
eval_report.md
memory_update.md
final_report.md
error.log                         # present only when a Claude CLI call fails
```

The run pipeline is:

```text
User Input → Intent Classifier / Selected Skill → Repository Scanner → Context Builder
→ Registry-composed Task Graph → Scheduler → Typed Recovery → Quality Gate
→ Conditional Review / Revision → Context Patch → Task Cache → Final Delivery
```

`agent_outputs/<task_id>.md` is the readable task record; the matching `.raw.txt` and `.sip.yaml` files preserve raw and normalized evidence. `reviews/` preserves every review round, while `artifacts/review_summary.json` and `artifacts/task_results.yaml` make outcomes machine-readable. `deliverables/FINAL_DELIVERY.md` is generated for every run, including graph types whose only requested output is a patch.

Durable curated artifacts are also stored in `<project>/.local_engine/artifacts/` under `audit/`, `plan/`, `review/`, `test/`, and `docs/`. For example, an audit run produces `deliverables/AUDIT_REPORT.md` in its run report and `.local_engine/artifacts/audit/AUDIT_REPORT.md` in project-local storage.

Global runtime state lives under `~/.local_engine/` (override with `LOCAL_ENGINE_HOME`). `runs/index.json`, `runs/latest.json`, and `runs/<run_id>.json` form the cross-project run index. Run IDs use UTC `YYYY-MM-DD-NNN` format; legacy `runs/<old_id>/run_metadata.yaml` records are imported on read.

Project-local `.local_engine/cache/` contains `repo_hash.json`, `task_cache.json`, and `verified_tasks.json`. Exact repository matches reuse verified tasks. When the repository changes, a task is reused only if its Skill declares watched paths, those paths are unchanged, and every direct dependency was also reused.

## Safety boundaries

- Plan mode never applies generated patches to project source.
- Apply mode requires explicit approval.
- Workers run with the target project as their current directory.
- Patches outside the project root, `.git/` changes, file deletions, `rm -rf`, `git push`, and destructive SQL are rejected.
- The engine writes only its project-local `.local_engine/` directory and its global `~/.local_engine/` directory.

## SIP: wide input, strict internal output

Claude output is never treated as trustworthy. The parser accepts direct YAML/JSON, fenced blocks, and embedded objects; malformed output enters bounded FORMAT recovery and useful remaining text is preserved as `unstructured`. SIP adds `warnings` and optional `failure_type` fields so quality and recovery decisions remain machine-readable.
