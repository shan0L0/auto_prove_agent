import Mathlib

set_option autoImplicit false

theorem padicValNat_sum_divisors_pow_le_log2 (k n : ℕ) (hk : 0 < k) (hn : 2 ≤ n) :
    (Odd k → padicValNat 2 (∑ d ∈ Nat.divisors n, d ^ k) ≤ Nat.clog 2 n) ∧
    (Even k → padicValNat 2 (∑ d ∈ Nat.divisors n, d ^ k) ≤ Nat.log2 n) := by
  sorry
