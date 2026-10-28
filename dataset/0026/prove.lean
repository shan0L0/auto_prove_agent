import Mathlib

set_option autoImplicit false

theorem no_degree4_A4_normalClosure_for_seventh_or_eighth_powers
    (n : ℕ) (hn : n = 7 ∨ n = 8) :
    ¬ ∃ x y : AlgebraicClosure ℚ,
      x * y ≠ 0 ∧
      x ^ n + y ^ n = 1 ∧
      Module.finrank ℚ (IntermediateField.adjoin ℚ ({x, y} : Set (AlgebraicClosure ℚ))) = 4 ∧
      Nonempty
        (((IntermediateField.normalClosure ℚ
            (↥(IntermediateField.adjoin ℚ ({x, y} : Set (AlgebraicClosure ℚ))))
            (AlgebraicClosure ℚ)) ≃ₐ[ℚ]
          (IntermediateField.normalClosure ℚ
            (↥(IntermediateField.adjoin ℚ ({x, y} : Set (AlgebraicClosure ℚ))))
            (AlgebraicClosure ℚ))) ≃*
          ↥(alternatingGroup (Fin 4))) := by
  sorry
