import Mathlib

set_option autoImplicit false

theorem maximal_series_ss_quasinormal_implies_solvable
    (G : Type*) [Group G] [Finite G]
    (n : ℕ) (S : Fin (n + 1) → Subgroup G)
    (h_top : S 0 = ⊤)
    (h_bot : S ⟨n, Nat.lt_succ_self n⟩ = ⊥)
    (h_max :
      ∀ i : Fin n,
        S i.succ < S i.castSucc ∧
          ∀ K : Subgroup G,
            S i.succ ≤ K → K ≤ S i.castSucc → K = S i.succ ∨ K = S i.castSucc)
    (h_ss :
      ∀ i : Fin (n + 1),
        ∃ B : Subgroup G,
          (∀ g : G, ∃ h ∈ S i, ∃ b ∈ B, h * b = g) ∧
            ∀ p : ℕ, ∀ P : Sylow p B, ∀ x : G,
              (∃ h ∈ S i, ∃ q ∈ Subgroup.map B.subtype (P : Subgroup B), h * q = x) ↔
              (∃ q ∈ Subgroup.map B.subtype (P : Subgroup B), ∃ h ∈ S i, q * h = x)) :
    IsSolvable G := by
  sorry
