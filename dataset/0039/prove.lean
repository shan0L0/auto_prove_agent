import Mathlib

set_option autoImplicit false

theorem mu_scalar_subspace_eq_iff
    (n : ℕ) (hn : 0 < n)
    (F : Submodule ℂ (Matrix (Fin n) (Fin n) ℂ))
    (hEF : (ℂ ∙ (1 : Matrix (Fin n) (Fin n) ℂ)) ≤ F) :
    let E : Submodule ℂ (Matrix (Fin n) (Fin n) ℂ) := ℂ ∙ (1 : Matrix (Fin n) (Fin n) ℂ)
    let μ := fun (V : Submodule ℂ (Matrix (Fin n) (Fin n) ℂ)) (A : Matrix (Fin n) (Fin n) ℂ) =>
      by
        classical
        exact if h : ∃ X : Matrix (Fin n) (Fin n) ℂ, X ∈ V ∧ Matrix.det (1 - A * X) = 0 then
          (1 : ℝ) / sInf ((fun X : Matrix (Fin n) (Fin n) ℂ =>
            ‖((Matrix.toEuclideanLin ≪≫ₗ LinearMap.toContinuousLinearMap) X)‖) ''
            {X : Matrix (Fin n) (Fin n) ℂ | X ∈ V ∧ Matrix.det (1 - A * X) = 0})
        else 0
    (∀ A : Matrix (Fin n) (Fin n) ℂ, μ E A = μ F A) ↔ E = F := by
  sorry
