from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .llm import LLMClient
from .models import Candidate, ProofAnalysis, ProofState


@dataclass
class Formalizer:
    def run(self, problem: str) -> str:
        stripped = problem.strip()
        if stripped.startswith(("theorem ", "lemma ", "example ")):
            return stripped
        raise ValueError("Natural-language formalization needs an LLM. Pass --lean for this MVP.")


@dataclass
class Analyzer:
    def run(self, theorem: str) -> ProofState:
        header = "import Mathlib\n\nset_option maxHeartbeats 200000\n\n"
        state = ProofState(theorem=theorem, header=header)
        state.analysis = analyze_theorem_shape(theorem)
        return state


@dataclass
class Retriever:
    def run(self, state: ProofState) -> ProofState:
        tools = set(state.retrieved_tools)
        if state.analysis:
            tools.update(state.analysis.likely_methods)
        if "equality" in (state.analysis.target_shape if state.analysis else ""):
            tools.update(["rfl", "simp", "rw"])
        if any(hint in ["nat", "int", "arithmetic"] for hint in (state.analysis.domain_hints if state.analysis else [])):
            tools.update(["omega", "norm_num", "linarith"])
        state.retrieved_tools = sorted(tools)
        return state


@dataclass
class TacticProposer:
    client: LLMClient

    def run(self, state: ProofState, beam_width: int) -> List[Candidate]:
        return self.client.propose(state, beam_width)


@dataclass
class Repairer:
    client: LLMClient

    def run(self, state: ProofState, beam_width: int) -> List[Candidate]:
        return self.client.propose(state, beam_width)


class VerifierRouter:
    @staticmethod
    def is_done(state: ProofState) -> bool:
        return state.verified_code is not None


def analyze_theorem_shape(theorem: str) -> ProofAnalysis:
    """Analyse a Lean theorem statement and return a :class:`ProofAnalysis`.

    Improvements over the original version:
    - Richer domain detection covering Fin, Finset, linear algebra, group
      theory, real analysis, and probability-like structures.
    - Target-shape detection now also recognises nested existential-conjunction
      patterns (common in construction theorems).
    - ``likely_methods`` are tailored per domain × shape so that the LLM
      receives more actionable hints.
    """
    import re as _re

    text = theorem
    # Use only the part after the last `:` as the "conclusion" heuristic,
    # but fall back to the whole text when no colon is present.
    conclusion = text.split(":", 1)[-1] if ":" in text else text

    # -----------------------------------------------------------------------
    # Domain detection
    # -----------------------------------------------------------------------
    domain_hints: list[str] = []

    if "ℕ" in text or "Nat." in text:
        domain_hints.append("nat")
    if "ℤ" in text or "Int." in text:
        domain_hints.append("int")
    if "ℝ" in text or "Real." in text:
        domain_hints.append("real")
    if any(token in text for token in ["≤", "<", "+", "-", "*", "^"]):
        domain_hints.append("arithmetic")

    # Fin / modular index arithmetic
    if _re.search(r'\bFin\b', text):
        domain_hints.append("Fin")
    # Modulo / remainder operator
    if "%" in text or _re.search(r'\bmod\b', text):
        domain_hints.append("modular_arithmetic")
    # Finset summation
    if "∑" in text or "Finset.sum" in text or "Finset." in text:
        domain_hints.append("Finset_sum")
    # Linear algebra
    if any(kw in text for kw in ["Submodule", "finrank", "FiniteDimensional", "Module."]):
        domain_hints.append("linear_algebra")
    # Group / ring algebra
    if any(kw in text for kw in ["AddCommGroup", "CommRing", "Field", "Fintype.card", "Fintype"]):
        domain_hints.append("group_theory")
    # Real analysis / special functions
    if any(kw in text for kw in ["Real.rpow", "rpow", "Real.log", "Real.sqrt", "Filter.atTop"]):
        domain_hints.append("real_analysis")
    # Probability-like / measure-like patterns
    if _re.search(r'π\s*[a-z]|hπ_|∑.*=\s*1\b', text):
        domain_hints.append("probability")

    # -----------------------------------------------------------------------
    # Target shape detection
    # -----------------------------------------------------------------------
    # Detect existential-conjunction pattern common in construction theorems
    # e.g. "∃ P, (∀ i j, ...) ∧ (∀ i, ...) ∧ ..."
    if "∃" in conclusion and "∧" in conclusion:
        target_shape = "existential_conjunction"
        methods = ["use", "refine", "constructor", "exact", "And.intro"]
    elif "∃" in conclusion:
        target_shape = "existential"
        methods = ["use", "refine", "constructor"]
    elif "∀" in conclusion:
        target_shape = "universal"
        methods = ["intro", "intros", "simp", "aesop"]
    elif "∧" in conclusion:
        target_shape = "conjunction"
        methods = ["constructor", "exact", "And.intro"]
    elif "=" in conclusion and "∑" in conclusion:
        target_shape = "sum_equality"
        methods = ["simp", "ring", "Finset.sum_congr", "linarith"]
    elif "=" in conclusion:
        target_shape = "equality"
        methods = ["rfl", "simp", "rw", "ring", "linarith"]
    elif "≤" in conclusion or "<" in conclusion:
        target_shape = "inequality"
        methods = ["linarith", "norm_num", "simp", "gcongr"]
    elif "→" in conclusion:
        target_shape = "implication"
        methods = ["intro", "exact", "simp"]
    else:
        target_shape = "unknown"
        methods = ["simp", "aesop", "tauto"]

    # -----------------------------------------------------------------------
    # Domain-specific method augmentation
    # -----------------------------------------------------------------------
    if "Fin" in domain_hints:
        methods = _dedupe_list(methods + ["Fin.ext_iff", "Fin.val_mk", "Nat.mod_lt"])
    if "Finset_sum" in domain_hints:
        methods = _dedupe_list(methods + ["Finset.sum_congr", "Finset.sum_comm", "Finset.sum_add_distrib"])
    if "linear_algebra" in domain_hints:
        methods = _dedupe_list(methods + ["Module.finrank_add_finrank_orthogonal", "Submodule.finrank_le"])
    if "probability" in domain_hints:
        methods = _dedupe_list(methods + ["Finset.sum_mul", "mul_comm", "Finset.sum_eq_one"])

    return ProofAnalysis(
        target_shape=target_shape,
        domain_hints=domain_hints,
        likely_methods=methods,
        notes="Heuristic structural analysis of the Lean theorem statement (extended).",
    )


def _dedupe_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result

