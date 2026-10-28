import Mathlib

set_option autoImplicit false

theorem classical_ramsey_upper_bounds :
    (∀ G₁ G₂ G₃ : SimpleGraph (Fin 229),
      (G₁ ⊔ G₂ ⊔ G₃ : SimpleGraph (Fin 229)) = ⊤ →
      Disjoint G₁ G₂ →
      Disjoint G₁ G₃ →
      Disjoint G₂ G₃ →
      (∃ s : Finset (Fin 229), G₁.IsNClique 4 s) ∨
      (∃ s : Finset (Fin 229), G₂.IsNClique 4 s) ∨
      (∃ s : Finset (Fin 229), G₃.IsNClique 4 s)) ∧
    (∀ G₁ G₂ G₃ : SimpleGraph (Fin 157),
      (G₁ ⊔ G₂ ⊔ G₃ : SimpleGraph (Fin 157)) = ⊤ →
      Disjoint G₁ G₂ →
      Disjoint G₁ G₃ →
      Disjoint G₂ G₃ →
      (∃ s : Finset (Fin 157), G₁.IsNClique 3 s) ∨
      (∃ s : Finset (Fin 157), G₂.IsNClique 4 s) ∨
      (∃ s : Finset (Fin 157), G₃.IsNClique 5 s)) ∧
    (∀ G₁ G₂ G₃ : SimpleGraph (Fin 91),
      (G₁ ⊔ G₂ ⊔ G₃ : SimpleGraph (Fin 91)) = ⊤ →
      Disjoint G₁ G₂ →
      Disjoint G₁ G₃ →
      Disjoint G₂ G₃ →
      (∃ s : Finset (Fin 91), G₁.IsNClique 3 s) ∨
      (∃ s : Finset (Fin 91), G₂.IsNClique 3 s) ∨
      (∃ s : Finset (Fin 91), G₃.IsNClique 6 s)) := by
  sorry
