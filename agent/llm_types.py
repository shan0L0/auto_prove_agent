from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ProofCandidateOut(BaseModel):
    code: str = Field(description="Sorry-free Lean proof body beginning with `by`, or a complete Lean declaration.")
    rationale: str = Field(default="", description="Brief reason why this proof candidate might work.")
    score: float = Field(default=0.5, ge=0.0, le=1.0, description="Confidence score from 0 to 1.")


class ProofCandidatesOut(BaseModel):
    candidates: List[ProofCandidateOut] = Field(
        description="Sorry-free Lean proof candidates to be checked by Lean. Use an empty list if no complete proof is known."
    )


class ProofDesignOut(BaseModel):
    name: str = Field(description="Short Lean-friendly name for this proof design.")
    construction: str = Field(description="The mathematical construction or proof strategy.")
    child_obligations: List[str] = Field(description="Lean-level child obligations this design will need.")
    why_lean_friendly: str = Field(description="Why this design should be easier to formalize in Lean.")
    risks: List[str] = Field(default_factory=list, description="Known risks or hard parts.")
    score: float = Field(default=0.5, ge=0.0, le=1.0, description="Expected Lean formalization confidence.")


class ProofDesignsOut(BaseModel):
    designs: List[ProofDesignOut] = Field(description="Two or three alternative proof designs, ranked by score.")
    selected_design: str = Field(description="Name of the design that should be used for the next split.")
    rationale: str = Field(description="Why this selected design is preferred.")


class SorrySublemmaOut(BaseModel):
    name: str = Field(description="Stable Lean-friendly lemma name.")
    claim: str = Field(description="Natural-language explanation of the sublemma.")
    lean_statement: str = Field(description="Complete Lean theorem/lemma statement ending with `:= by sorry`.")


class SorrySkeletonOut(BaseModel):
    setup_code: str = Field(
        default="",
        description="Optional helper definitions/notation needed before child lemmas. Do not include imports or set_option commands.",
    )
    parent_proof_code: str = Field(
        default="",
        description="Sorry-free Lean proof body for the current node, beginning with `by`, assuming the child lemmas are available.",
    )
    sublemmas: List[SorrySublemmaOut] = Field(description="The child obligations introduced by the skeleton.")
    rationale: str = Field(default="", description="Brief explanation of why these sublemmas imply the parent.")


class TriageOut(BaseModel):
    """Upstream feasibility assessment for a problem.

    Run once at root before the agent commits compute. The goal is not to veto
    hard problems; it is to identify the research mode the agent should enter,
    such as construction search, library search, small-case exploration, or
    formalization of a missing object.
    """

    difficulty: str = Field(
        description=(
            "Exactly one of: 'trivial' (closeable by `simp`/`decide`), 'standard' "
            "(routine for an undergraduate proof assistant user, all needed lemmas in Mathlib), "
            "'library_search' (needs locating an existing Mathlib theorem), "
            "'construction_required' (needs an explicit witness/object), "
            "'research_level' (requires a non-trivial original construction or theorem unlikely "
            "to be directly available)."
        ),
    )
    required_external_theorems: List[str] = Field(
        default_factory=list,
        description=(
            "Named theorems / constructions the proof would substantively rely on. "
            "Use canonical English names (e.g. 'Paley construction for Hadamard matrices', "
            "'Cauchy-Schwarz inequality')."
        ),
    )
    likely_in_mathlib: List[bool] = Field(
        default_factory=list,
        description=(
            "Parallel to required_external_theorems. True if you are confident the theorem "
            "is in current Mathlib under a recognisable name; False if uncertain or unlikely. "
            "Same length as required_external_theorems."
        ),
    )
    recommended_strategy: str = Field(
        description=(
            "Exactly one of: 'direct_close' (try automation tactics), 'split' (decompose into "
            "child obligations), 'search_library' (retrieve needed Mathlib facts), "
            "'construct_witness' (find and validate an explicit witness), "
            "'explore_small_cases' (study smaller instances first), 'research_plan' "
            "(write down missing objects/lemmas and proceed incrementally). Legacy clients may "
            "still return 'abandon'; callers should treat that as 'research_plan', not a stop signal."
        ),
    )
    recommended_mode: str = Field(
        default="",
        description=(
            "Optional normalized research mode, e.g. direct, split, search_library, "
            "construct_witness, explore_small_cases, literature_needed."
        ),
    )
    research_plan: List[str] = Field(
        default_factory=list,
        description=(
            "Concrete next research steps. Examples: test a proposed witness on off-diagonal "
            "entries, search for a Fourier construction, prove small n analogues, isolate the "
            "missing Mathlib lemma."
        ),
    )
    blocked_on: str = Field(
        default="",
        description=(
            "If the run is likely to get stuck, name the current blocker as one of: "
            "construction, library_theorem, formalization, computation, unknown. Empty for routine goals."
        ),
    )
    rationale: str = Field(
        default="",
        description="One short paragraph explaining the assessment. Cite specific aspects of the theorem.",
    )


class FalsifiedSublemma(BaseModel):
    sublemma_name: str = Field(description="Name of the sublemma identified as falsifiable under the chosen witness.")
    falsifier_lean_code: str = Field(
        description=(
            "Self-contained Lean snippet of the form `example (h : <sublemma proposition>) : False := by ...` "
            "that derives `False` using ONLY the setup definitions and standard Mathlib tactics "
            "(e.g. `simp`, `decide`, `norm_num`). Instantiate quantifiers at concrete small values where needed."
        ),
    )
    rationale: str = Field(default="", description="Brief reason why the witness violates this sublemma.")


class WitnessValidationOut(BaseModel):
    falsified: List[FalsifiedSublemma] = Field(
        default_factory=list,
        description=(
            "Sublemmas the chosen witness PROVABLY fails. Leave empty if every sublemma is plausibly satisfied. "
            "Only list sublemmas you are confident are mathematically false under the witness — "
            "do not include sublemmas that are merely hard to prove."
        ),
    )


class ConjunctCheck(BaseModel):
    """One conjunct of the parent's conclusion, evaluated on the witness."""

    conjunct: str = Field(
        description=(
            "A single conjunct of the parent's conclusion, with the existential variable "
            "substituted by the witness. State it as a self-contained proposition."
        ),
    )
    verdict: str = Field(
        description=(
            "Exactly one of: 'holds' (the conjunct is satisfied by the witness), "
            "'fails' (the conjunct is provably false on the witness), "
            "'uncertain' (you cannot resolve it with reasonable effort)."
        ),
    )
    reasoning: str = Field(
        default="",
        description=(
            "Concrete step-by-step computation justifying the verdict — not just an assertion. "
            "For universally-quantified conjuncts, test at MULTIPLE concrete inputs (both "
            "degenerate/symmetric and non-degenerate) and report what each evaluates to."
        ),
    )


class ParentWitnessFalsificationOut(BaseModel):
    """Direct falsification of a witness against the parent claim's conjuncts.

    Used BEFORE the skeleton typechecks, so that obviously-bad witnesses are
    rejected on the first split attempt instead of after multiple Lean retries.

    The `conjunct_checks` field forces the model to enumerate every conjunct
    independently — without this, models tend to stop after the first
    easy-looking conjunct and miss the one the witness actually fails.
    """

    conjunct_checks: List[ConjunctCheck] = Field(
        description=(
            "EVERY conjunct of the parent's conclusion, evaluated independently. Decompose on `∧` "
            "and substitute the witness for any existential variable. Include trivial-looking "
            "conjuncts too. Do NOT stop after checking one — multiple conjuncts often hold for "
            "the easy parts of a witness while one fails."
        ),
    )
    falsifier_lean_code: str = Field(
        default="",
        description=(
            "If any conjunct's verdict is 'fails', a self-contained Lean snippet of the form "
            "`example (h : <that failing conjunct>) : False := by ...` deriving `False` from `h` "
            "using only the setup definitions in scope and standard Mathlib tactics "
            "(`simp`, `decide`, `norm_num`, `congrFun`, `omega`, etc.). Instantiate quantifiers "
            "at concrete small values. Do NOT use `sorry`. Empty string if no conjunct fails."
        ),
    )
    failing_conjunct: str = Field(
        default="",
        description="If a conjunct fails, restate it here. Empty if none fails.",
    )
    rationale: str = Field(default="", description="Brief overall summary of why the witness fails (if it does).")


class RepairParentOut(BaseModel):
    parent_proof_code: str = Field(description="Fixed sorry-free Lean proof body beginning with `by`.")
    rationale: str = Field(default="", description="Brief explanation of what was changed.")


class ProofDesignReviewOut(BaseModel):
    selected_design: str = Field(
        description="Name of the design that is most mathematically sound and Lean-feasible. "
        "Must exactly match one of the candidate design names.",
    )
    approved_designs: List[str] = Field(
        default_factory=list,
        description="Names of designs whose mathematical approach is sound. May be empty if all have issues.",
    )
    rejection_reasons: dict = Field(
        default_factory=dict,
        description="Map from design name to the reason it was rejected or down-ranked.",
    )
    critique: str = Field(
        default="",
        description="Overall assessment: what the designs get right and wrong, and why the selected one is best.",
    )
    mathematical_warnings: List[str] = Field(
        default_factory=list,
        description="Mathematical issues found across designs (e.g. 'zero matrix fails norm=1').",
    )
    api_concerns: List[str] = Field(
        default_factory=list,
        description="Mathlib identifiers used in the designs that may not exist or have wrong names.",
    )


class RetryConfigOut(BaseModel):
    max_depth: Optional[int] = Field(default=None, ge=0, le=10, description="Optional retry max depth override.")
    max_rounds: Optional[int] = Field(default=None, ge=1, le=10, description="Optional retry max rounds override.")
    max_split_retries: Optional[int] = Field(default=None, ge=1, le=10, description="Optional retry split retry override.")
    beam_width: Optional[int] = Field(default=None, ge=1, le=10, description="Optional retry beam width override.")


class RetryProfileOut(BaseModel):
    should_retry: bool = Field(description="Whether this failed task is worth retrying with local guidance.")
    failure_stage: str = Field(description="The failing stage: direct_close, llm_proof, design, split, parent_assembly, child_proof, retrieval, unknown.")
    failure_reason: str = Field(description="Concise diagnosis grounded in the logs and Lean diagnostics.")
    task_specific_prompt: str = Field(
        default="",
        description="Task-local prompt addendum for the next run. Must be specific and not a global prompt rewrite.",
    )
    preferred_design: str = Field(default="", description="Preferred construction or proof design for retry.")
    forbidden_designs: List[str] = Field(default_factory=list, description="Designs or proof paths to avoid on retry.")
    extra_retrieval_queries: List[str] = Field(default_factory=list, description="Extra Mathlib search queries for retry.")
    retry_config: RetryConfigOut = Field(default_factory=RetryConfigOut, description="Small retry config overrides.")
