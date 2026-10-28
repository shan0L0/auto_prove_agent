import Mathlib

set_option autoImplicit false

theorem gcd_lcm_matrix_factorization
    (n a b : ℕ) (hn : 0 < n) (ha : 0 < a) (hb : 0 < b) (hab : a ∣ b)
    (x : Fin n → ℕ) (hxpos : ∀ i, 0 < x i) (hxinj : Function.Injective x)
    (hgcd_closed :
      let s : Finset ℕ := Finset.univ.image x
      ∀ i j, Nat.gcd (x i) (x j) ∈ s)
    (hG :
      let s : Finset ℕ := Finset.univ.image x
      let G : ℕ → Finset ℕ := fun m =>
        s.filter (fun d =>
          d < m ∧ d ∣ m ∧ ∀ y ∈ s, d ∣ y → y ∣ m → y = d ∨ y = m)
      ∀ m ∈ s,
        (G m).card ≤ 1 ∨
          2 ≤ (G m).card ∧
            ∀ y₁ ∈ G m, ∀ y₂ ∈ G m, y₁ ≠ y₂ →
              Nat.lcm y₁ y₂ = m ∧ Nat.gcd y₁ y₂ ∈ G y₁ ∧ Nat.gcd y₁ y₂ ∈ G y₂) :
    ∃ C D : Matrix (Fin n) (Fin n) ℤ,
      let A : Matrix (Fin n) (Fin n) ℤ := fun i j => ((Nat.gcd (x i) (x j)) ^ a : ℤ)
      let B : Matrix (Fin n) (Fin n) ℤ := fun i j => ((Nat.gcd (x i) (x j)) ^ b : ℤ)
      let L : Matrix (Fin n) (Fin n) ℤ := fun i j => ((Nat.lcm (x i) (x j)) ^ b : ℤ)
      B = A * C ∧ L = A * D := by
  sorry
