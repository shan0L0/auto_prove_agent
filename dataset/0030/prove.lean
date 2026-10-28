import Mathlib

set_option autoImplicit false

theorem group_algebra_iso_implies_group_iso_of_order_dvd_64
    (R G H : Type*) [CommRing R] [Group G] [Fintype G] [Group H]
    (h2 : ¬ IsUnit (2 : R))
    (hG : Fintype.card G ∣ 64)
    (halg : MonoidAlgebra R G ≃ₐ[R] MonoidAlgebra R H) :
    Nonempty (G ≃* H) := by
  sorry
