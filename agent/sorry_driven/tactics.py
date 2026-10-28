from __future__ import annotations

from .types import SorryNode


def _complete_with_tactic(statement: str, tactic: str) -> str:
    prefix = statement.split(":= by", 1)[0].rstrip() if ":= by" in statement else statement.rstrip()
    return prefix + f" := by\n  {tactic}\n"


def _header() -> str:
    return "import Mathlib\n\nset_option maxHeartbeats 200000\n\n"


def _tactics_for_node(node: SorryNode) -> list[str]:
    """Return an ordered tactic list tailored to the node's theorem shape."""
    from ..nodes import analyze_theorem_shape

    analysis = analyze_theorem_shape(node.statement)
    shape = analysis.target_shape
    hints = set(analysis.domain_hints)

    # Existential goals require a witness to be constructed; no automation tactic
    # can produce one for a non-trivial existential, so skip the entire direct-close
    # phase and let the LLM proposer take over immediately. This avoids burning
    # ~1 minute per tactic on hard problems with `Matrix`, `Fin n`, etc.
    if shape in {"existential", "existential_conjunction"}:
        return []

    base = ["exact?", "simp", "simp only []", "aesop"]
    shape_tactics: dict[str, list[str]] = {
        "equality": ["rfl", "ring", "linarith", "norm_num", "norm_cast", "field_simp"],
        "sum_equality": ["ring", "simp [Finset.sum_congr]", "linarith", "norm_num"],
        "inequality": ["linarith", "norm_num", "positivity", "gcongr"],
        "conjunction": ["constructor <;> simp", "tauto"],
        "implication": ["tauto", "intro; simp", "intro; exact?"],
        "universal": ["intro; simp", "intro; exact?", "intro; norm_num"],
        "existential": [],
        "existential_conjunction": [],
        "unknown": ["norm_num", "omega", "tauto"],
    }

    extra: list[str] = shape_tactics.get(shape, [])
    if "arithmetic" in hints or "nat" in hints or "int" in hints:
        extra = _unique_extend(extra, ["omega", "norm_num", "linarith"])
    if "Finset_sum" in hints:
        extra = _unique_extend(extra, ["simp [Finset.sum_congr]", "Finset.sum_congr rfl"])
    if "real" in hints or "real_analysis" in hints:
        extra = _unique_extend(extra, ["norm_num", "field_simp", "linarith", "positivity"])
    if "linear_algebra" in hints:
        extra = _unique_extend(extra, ["simp [Module.finrank_add_finrank_orthogonal]"])

    all_tactics = _unique_extend(base, extra)
    if any(kw in node.statement for kw in ["ℝ", "ℕ", "∀", "∃", "Fin", "Finset"]):
        all_tactics = [t for t in all_tactics if "decide" not in t]
    else:
        all_tactics.append("decide")

    return all_tactics


def _unique_extend(base: list[str], extra: list[str]) -> list[str]:
    seen = set(base)
    result = list(base)
    for item in extra:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
