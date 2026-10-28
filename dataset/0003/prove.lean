import Mathlib

set_option autoImplicit false

theorem divisor_family_packing_asymptotic :
    let F_n : ℕ → Finset (Finset ℕ) := fun n =>
      (Finset.Icc 1 n).image (fun d : ℕ => (Finset.Icc 1 (n / d)).image (fun k : ℕ => k * d))
    let mFamily : Finset (Finset ℕ) → ℕ := fun F =>
      sInf {M : ℕ | ∃ t : Finset ℕ → ℤ, ∃ l : ℤ,
        (∀ A ∈ F, ∀ x ∈ A.image (fun a : ℕ => t A + (a : ℤ)), x ∈ Finset.Icc l (l + (M : ℤ) - 1)) ∧
        (∀ A ∈ F, ∀ B ∈ F, A ≠ B →
          Disjoint (A.image (fun a : ℕ => t A + (a : ℤ))) (B.image (fun b : ℕ => t B + (b : ℤ))))}
    ; (fun n : ℕ => (mFamily (F_n n) : ℝ)) =Θ[Filter.atTop]
        (fun n : ℕ => (n : ℝ) ^ (3 / 2 : ℝ) / Real.log n) := by
  sorry
