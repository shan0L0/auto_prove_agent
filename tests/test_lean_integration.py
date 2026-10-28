from pathlib import Path

import pytest

from agent.lean_runner import LeanRunner


pytestmark = pytest.mark.integration


def test_elan_lean_checks_minimal_proof():
    lean = Path("/Users/yuqiang/.elan/bin/lean")
    if not lean.exists():
        pytest.skip("elan Lean binary is not installed at /Users/yuqiang/.elan/bin/lean")

    runner = LeanRunner(Path("lean_workspace"), timeout_seconds=60, lean_command=[str(lean)])
    result = runner.check("example : 1 = 1 := by\n  rfl\n", file_name="Smoke.lean")
    assert result.ok, result.output
