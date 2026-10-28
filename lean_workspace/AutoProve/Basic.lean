import Mathlib

set_option maxHeartbeats 0

-- noncomputable def P (m : ℕ) (π : Fin m → ℝ) (i j : Fin m) : ℝ :=
--   if j = i then 1 - min (π i) (π ⟨(i.1 + 1) % m, Nat.mod_lt (i.1 + 1) (by omega)⟩)
--              - min (π i) (π ⟨(i.1 + (m - 1)) % m, Nat.mod_lt (i.1 + (m - 1)) (by omega)⟩)
--   else if j.1 = (i.1 + 1) % m then min (π i) (π ⟨(i.1 + 1) % m, Nat.mod_lt (i.1 + 1) (by omega)⟩)
--   else if i.1 = (j.1 + 1) % m then min (π i) (π ⟨(i.1 + (m - 1)) % m, Nat.mod_lt (i.1 + (m - 1)) (by omega)⟩)
--   else 0

noncomputable section

def next {m : ℕ} (hm0 : 0 < m) (i : Fin m) : Fin m :=
  ⟨(i.1 + 1) % m, Nat.mod_lt (i.1 + 1) hm0⟩

def prev {m : ℕ} (hm0 : 0 < m) (i : Fin m) : Fin m :=
  ⟨(i.1 + (m - 1)) % m, Nat.mod_lt (i.1 + (m - 1)) hm0⟩


lemma P_nonneg (m : ℕ) (hm : 3 ≤ m) (π : Fin m → ℝ) (hπ_pos : ∀ i, 0 < π i) (i j : Fin m) : 0 ≤ P m π i j := by sorry

lemma P_row_sum (m : ℕ) (hm : 3 ≤ m) (π : Fin m → ℝ) (hπ_pos : ∀ i, 0 < π i) (hπ_sum : ∑ i, π i = 1) (i : Fin m) : ∑ j, P m π i j = 1 := by sorry

lemma P_support (m : ℕ) (hm : 3 ≤ m) (π : Fin m → ℝ) (i j : Fin m) : P m π i j ≠ 0 → j = i ∨ j.1 = (i.1 + 1) % m ∨ i.1 = (j.1 + 1) % m := by sorry

lemma P_detailed_balance (m : ℕ) (hm : 3 ≤ m) (π : Fin m → ℝ) (i j : Fin m) : π i * P m π i j = π j * P m π j i := by sorry

lemma P_stationary (m : ℕ) (hm : 3 ≤ m) (π : Fin m → ℝ) (hπ_pos : ∀ i, 0 < π i) (hπ_sum : ∑ i, π i = 1) (j : Fin m) : ∑ i, π i * P m π i j = π j := by sorry

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
  exact ⟨P m π, P_nonneg m hm π hπ_pos, P_row_sum m hm π hπ_pos hπ_sum,
    P_support m hm π, P_detailed_balance m hm π, P_stationary m hm π hπ_pos hπ_sum⟩
