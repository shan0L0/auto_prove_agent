import Mathlib

set_option autoImplicit false

theorem exists_nat_repr_as_sum_of_five_squares_of_primes_of_ps_floor
    (γ₁ γ₂ γ₃ γ₄ γ₅ : ℝ)
    (hγ₁ : (28 : ℝ) / 29 < γ₁ ∧ γ₁ < 1)
    (hγ₂ : (28 : ℝ) / 29 < γ₂ ∧ γ₂ < 1)
    (hγ₃ : (28 : ℝ) / 29 < γ₃ ∧ γ₃ < 1)
    (hγ₄ : (28 : ℝ) / 29 < γ₄ ∧ γ₄ < 1)
    (hγ₅ : (28 : ℝ) / 29 < γ₅ ∧ γ₅ < 1) :
    ∃ N : ℕ, ∀ n : ℤ, (N : ℤ) ≤ n → Int.ModEq 24 n 5 →
      ∃ p₁ p₂ p₃ p₄ p₅ m₁ m₂ m₃ m₄ m₅ : ℕ,
        Nat.Prime p₁ ∧
        Nat.Prime p₂ ∧
        Nat.Prime p₃ ∧
        Nat.Prime p₄ ∧
        Nat.Prime p₅ ∧
        0 < m₁ ∧
        0 < m₂ ∧
        0 < m₃ ∧
        0 < m₄ ∧
        0 < m₅ ∧
        (p₁ : ℤ) = ⌊Real.rpow (m₁ : ℝ) (γ₁⁻¹)⌋ ∧
        (p₂ : ℤ) = ⌊Real.rpow (m₂ : ℝ) (γ₂⁻¹)⌋ ∧
        (p₃ : ℤ) = ⌊Real.rpow (m₃ : ℝ) (γ₃⁻¹)⌋ ∧
        (p₄ : ℤ) = ⌊Real.rpow (m₄ : ℝ) (γ₄⁻¹)⌋ ∧
        (p₅ : ℤ) = ⌊Real.rpow (m₅ : ℝ) (γ₅⁻¹)⌋ ∧
        n = (p₁ : ℤ)^2 + (p₂ : ℤ)^2 + (p₃ : ℤ)^2 + (p₄ : ℤ)^2 + (p₅ : ℤ)^2 := by
  sorry
