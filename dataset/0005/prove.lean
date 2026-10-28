import Mathlib

set_option autoImplicit false

theorem restricted_hFold_sumset_eventually_univ
    (h : ℕ) (hh : 4 ≤ h)
    (αh : ℝ)
    (hroot : 0 < αh ∧
      ((3 : ℝ) ^ (h - 2) * αh ^ (h - 1) + αh - 1 = 0) ∧
      ∀ x : ℝ,
        0 < x →
        ((3 : ℝ) ^ (h - 2) * x ^ (h - 1) + x - 1 = 0) →
        x = αh)
    {α : ℝ} (hα : αh < α) :
    ∃ M : ℕ, 0 < M ∧
      ∀ n : ℕ,
        M < n →
        n % 2 = 1 →
        ∀ (G : Type*) [AddCommGroup G] [Fintype G],
          Fintype.card G = n →
          ∀ A : Finset G,
            α * (n : ℝ) ≤ (A.card : ℝ) →
            ({g : G | ∃ s : Finset G, s ⊆ A ∧ s.card = h ∧ s.sum id = g} : Set G) = Set.univ := by
  sorry
