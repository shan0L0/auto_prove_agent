import Mathlib

set_option autoImplicit false

theorem inf_prime_inf_matrix_det_generators_eq_zero :
    sInf
      {c : ℝ |
        ∃ p : ℕ,
          Nat.Prime p ∧
            c =
              sInf
                {x : ℝ |
                  ∃ n : ℕ,
                    0 < n ∧
                      x =
                        ((Nat.card
                            {A : Matrix (Fin n) (Fin n) (ZMod p) //
                              IsPrimitiveRoot (Matrix.det A) (p - 1)} : ℕ) : ℝ) /
                          (p : ℝ) ^ (n ^ 2)}} =
      0 := by
  sorry
