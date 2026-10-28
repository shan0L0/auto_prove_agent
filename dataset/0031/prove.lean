import Mathlib

set_option autoImplicit false

theorem irrational_subsets_rational_sum_bound
    (n r : ℕ) (hnr : r < n) (hr1 : 1 < r) (S : Finset ℝ)
    (hcard : S.card = n) (hirr : ∀ x, x ∈ S → Irrational x) :
    (by
      classical
      let m := n / r
      exact ((S.powersetCard r).filter (fun T => ∃ q : ℚ, Finset.sum T (fun x => x) = (q : ℝ))).card
        ≤ m * Nat.choose (n - m) (r - 1)) := by
  sorry
