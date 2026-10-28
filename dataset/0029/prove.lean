import Mathlib

set_option autoImplicit false

theorem arctan_sqrt_integral :
    (∫ x in (0 : ℝ)..1,
      Real.arctan (Real.sqrt ((2 + x ^ 2) / (4 + x ^ 2))) /
        ((1 + x ^ 2) * Real.sqrt (2 + x ^ 2))) = Real.pi ^ 2 / 30 := by
  sorry
