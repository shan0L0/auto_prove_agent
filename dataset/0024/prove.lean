import Mathlib

set_option autoImplicit false

open scoped Topology
open Filter

theorem erdos_rogers_hypergraph_asymptotic :
    ∀ ⦃k s : ℕ⦄, 4 ≤ k → k + 7 ≤ s →
      let erdosRogers : ℕ → ℕ :=
        fun N =>
          sSup
            {m : ℕ |
              ∀ H : Set (Finset (Fin N)),
                (∀ e : Finset (Fin N), e ∈ H → e.card = k) →
                (∀ W : Finset (Fin N),
                  W.card = s → ¬ ((↑(W.powersetCard k) : Set (Finset (Fin N))) ⊆ H)) →
                ∃ U : Finset (Fin N),
                  U.card = m ∧
                  ∀ W : Finset (Fin N),
                    W ⊆ U →
                    W.card = k + 1 →
                    ¬ ((↑(W.powersetCard k) : Set (Finset (Fin N))) ⊆ H)}
      ∃ c C : ℝ,
        0 < c ∧ c ≤ C ∧
        (∀ᶠ N : ℕ in Filter.atTop,
          (((Real.log^[k - 2]) (N : ℝ)).rpow c) ≤ (erdosRogers N : ℝ) ∧
          (erdosRogers N : ℝ) ≤ (((Real.log^[k - 2]) (N : ℝ)).rpow C)) := by
  sorry
