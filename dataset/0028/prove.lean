import Mathlib

set_option autoImplicit false

theorem image_seq_not_contained_in_short_interval
    (ξ : ℝ) (hξ0 : ξ ≠ 0) {p q : ℕ} (hpq : p > q) (hq : 1 ≤ q)
    (hcop : Nat.Coprime p q) (hξ : Irrational ξ ∨ 1 < q) :
    ¬ ∃ a ℓ : ℝ,
      0 ≤ ℓ ∧
      ℓ < ((1 : ℝ) + (q : ℝ) / (p : ℝ) - ((q : ℝ) ^ 2) / ((p : ℝ) ^ 2)) / (p : ℝ) ∧
      Set.range (fun n : ℕ => ((ξ * (-(p : ℝ) / (q : ℝ)) ^ n : ℝ) : AddCircle (1 : ℝ))) ⊆
        {x : AddCircle (1 : ℝ) | ∃ y ∈ Set.Icc a (a + ℓ), ((y : ℝ) : AddCircle (1 : ℝ)) = x} := by
  sorry
