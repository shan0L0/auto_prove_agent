# Auto Prove

Lean 4 sorry-driven proof agent MVP.

The agent tries to close each Lean theorem node directly. If it cannot, it asks the LLM for proof designs, chooses a Lean-friendly design, splits the theorem into child lemmas with `sorry`, verifies that the parent follows from those child lemmas, and recursively works on the children. Lean remains the source of truth.

## Requirements

- Python 3.10+
- Lean 4 and Lake via `elan`
- mathlib cache for the workspace

This machine currently needs Lean/Lake installed before real proof checking will run.

## Setup

```bash
cd lean_workspace
lake exe cache get
lake build
```

If Lean is installed through elan but not on `PATH`, set:

```bash
AUTO_PROVE_LEAN_BIN=/Users/yuqiang/.elan/bin/lean
AUTO_PROVE_LAKE_BIN=/Users/yuqiang/.elan/bin/lake
```

For VSCode, open the repository root (`auto-prove`) so the Lean extension sees the root `lakefile.lean`.
If a file still reports `unknown module prefix 'Mathlib'`, run `lake exe cache get` at the repository root and restart the Lean server.

## Usage

```bash
python3 -m agent.main --lean "example : 1 + 1 = 2"
```

Prepare an ArXivLean parquet dataset:

```bash
python3 -m agent.main --prepare-dataset train-00000-of-00001.parquet --dataset-dir dataset
```

Solve one prepared problem directory. The agent copies it to a writable workspace under `runs/workspaces/`, writes proof attempts and split files there, and exports the completed directory to `output/<problem-id>/`:

```bash
python3 -m agent.main --task-dir dataset/0001
```

Use `--profile quick` for a cheaper pass, `--profile deep` for a larger search, or `--profile review` to enable one reviewer-agent retry. Use `--verbose` to print candidate Lean code and diagnostics while debugging, or `--quiet` for script-friendly output.

To let a reviewer agent inspect a failed run and inject task-specific retry guidance without editing the global prompt:

```bash
python3 -m agent.main --task-dir dataset/0001 --profile review
```

The reviewer writes `retry_profile_<n>.json` and `.md` into both the run directory and the exported output directory. The next retry receives that profile as local prompt context plus any extra retrieval queries.

Local Mathlib retrieval is enabled by default. To also query Lean State Search through LeanSearchClient:

```bash
python3 -m agent.main --task-dir dataset/0001 --output-dir output --enable-state-search --state-search-revision v4.22.0
```

Most tuning flags are hidden from the default help. Run this to see every advanced option:

```bash
python3 -m agent.main --advanced-help
```

The default proposer is deterministic and tries a small tactic set (`simp`, `rfl`, `omega`, `aesop`). For natural-language problems, pass already-formalized Lean for now; broad autoformalization is out of scope for the MVP.

## Research Mode

The sorry-driven agent treats hard goals as research programs rather than as grounds for early abandonment. Upstream triage is advisory: even if a legacy model returns an `abandon` strategy, the runner normalizes it into a local research plan and continues proof search.

For construction-heavy goals, the agent records:

- `triage.json`: difficulty, missing objects/theorems, likely Mathlib coverage, and concrete next research steps.
- `research_notebook.md`: accumulated blockers, invalid witnesses, witness sanity checks, promising directions, and failed nodes.
- `sorry_tree.md`: proof tree status plus a short research-status summary when the run is blocked on construction, library facts, formalization, or computation.

Witness sanity checks run before accepting a split when a concrete witness is in scope. For example, matrix orthogonality goals are prompted to test both diagonal and off-diagonal entries, so a false all-ones witness is recorded as an invalid construction instead of being retried as a Lean tactic problem.

## LLM Configuration

Edit `.env` to choose the provider and credentials:

```bash
AUTO_PROVE_LLM_PROVIDER=openai
AUTO_PROVE_LLM_TEMPERATURE=0.2
AUTO_PROVE_LLM_TIMEOUT=60
AUTO_PROVE_LLM_MAX_TOKENS=2000
AUTO_PROVE_OPENAI_BASE_URL=https://api.openai.com/v1
AUTO_PROVE_OPENAI_API_KEY=...
AUTO_PROVE_OPENAI_MODEL=gpt-4.1-mini
```

or:

```bash
AUTO_PROVE_LLM_PROVIDER=anthropic
AUTO_PROVE_LLM_TEMPERATURE=0.2
AUTO_PROVE_LLM_TIMEOUT=60
AUTO_PROVE_LLM_MAX_TOKENS=2000
AUTO_PROVE_ANTHROPIC_BASE_URL=https://api.anthropic.com
AUTO_PROVE_ANTHROPIC_API_KEY=...
AUTO_PROVE_ANTHROPIC_MODEL=claude-3-5-sonnet-latest
```

OpenAI-compatible calls use `langchain-openai`; Anthropic-compatible calls use `langchain-anthropic`. The default `heuristic` provider still works offline.

## LangSmith Tracing

Install dependencies from `requirements.txt` or `pyproject.toml`; the project pins LangChain packages and uses the latest compatible LangSmith SDK range.

Enable tracing in `.env`:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=auto-prove-agent
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com
# LANGSMITH_WORKSPACE_ID=...
```

When enabled, each structured LLM phase is traced with a run name such as `auto-prove:proof candidates`, tags for the provider and phase, and metadata for the model. The CLI flushes LangSmith tracers before exiting so short proof-search runs still appear in the project.

Runs are written under `runs/<timestamp>/` with `trace.jsonl` and `llm_trace.md`. Workspaces keep `sorry_tree.md`, `research_notebook.md`, per-node attempts, proof designs, rejected split diagnostics, and `proof_parts/<node>/` files for accepted splits.

## Project Layout

- `agent/models.py`: `ProofState`, `Candidate`, `LeanResult`
- `agent/cli_config.py`: CLI presets and advanced argument handling
- `agent/lean_runner.py`: temp-file Lean execution with timeout
- `agent/goal_extractor.py`: permissive Lean goal parsing
- `agent/nodes.py`: formalizer/analyzer/retriever/proposer/repairer/router nodes
- `agent/controller.py`: direct-close proof candidate checker used by sorry-driven nodes
- `agent/llm.py`: LangChain client adapters and structured-output handling
- `agent/llm_types.py`: Pydantic schemas for structured LLM responses
- `agent/llm_prompts.py`: prompt builders for direct proof, split, design, and retry review
- `agent/sorry_driven/`: recursive sorry-driven proof development package
- `agent/retry_review.py`: failed-run reviewer and task-local retry profile injection
- `agent/prompts/sorry_driven.md`: editable prompt contract for proof design and splitting
- `agent/retrieval.py`: local Mathlib source search and optional Lean State Search
- `agent/main.py`: CLI
- `lean_workspace/`: Lean 4 + mathlib workspace
- `tests/`: unit tests for offline behavior
