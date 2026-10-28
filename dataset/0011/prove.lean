import Mathlib

set_option autoImplicit false

theorem cayley_hamiltonian_dense_large :
    ∃ c : ℝ, 0 < c ∧ ∃ N : ℕ,
      ∀ (G : Type*) [Group G] [Fintype G] [DecidableEq G] (S : Finset G),
        (∀ s ∈ S, s⁻¹ ∈ S) →
        1 ∉ S →
        let Γ : SimpleGraph G := SimpleGraph.fromRel (fun a b => ∃ s ∈ S, b = a * s)
        Fintype.card G ≥ N →
        (S.card : ℝ) ≥ Real.rpow (Fintype.card G : ℝ) (1 - c) →
        Γ.Connected →
        Γ.IsHamiltonian := by
  sorry
