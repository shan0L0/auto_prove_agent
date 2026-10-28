import Mathlib

set_option autoImplicit false

theorem exists_reversible_nearest_neighbor_chain_on_cycle
    (m : ℕ) (hm : 3 ≤ m) (π : Fin m → ℝ)
    (hπ_pos : ∀ i, 0 < π i) (hπ_sum : ∑ i, π i = 1) :
    ∃ P : Fin m → Fin m → ℝ,
      (∀ i j, 0 ≤ P i j) ∧
      (∀ i, ∑ j, P i j = 1) ∧
      (∀ i j, P i j ≠ 0 →
        j = i ∨ j.1 = (i.1 + 1) % m ∨ i.1 = (j.1 + 1) % m) ∧
      (∀ i j, π i * P i j = π j * P j i) ∧
      (∀ j, ∑ i, π i * P i j = π j) := by
  sorry
