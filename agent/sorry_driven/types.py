from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..models import RejectedDesign


@dataclass(frozen=True)
class SorryDrivenConfig:
    beam_width: int = 3
    max_rounds: int = 3
    timeout_seconds: Optional[float] = 300.0
    # Parallel sorry nodes. Keep this modest: each node may call Lean and an LLM.
    max_workers: int = 3
    # Parallel direct Lean tactic probes within one node. This is intentionally
    # separate from max_workers to avoid multiplying node parallelism by tactic
    # parallelism and saturating the machine with Lean subprocesses.
    tactic_workers: int = 2
    max_iterations: int = 50
    max_depth: int = 3
    max_split_retries: int = 3
    max_subgoals: int = 5
    mathlib_search_limit: int = 8
    enable_state_search: bool = False
    state_search_limit: int = 6
    state_search_revision: Optional[str] = None
    task_specific_guidance: str = ""
    extra_retrieval_queries: List[str] = field(default_factory=list)
    # Drop LLM proof candidates whose self-reported score is below this threshold.
    # The LLM frequently emits low-scored candidates whose own rationale says
    # "this is incorrect"; running Lean on them is pure cost. Set to 0.0 to keep
    # all candidates (e.g. via --ignore-candidate-score on the CLI).
    min_candidate_score: float = 0.15
    # Run a single LLM "feasibility triage" call at root before proof search.
    # By default this is advisory: it writes triage.json/log events but does not
    # stop the run, because LLMs can misjudge Mathlib coverage.
    enable_triage: bool = True
    # Deprecated compatibility flag. Triage is now research-advisory only: even
    # if a legacy model returns "abandon", the agent records a research plan and
    # continues. This field is kept so old CLI/tests/configs still construct.
    triage_abandons: bool = False
    # When triage recommends one of these strategies for the root node, skip the
    # expensive _try_llm_proof step and go straight to _split_node. Direct-close
    # tactics (simp/omega/decide) are still tried first — they are cheap.
    # Set to an empty frozenset to disable and always try direct LLM proof.
    skip_direct_proof_strategies: frozenset = field(
        default_factory=lambda: frozenset({"construct_witness", "explore_small_cases", "research_plan"})
    )
    # After generating N proof designs, run an independent critic LLM call that
    # checks mathematical soundness and Mathlib API existence before code generation.
    # Adds one LLM call per node but catches bad strategies early.
    enable_design_review: bool = True


@dataclass
class SorryNode:
    id: str
    statement: str
    status: str = "sorry"
    parent_id: Optional[str] = None
    depth: int = 0
    claim: str = ""
    proof_code: Optional[str] = None
    skeleton_code: Optional[str] = None
    proof_design: str = ""
    diagnostics: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    # Accumulated setup_code from all ancestor splits. When a parent node is
    # split, its setup_code is stored here so children can reference it.
    preamble: str = field(default="")
    # Goal state at this node's sorry position, as returned by Lean LSP.
    initial_goal: str = field(default="")
    # Witnesses/skeletons that have already been rejected for this node.
    # Each retry of `_split_node` appends to this list and the entries are fed
    # into the next split prompt so the LLM does not propose close variants.
    rejected_designs: List[RejectedDesign] = field(default_factory=list)


@dataclass
class SorryDrivenResult:
    status: str
    workspace_dir: Path
    nodes: List[SorryNode]
    diagnostics: List[str] = field(default_factory=list)
    research_memory: dict = field(default_factory=dict)
