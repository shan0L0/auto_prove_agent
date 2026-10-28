import Mathlib

set_option autoImplicit false

theorem exists_complex_hadamard_94 :
    ∃ H : Matrix (Fin 94) (Fin 94) ℂ,
      (∀ i j, ‖H i j‖ = 1) ∧
      H * Matrix.conjTranspose H = (94 : ℂ) • (1 : Matrix (Fin 94) (Fin 94) ℂ) := by
  sorry
