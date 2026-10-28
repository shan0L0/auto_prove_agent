import Mathlib

set_option autoImplicit false

theorem squarefree_order_graph_connected {G : Type*} [CommGroup G] [Fintype G]
    (hG : Squarefree (Fintype.card G)) :
    (let Γ : SimpleGraph G := {
      Adj := fun x y => x ≠ y ∧ Nat.Prime (orderOf (x * y))
      symm := by
        intro x y h
        rcases h with ⟨hxy, hp⟩
        refine ⟨?_, ?_⟩
        · intro hyx
          exact hxy hyx.symm
        · simpa [mul_comm] using hp
    }
    Γ.Connected) := by
  sorry
