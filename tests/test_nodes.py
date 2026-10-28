from agent.nodes import Analyzer, Retriever


def test_analyzer_retriever_add_human_style_metadata():
    state = Analyzer().run("example : 1 = 1 := by\nsorry")
    state = Retriever().run(state)

    assert state.analysis is not None
    assert state.analysis.target_shape == "equality"
    assert "rfl" in state.retrieved_tools


def test_analyzer_marks_research_number_theory_fixture_as_high_risk_domain():
    theorem = (
        "theorem demo (γ : ℝ) : ∃ N : ℕ, ∀ n : ℤ, "
        "Int.ModEq 24 n 5 → ∃ p m : ℕ, Nat.Prime p ∧ "
        "(p : ℤ) = ⌊Real.rpow (m : ℝ) (γ⁻¹)⌋ := by\nsorry"
    )
    state = Retriever().run(Analyzer().run(theorem))

    assert state.analysis is not None
    assert state.analysis.target_shape in {"existential", "existential_conjunction"}
    assert "real_analysis" in state.analysis.domain_hints
    assert "Nat.mod_lt" in state.analysis.likely_methods or "omega" in state.retrieved_tools
