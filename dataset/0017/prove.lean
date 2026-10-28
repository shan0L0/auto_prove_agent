import Mathlib

set_option autoImplicit false

theorem hadwiger_ramsey_limit :
    Filter.Tendsto
      (fun k : ℕ =>
        (((sInf {n : ℕ |
            ∀ C : Fin 2 → SimpleGraph (Fin n),
              (∀ v w : Fin n, v ≠ w → ∃! i : Fin 2, (C i).Adj v w) →
                ∃ i : Fin 2, ∃ B : Fin k → Set (Fin n),
                  (∀ j : Fin k, Nonempty (B j)) ∧
                  (∀ j : Fin k, ((C i).induce (B j)).Connected) ∧
                  (∀ j₁ j₂ : Fin k, j₁ ≠ j₂ → Disjoint (B j₁) (B j₂)) ∧
                  (∀ j₁ j₂ : Fin k, j₁ ≠ j₂ → ∃ x ∈ B j₁, ∃ y ∈ B j₂, (C i).Adj x y)}) :
            ℕ) :
          ℝ) / ((k : ℝ) * Real.sqrt (Real.logb 2 (k : ℝ))))
      Filter.atTop
      (nhds 1) := by
  sorry
