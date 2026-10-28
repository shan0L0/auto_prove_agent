import Mathlib

set_option autoImplicit false

theorem odd_large_as_prime_square_add_fourteen_prime_fifths :
  ∃ N : ℤ, ∀ n : ℤ, Odd n → n ≥ N →
    ∃ p0 : ℕ, Nat.Prime p0 ∧
      ∃ p : Fin 14 → ℕ, (∀ i, Nat.Prime (p i)) ∧
        n = (p0 : ℤ)^2 + ∑ i : Fin 14, ((p i : ℤ)^5) := by
  sorry
