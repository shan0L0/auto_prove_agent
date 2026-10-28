import Mathlib

set_option autoImplicit false

theorem primes_no_k_AP_density_decay
    (k : ℕ) (hk : 4 ≤ k) :
    ∃ c : ℝ, 0 < c ∧
      ∀ A : ℕ → Finset ℕ,
        (∀ N n, n ∈ A N → Nat.Prime n ∧ n ≤ N) →
        (∀ N, ¬ ∃ a d : ℕ, 0 < d ∧ ∀ j : Fin k, a + j.1 * d ∈ A N) →
        (fun N : ℕ => (((A N).card : ℝ) / (((Finset.range (N + 1)).filter Nat.Prime).card : ℝ)))
          =O[atTop]
        (fun N : ℕ => Real.exp (- (Real.log (Real.log (Real.log (N : ℝ)))).rpow c)) := by
  sorry
