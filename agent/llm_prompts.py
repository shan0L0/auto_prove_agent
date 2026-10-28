from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List

from .models import ProofState


PROMPT_DIR = Path(__file__).with_name("prompts")


def messages_for_state(state: ProofState, beam_width: int) -> List[tuple[str, str]]:
    diagnostics = "\n\n".join(state.diagnostics[-3:]) if state.diagnostics else "(none)"
    goals = "\n\n".join(state.goals) if state.goals else "(not available yet)"
    analysis = state.analysis.__dict__ if state.analysis else {}
    tools = ", ".join(state.retrieved_tools) if state.retrieved_tools else "(none)"
    retrieved_context = retrieved_context_text(state)
    last_error = state.error_kinds[-1] if state.error_kinds else "(none)"
    prompt_mode = getattr(state, "prompt_mode", "proof")
    task_guidance = task_specific_guidance_text(state)
    repair_context = repair_context_text(state)
    return [
        ("system", proof_system_prompt(prompt_mode)),
        (
            "human",
            f"Need {beam_width} proof candidates.\n\n"
            f"Lean theorem statement to prove. Preserve this statement exactly:\n```lean\n{state.theorem}\n```\n\n"
            f"Problem analysis:\n```json\n{json.dumps(analysis, ensure_ascii=False)}\n```\n\n"
            f"Relevant Lean tools/tactics to consider:\n```text\n{tools}\n```\n\n"
            f"Retrieved Mathlib / Lean State Search context. You may cite these names and file paths, "
            f"or infer nearby lemmas from the listed source files:\n```text\n{retrieved_context}\n```\n\n"
            f"Task-specific retry guidance from previous failed run:\n```text\n{task_guidance}\n```\n\n"
            f"Current goals:\n```text\n{goals}\n```\n\n"
            f"Last error kind:\n```text\n{last_error}\n```\n\n"
            f"Repair context from recent failed Lean candidates:\n```text\n{repair_context}\n```\n\n"
            f"Recent Lean diagnostics:\n```text\n{diagnostics}\n```\n\n"
            f"{proof_task_instruction(prompt_mode)}\n\n"
            "Respond in this exact shape and with no surrounding text:\n"
            "{\"candidates\":[{\"code\":\"by\\n  trivial\","
            "\"rationale\":\"...\",\"score\":0.9}]}",
        ),
    ]


def proof_design_messages_for_state(state: ProofState, max_designs: int) -> List[tuple[str, str]]:
    goals = "\n\n".join(state.goals) if state.goals else "(not available yet)"
    diagnostics = "\n\n".join(state.diagnostics[-5:]) if state.diagnostics else "(none)"
    analysis = state.analysis.__dict__ if state.analysis else {}
    retrieved_context = retrieved_context_text(state)
    task_guidance = task_specific_guidance_text(state)
    return [
        (
            "system",
            sorry_driven_prompt()
            + "\n\n"
            "You are the proof-design stage of a sorry-driven Lean agent. Return structured data only. "
            "Do not write Lean code yet. Compare alternative mathematical constructions and choose the "
            "one whose child obligations are smallest and most likely to type-check in Lean.",
        ),
        (
            "human",
            f"Propose up to {max_designs} proof designs for this theorem, then select one design for the split stage.\n\n"
            "Design criteria:\n"
            "- Prefer constructions whose parent assembly is short and sorry-free.\n"
            "- Prefer child obligations that are local, reusable, and individually testable.\n"
            "- For existence goals, first identify the mathematical object/witness, then validate it on hard cases before formalizing it.\n"
            "- If a construction is falsified or listed in research memory, do not retry it or close variants.\n"
            "- If recent failed direct candidates contain a plausible construction, keep it only after a quick mathematical sanity check; if the witness fails any required branch, reject it and choose a new construction.\n"
            "- Do not choose a design whose only child obligation is just the current theorem restated under a new name; isolate a genuinely smaller construction theorem or mark the run blocked on that construction.\n"
            "- Avoid designs that require fragile `Fin` rewriting, large `Finset.sum` rewrites, or hidden analytic facts unless unavoidable.\n"
            "- If the theorem needs a hard external theorem, expose that fact as a named child obligation.\n\n"
            f"Theorem:\n```lean\n{state.theorem}\n```\n\n"
            f"Goals:\n```text\n{goals}\n```\n\n"
            f"Analysis:\n```json\n{json.dumps(analysis, ensure_ascii=False)}\n```\n\n"
            f"Retrieved context:\n```text\n{retrieved_context}\n```\n\n"
            f"Recent diagnostics and failed-candidate summaries:\n```text\n{diagnostics}\n```\n\n"
            f"Task-specific retry guidance from previous failed run:\n```text\n{task_guidance}\n```",
        ),
    ]


def review_proof_designs_messages(
    designs_json: str,
    theorem: str,
    goals: str,
    retrieved_context: str,
    diagnostics: str,
    task_guidance: str,
) -> List[tuple[str, str]]:
    return [
        (
            "system",
            "You are a mathematical critic for a Lean 4 proof agent. "
            "You receive a set of candidate proof strategies and must rank them by feasibility. "
            "Your job is NOT to write Lean code. Your job is to catch strategies that are "
            "mathematically invalid or rely on non-existent Mathlib APIs before the agent wastes "
            "compute generating and checking Lean code. Return structured data only.",
        ),
        (
            "human",
            "Review the following candidate proof designs and select the most feasible one.\n\n"
            "Evaluation criteria (check each design against ALL of these):\n"
            "1. **Mathematical soundness**: Does the proposed witness or construction actually satisfy "
            "the goal? E.g. the zero matrix fails any norm=1 condition; a diagonal matrix H satisfies "
            "H * H^† = I, not n • I. Work through the key conjuncts explicitly.\n"
            "2. **Mathlib API existence**: Are the named lemmas/definitions plausibly in current Mathlib? "
            "Flag anything that looks invented (e.g. `Matrix.kroneckerProduct` — the correct name is "
            "`Matrix.kronecker`; `Matrix.dft` may not exist). When in doubt, flag it.\n"
            "3. **Lean feasibility**: Will the child obligations be individually provable, or do they "
            "just push the hard work into a child sorry with no plan?\n"
            "4. **Name clash risk**: Does the design redefine names that are likely already in the "
            "preamble from ancestor splits?\n\n"
            "Selection rule: pick the design with the fewest fatal flaws. If multiple designs are "
            "sound, prefer the one with smaller, more independent child obligations. "
            "If ALL designs are flawed, still select the least bad one and explain what must change.\n\n"
            f"Theorem:\n```lean\n{theorem}\n```\n\n"
            f"Goals:\n```text\n{goals}\n```\n\n"
            f"Candidate designs:\n```json\n{designs_json}\n```\n\n"
            f"Retrieved Mathlib context (use this to verify API names):\n```text\n{retrieved_context}\n```\n\n"
            f"Recent diagnostics and known blockers:\n```text\n{diagnostics}\n```\n\n"
            f"Task-specific guidance:\n```text\n{task_guidance}\n```",
        ),
    ]


def sorry_split_messages_for_state(state: ProofState, max_subgoals: int) -> List[tuple[str, str]]:
    goals = "\n\n".join(state.goals) if state.goals else "(not available yet)"
    diagnostics = "\n\n".join(state.diagnostics[-5:]) if state.diagnostics else "(none)"
    analysis = state.analysis.__dict__ if state.analysis else {}
    retrieved_context = retrieved_context_text(state)
    proof_design = getattr(state, "proof_design", "")
    task_guidance = task_specific_guidance_text(state)
    rejected_designs_block = rejected_designs_text(state)
    return [
        (
            "system",
            sorry_driven_prompt()
            + "\n\n"
            "You are Step 2 of a sorry-driven Lean proof agent. Your task is not to finish the whole "
            "proof directly. Your task is to split the current theorem into child obligations and prove "
            "that those child obligations are sufficient for the parent. Return structured data only. "
            "Do not invent Mathlib theorem names. Return setup_code, sublemmas, and parent_proof_code. "
            "setup_code may contain helper definitions but no imports/options. Child lemmas may end with "
            "`:= by sorry`. parent_proof_code must be a proof body beginning with `by` and must contain no `sorry`.\n\n"
            "Parent assembly rule: parent_proof_code must reference child lemmas by their EXACT declared "
            "names (the identifier after `lemma`/`def` in each sublemma). Do NOT invent local hypothesis "
            "names like `hHnorm` or `hAorth` — those are not in scope. Use `obtain`/`rcases` to "
            "destructure existential child lemmas: `obtain ⟨H, h1, h2⟩ := child_lemma_name`.\n\n"
            "Witness extraction rule: do NOT write `Classical.choose myLemma` inline in child lemma "
            "bodies or in parent_proof_code. If you need to name the extracted witness across lemmas, "
            "define it once in setup_code: `noncomputable def myWitness := Classical.choose myLemma`, "
            "then reference `myWitness` everywhere. Never apply `Classical.choose` directly to indices.\n\n"
            "Child lemma content rule: every child lemma must assert a non-trivial mathematical property. "
            "A child lemma whose conclusion is `True` or an unconstrained existential `∃ x, True` is "
            "forbidden — it conveys nothing to the parent and will be rejected.",
        ),
        (
            "human",
            f"Split this Lean theorem into 2-{max_subgoals} child obligations.\n\n"
            "Sorry-driven contract:\n"
            "- Child lemmas may use `sorry`; they become independent queue nodes.\n"
            "- The parent declaration must be proved after the child lemmas with no `sorry`.\n"
            "- The parent proof should mainly assemble child lemmas using exact/apply/refine/rewrite/simp.\n"
            "- If a mathematical fact is hard, make it a child lemma instead of leaving a parent proof gap.\n\n"
            "Repair discipline:\n"
            "- If diagnostics mention a plausible rejected direct proof candidate, preserve its construction only after checking that the witness is mathematically compatible with the current goal.\n"
            "- If diagnostics/research memory say a witness fails a concrete branch, reject that witness and choose a new construction.\n"
            "- For matrix orthogonality goals such as `H * H.conjTranspose = n • 1`, always sanity-check both diagonal and off-diagonal entries before accepting a witness.\n"
            "- Split the missing Lean obligations of that construction into child lemmas.\n"
            "- Do not replace a simple construction with a more complicated one merely because the first proof had Lean syntax/indexing errors.\n\n"
            "Existential construction discipline:\n"
            "- Do not create a child lemma that is merely the current theorem with a different name. That is a research blocker, not a useful split.\n"
            "- If you introduce an existential witness, define the concrete object in `setup_code` and have child lemmas prove properties of that exact object.\n"
            "- Do not state witness-specific properties for an arbitrary parameter such as `(H : Matrix ...)` unless the hypotheses uniquely tie `H` to the constructed witness.\n"
            "- For tensor/Kronecker/product constructions, `setup_code` must define the constructed object or an explicit constructor function; child lemmas must apply to that definition, not to an unconstrained arbitrary matrix.\n\n"
            "Requirements:\n"
            "1. Give natural-language claims for the child obligations.\n"
            "2. Give complete Lean statements for every child obligation, each ending with `:= by sorry`.\n"
            "3. Give optional setup_code for helper definitions. Do not include imports or set_option commands.\n"
            "4. Give parent_proof_code: a sorry-free proof body for the current theorem/example, beginning with `by`.\n"
            "   parent_proof_code should use the child lemmas to close the parent.\n"
            "5. The combined skeleton must type-check in Lean when child-lemma sorry is allowed.\n\n"
            f"Selected proof design to implement:\n```text\n{proof_design or '(none; choose the Lean-friendliest split yourself)'}\n```\n\n"
            f"Previously rejected designs/witnesses for THIS node — do NOT propose any of these "
            f"or close variants (e.g. same witness with renamed binders or trivial helper changes):\n"
            f"```text\n{rejected_designs_block}\n```\n\n"
            f"Task-specific retry guidance from previous failed run:\n```text\n{task_guidance}\n```\n\n"
            f"Theorem:\n```lean\n{state.theorem}\n```\n\n"
            f"Goals:\n```text\n{goals}\n```\n\n"
            f"Analysis:\n```json\n{json.dumps(analysis, ensure_ascii=False)}\n```\n\n"
            f"Recent diagnostics and failed-candidate summaries:\n```text\n{diagnostics}\n```\n\n"
            f"Retrieved context:\n```text\n{retrieved_context}\n```",
        ),
    ]


def triage_messages(theorem: str, retrieved_context: str = "") -> List[tuple[str, str]]:
    return [
        (
            "system",
            "You are the upstream research-triage stage of a Lean proof agent that simulates "
            "a persistent researcher. Your output must NEVER recommend giving up. Instead, "
            "classify the theorem and produce a useful research plan. Return structured data only.",
        ),
        (
            "human",
            "Assess the following Lean theorem before the agent commits compute. Decide:\n"
            "  (1) difficulty: trivial / standard / library_search / construction_required / research_level\n"
            "  (2) which named external theorems / constructions a proof would actually rely on\n"
            "  (3) for each, whether it is plausibly in current Mathlib\n"
            "  (4) recommended_strategy: direct_close / split / search_library / construct_witness / explore_small_cases / research_plan\n"
            "  (5) recommended_mode, blocked_on, and research_plan steps when the theorem is hard\n\n"
            "Calibration:\n"
            "- 'trivial' = closeable by `simp`, `decide`, `omega`, `norm_num`, `aesop`, or `exact?` "
            "with no design work.\n"
            "- 'standard' = the proof needs structured tactics or a few well-known Mathlib lemmas, "
            "all of which you can name and which exist in Mathlib.\n"
            "- 'library_search' = the proof likely exists via Mathlib facts, but names/import paths must be found.\n"
            "- 'construction_required' = an explicit witness/object is needed before Lean proof search is useful.\n"
            "- 'research_level' = the proof would require constructing a specific non-trivial "
            "object whose existence is itself a research result (e.g. a particular Hadamard "
            "matrix of fixed order, an explicit example of a hard combinatorial design, an "
            "ad-hoc analytic identity), or invoking a deep theorem that is not in Mathlib in "
            "usable form.\n\n"
            "Researcher rule: do NOT return `abandon`. For research-level goals, return "
            "`research_plan`, `construct_witness`, `explore_small_cases`, or `search_library`. "
            "Name the blocker in `blocked_on` and give concrete next experiments. For existence "
            "goals, separate object discovery, mathematical sanity checks, and Lean formalization.\n\n"
            f"Theorem:\n```lean\n{theorem.strip()}\n```\n\n"
            f"Retrieved Mathlib snippets (helps you assess what's available):\n```text\n"
            f"{retrieved_context.strip() or '(none)'}\n```",
        ),
    ]


def parent_witness_validation_messages(
    parent_statement: str,
    preamble: str,
    setup_code: str,
) -> List[tuple[str, str]]:
    setup_block = setup_code.strip() or "(none in this skeleton; only the preamble is in scope)"
    preamble_block = preamble.strip() or "(empty)"
    return [
        (
            "system",
            "You are the early witness-validation stage of a sorry-driven Lean proof agent. "
            "This is the agent's witness sanity check. Your job is to flag a chosen witness that "
            "PROVABLY fails the parent claim's conclusion, "
            "BEFORE the skeleton's parent-assembly is type-checked. Be conservative: only return a "
            "falsifier if you can construct a concrete Lean derivation of `False` from one conjunct "
            "of the parent's conclusion under the witness. Do NOT flag witnesses that are merely "
            "hard to verify. Return structured data only.",
        ),
        (
            "human",
            "A sorry-driven split has just proposed a witness/setup. Decide whether the witness "
            "actually satisfies the parent claim's conclusion BEFORE we attempt to type-check any "
            "sublemmas.\n\n"
            "Procedure (FOLLOW EVERY STEP — do NOT skip):\n\n"
            "Step 1 — Decompose. Read the parent's conclusion. If it is existential "
            "(`∃ x, P x ∧ Q x ∧ ...`), substitute the witness for the existential variable. Then "
            "split the result on `∧` to obtain a list of conjuncts. Number them 1, 2, 3, ...\n\n"
            "Step 2 — Evaluate EVERY conjunct independently. Return one entry in `conjunct_checks` "
            "for EACH conjunct from Step 1, even ones that look obviously true. For each entry give:\n"
            "  - `conjunct`: the conjunct as evaluated on the witness;\n"
            "  - `verdict`: 'holds', 'fails', or 'uncertain';\n"
            "  - `reasoning`: actual computation, not just an assertion.\n\n"
            "Step 3 — When evaluating a universally-quantified conjunct (`∀ y, R(y)`) or an equality "
            "of compound objects (functions, matrices, sequences), do NOT only test one sample. Pick "
            "AT LEAST TWO distinct inputs that exercise different branches of the witness's definition "
            "— for example: (a) inputs that hit the 'easy' / symmetric / equal-index case, AND (b) inputs "
            "that hit the 'hard' / asymmetric / unequal-index case. Compute the LHS and the RHS at each "
            "and compare. A witness can satisfy a conjunct on the easy case while failing it on the hard one.\n\n"
            "Step 4 — Falsifier. If any conjunct in Step 2 has verdict 'fails', produce "
            "`falsifier_lean_code` of the form\n"
            "  `example (h : <that failing conjunct as a Lean proposition>) : False := by ...`\n"
            "deriving `False` from `h` using only the setup definitions in scope (preamble + "
            "setup_code) and standard Mathlib tactics (`simp [<witness names>, ...]`, `decide`, "
            "`norm_num`, `congrFun`, `omega`, `exact absurd ... (by ...)`, etc.). Instantiate "
            "quantifiers at the concrete failing values found in Step 3. Do NOT use `sorry`. "
            "If no conjunct fails, leave `falsifier_lean_code` empty.\n\n"
            "Critical reminders:\n"
            "- Do NOT stop after one conjunct. Multiple conjuncts is the common case; the witness "
            "  often satisfies the easy ones and fails one of the harder ones.\n"
            "- 'holds' must mean you actually computed it on multiple inputs and got equality, not "
            "  just 'this looks plausible'.\n"
            "- For matrix equations involving `(1 : Matrix ...)`, remember that this is the identity "
            "  matrix, not the all-ones matrix; off-diagonal entries usually matter.\n"
            "- The falsifier will be Lean-checked. A wrong falsifier is discarded silently and the "
            "  witness is treated as valid; therefore prefer correctness over coverage.\n\n"
            f"Parent claim being split:\n```lean\n{parent_statement.strip()}\n```\n\n"
            f"Preamble in scope (definitions inherited from ancestor splits):\n```lean\n{preamble_block}\n```\n\n"
            f"Setup code introduced by THIS split (the new witness lives here):\n```lean\n{setup_block}\n```",
        ),
    ]


def witness_validation_messages(
    parent_statement: str,
    preamble: str,
    setup_code: str,
    sublemma_blocks: List[str],
) -> List[tuple[str, str]]:
    setup_block = setup_code.strip() or "(none in this skeleton; only the preamble is in scope)"
    preamble_block = preamble.strip() or "(empty)"
    sublemmas_text = "\n\n".join(sublemma_blocks) if sublemma_blocks else "(none)"
    return [
        (
            "system",
            "You are the witness-validation stage of a sorry-driven Lean proof agent. "
            "Your job is to flag sublemmas whose claims are mathematically FALSE under the chosen witness. "
            "Be conservative: only report a sublemma as falsified if you can construct a concrete Lean "
            "derivation of `False` from it using the setup definitions. Do not flag sublemmas that are "
            "merely hard or that you cannot disprove. Return structured data only.",
        ),
        (
            "human",
            "Below is a candidate sorry-driven split. Decide whether the chosen witness/setup actually "
            "satisfies every sublemma. For each sublemma you can disprove, produce a self-contained Lean "
            "`example (h : <sublemma proposition>) : False := by ...` that derives False using only the "
            "setup definitions in scope (preamble + setup_code). Use concrete small `Fin` indices or "
            "specific element values to instantiate quantifiers. Use `simp [<witness names>]`, `decide`, "
            "`norm_num`, `omega`, etc. Do not use `sorry`. The Lean snippet must be sound — it will be "
            "verified by Lean.\n\n"
            "If every sublemma is plausibly satisfied (or if you cannot construct a Lean disproof), "
            "return an empty `falsified` list.\n\n"
            f"Parent claim being split:\n```lean\n{parent_statement.strip()}\n```\n\n"
            f"Preamble in scope (definitions inherited from ancestor splits):\n```lean\n{preamble_block}\n```\n\n"
            f"Setup code introduced by THIS split:\n```lean\n{setup_block}\n```\n\n"
            f"Sublemmas to validate:\n{sublemmas_text}",
        ),
    ]


def retry_review_messages(failure_context: str) -> List[tuple[str, str]]:
    return [
        (
            "system",
            "You are a reviewer agent for a sorry-driven Lean proof system. "
            "Your job is to inspect a failed run and produce a task-local retry profile. "
            "Do not rewrite global prompts. Do not claim a fact unless it is grounded in the supplied logs. "
            "Return structured data only.",
        ),
        (
            "human",
            "Review this failed Lean proof-agent run. Diagnose the failure and decide whether one local retry is useful.\n\n"
            "Rules:\n"
            "- Prefer the smallest task-specific guidance that could change the next run.\n"
            "- If the failure is due to missing major mathematics, do not call it hopeless; identify the next research step, blocker, or external source needed. Set should_retry=false only when another immediate local retry would repeat the same information.\n"
            "- If the model chose a bad construction, name a preferred_design and forbidden_designs.\n"
            "- Suggest extra_retrieval_queries only when they are concrete Mathlib/search strings.\n"
            "- retry_config overrides should be small; leave fields null unless clearly helpful.\n\n"
            f"Failure context:\n```text\n{failure_context[:30000]}\n```",
        ),
    ]


def proof_system_prompt(prompt_mode: str) -> str:
    base = (
        "You are solving a Lean 4 proof-completion task in the style of MathArena ArXivLean. "
        "Lean is the only verifier. Preserve the theorem statement exactly. You may use Mathlib "
        "tactics and local helper reasoning inside the proof, but do not introduce axioms or change "
        "the statement. Never use `sorry`, `admit`, `axiom`, `native_decide`, or nonstandard axioms. "
        "When retrieval context is provided, use only theorem names supported by snippets or file "
        "locations. Do not invent theorem names. Return structured data only."
    )
    if prompt_mode == "sorry_direct":
        return (
            base
            + "\n\n"
            + sorry_driven_prompt()
            + "\n\n"
            + " This is Step 1 of a sorry-driven proof agent: try to close the current node directly. "
            "Return proof candidates only if they are complete sorry-free Lean proofs. If the proof "
            "requires new lemmas or substantial missing facts, return an empty candidates list so the "
            "agent can split the node into child obligations."
        )
    return (
        base
        + " Return proof candidates only. Each candidate must contain code, rationale, and score. "
        "The code should be either the complete Lean declaration with the original statement unchanged, "
        "or just a proof body beginning with `by`. Do not include imports."
    )


def proof_task_instruction(prompt_mode: str) -> str:
    common = (
        "Do not explain the theorem. Do not write analysis before the structured response. "
        "Return only the structured candidates requested by the schema."
    )
    repair_rule = (
        "\nWhen Repair context contains a recent failed candidate, treat it as the primary object "
        "to inspect. First decide whether its witness/construction is mathematically compatible "
        "with every branch of the goal. Preserve and repair it only when the failure appears to be "
        "Lean syntax, naming, equality orientation, missing side conditions, summation indices, or "
        "tactic detail. If the witness itself fails the goal, abandon that construction and return "
        "no candidate or choose a different construction."
    )
    if prompt_mode == "sorry_direct":
        return (
            common
            + "\nThis is a sorry-driven direct-close attempt. Every returned candidate must be a complete "
            "Lean proof with no `sorry`. Do not return partial sketches. Do not return a proof that merely "
            "states missing lemmas. For existential goals, sanity-check the proposed witness on hard "
            "branches before returning Lean code; if the witness fails, return `candidates: []` instead "
            "of a doomed proof attempt. If you cannot close the theorem now, return `candidates: []`."
            + repair_rule
        )
    return (
        common
        + "\nIf you only know partial ideas, do not use `sorry`; either return a complete candidate or "
        "return fewer/no candidates."
        + repair_rule
    )


def rejected_designs_text(state: ProofState) -> str:
    rejected = getattr(state, "rejected_designs", [])
    if not rejected:
        return "(none — this is the first split attempt for this node)"
    blocks = []
    for entry in rejected:
        block = f"[Attempt {entry.attempt}] reason: {entry.failure_reason}"
        if entry.failing_conjunct:
            block += f"\nfailing conjunct: {entry.failing_conjunct}"
        if entry.witness_summary:
            block += f"\nwitness summary:\n```lean\n{entry.witness_summary.strip()}\n```"
        blocks.append(block)
    return "\n\n".join(blocks)


def task_specific_guidance_text(state: ProofState) -> str:
    guidance = state.task_specific_guidance.strip()
    queries = state.extra_retrieval_queries
    if not guidance and not queries:
        return "(none)"
    parts = []
    if guidance:
        parts.append(guidance)
    if queries:
        parts.append("Extra retrieval queries: " + ", ".join(queries))
    return "\n\n".join(parts)


def repair_context_text(state: ProofState, max_attempts: int = 2, max_code_chars: int = 1600, max_diag_chars: int = 1600) -> str:
    if not state.attempts:
        return "(none; this is the first proposal round)"

    attempt_count = len(state.attempts)
    start_number = max(1, attempt_count - max_attempts + 1)
    blocks = [
        "The following Lean candidates were already tried and rejected. "
        "Repair them locally instead of switching proof strategy by default."
    ]
    recent_attempts = state.attempts[-max_attempts:]
    recent_diagnostics = state.diagnostics[-len(recent_attempts):]
    recent_errors = state.error_kinds[-len(recent_attempts):]
    for offset, candidate in enumerate(recent_attempts):
        number = start_number + offset
        diagnostic = recent_diagnostics[offset] if offset < len(recent_diagnostics) else ""
        error_kind = recent_errors[offset] if offset < len(recent_errors) else "unknown"
        blocks.append(
            f"Attempt {number}:\n"
            f"score={candidate.score:.2f}; source={candidate.source}; error_kind={error_kind}\n"
            f"rationale={candidate.rationale or '(none)'}\n"
            "Lean code:\n"
            f"{_truncate(candidate.code, max_code_chars)}\n"
            "Lean diagnostics:\n"
            f"{_truncate(diagnostic or '(none)', max_diag_chars)}"
        )
    return "\n\n".join(blocks)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n... [truncated]"


@lru_cache(maxsize=1)
def sorry_driven_prompt() -> str:
    return (PROMPT_DIR / "sorry_driven.md").read_text(encoding="utf-8").strip()


def repair_parent_assembly_messages(
    theorem: str,
    parent_proof_code: str,
    child_names: List[str],
    setup_code: str,
    goals: str,
) -> List[tuple[str, str]]:
    names_block = "\n".join(f"- `{n}`" for n in sorted(child_names))
    return [
        (
            "system",
            "You are a Lean 4 proof-assembly repair agent. "
            "Fix a parent_proof_code that references undeclared hypothesis names. "
            "Return structured data only. Do not use sorry.",
        ),
        (
            "human",
            f"The parent assembly code references undeclared names. Rewrite it so it calls only "
            f"the declared child lemmas listed below.\n\n"
            f"Declared child lemma names — use ONLY these:\n{names_block}\n\n"
            f"Typical pattern:\n"
            f"  obtain ⟨witness, prop1, prop2⟩ := child_lemma_name\n"
            f"  exact ⟨witness, prop1, prop2⟩\n\n"
            f"Theorem:\n```lean\n{theorem}\n```\n\n"
            f"Setup code in scope:\n```lean\n{setup_code or '(none)'}\n```\n\n"
            f"Current goals:\n```text\n{goals or '(not available)'}\n```\n\n"
            f"Buggy parent_proof_code to fix:\n```lean\n{parent_proof_code}\n```\n\n"
            f"Rules:\n"
            f"- Begin with `by`.\n"
            f"- Do not use sorry, admit, or axiom.\n"
            f"- Do not invent new lemma names beyond the declared list.\n"
            f"- Use `obtain`/`rcases`/`exact`/`refine` to destructure child lemmas.\n"
            f"- If a child lemma proves `∃ x, P x`, destructure it: "
            f"`obtain ⟨x, hx⟩ := child_name` and then use `x` and `hx`.",
        ),
    ]


def retrieved_context_text(state: ProofState, limit: int = 8) -> str:
    if not state.retrieved_context:
        return "(none)"
    blocks = []
    for index, result in enumerate(state.retrieved_context[:limit], start=1):
        location = ""
        if result.file_path:
            location = f"{result.file_path}:{result.line}" if result.line else result.file_path
        header = f"[{index}] source={result.source} query={result.query}"
        if result.name:
            header += f" name={result.name}"
        if location:
            header += f" location={location}"
        body = result.content or result.snippet or result.diagnostic
        blocks.append(header + "\n" + body.strip())
    return "\n\n".join(blocks)
