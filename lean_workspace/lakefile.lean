import Lake
open Lake DSL

package «lean_workspace» where

require mathlib from git
  "https://github.com/leanprover-community/mathlib4.git"

@[default_target]
lean_lib AutoProve where

