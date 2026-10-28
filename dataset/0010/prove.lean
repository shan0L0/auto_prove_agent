import Mathlib

set_option autoImplicit false

theorem fibonacci_convolution_identity
    (n k s : ℕ) (hn : 1 ≤ n) (hk : 1 ≤ k) (hs : 1 ≤ s) :
    let Φ : ℕ → ℚ := fun m => (Nat.fib (m * k) : ℚ) / (Nat.fib k : ℚ)
    let Ψ : ℕ → ℕ → ℚ :=
      fun n t =>
        Nat.rec
          (motive := fun _ => ℕ → ℚ)
          (fun m => Finset.sum (Finset.range m) (fun j => Φ (j + 1) * Φ (m - j)))
          (fun _ prev m => Finset.sum (Finset.range m) (fun j => Φ (j + 1) * prev (m - j)))
          t n
    Ψ n (s - 1) =
      (((5 : ℚ) ^ s) * ((Nat.fib k : ℚ) ^ (2 * s + 1)))⁻¹ *
        Finset.sum (Finset.range (s + 1)) (fun j =>
          ((-1 : ℚ) ^ ((k - 1) * j)) *
            (Nat.choose (n + 2 * s) j : ℚ) *
              (Nat.choose (n + s - 1 - j) (n - 1) : ℚ) *
                (Nat.fib (k * (n + 2 * s - 2 * j)) : ℚ)) := by
  sorry
