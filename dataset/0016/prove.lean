import Mathlib

set_option autoImplicit false

theorem exists_int_between_consecutive_squares_with_at_most_three_prime_factors :
    ∀ n : ℤ, 1 ≤ n →
      ∃ k : ℤ, n ^ 2 < k ∧ k < (n + 1) ^ 2 ∧ k.natAbs.factorization.sum (fun _ e => e) ≤ 3 := by
  sorry
