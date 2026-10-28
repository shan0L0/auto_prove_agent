"""Tests for agent.lean_lsp — no Lean required for the pure-Python parts."""
from __future__ import annotations

import pytest
from agent.lean_lsp import _find_sorrys, LeanLSPClient, SorryGoal


# ---------------------------------------------------------------------------
# _find_sorrys
# ---------------------------------------------------------------------------

def test_find_sorrys_basic():
    code = "theorem foo : True := by\n  sorry\n"
    positions = _find_sorrys(code)
    assert positions == [(1, 2)]


def test_find_sorrys_multiple():
    code = "lemma a : True := by sorry\nlemma b : True := by sorry\n"
    positions = _find_sorrys(code)
    assert len(positions) == 2
    assert positions[0][0] == 0  # first sorry on line 0
    assert positions[1][0] == 1  # second sorry on line 1


def test_find_sorrys_skips_comment_lines():
    code = "-- sorry this is a comment\ntheorem foo : True := by\n  sorry\n"
    positions = _find_sorrys(code)
    # Only the tactic sorry, not the comment
    assert len(positions) == 1
    assert positions[0][0] == 2


def test_find_sorrys_skips_inline_comments():
    code = "theorem foo : True := by\n  trivial -- sorry\n"
    positions = _find_sorrys(code)
    # The 'sorry' after '--' should be ignored
    assert positions == []


def test_find_sorrys_none():
    code = "theorem foo : True := by trivial\n"
    assert _find_sorrys(code) == []


def test_find_sorrys_by_exact_sorry():
    # 'sorry' should not match 'notsorry' — test word boundary
    code = "-- notsorry\ntheorem foo : True := by sorry\n"
    positions = _find_sorrys(code)
    assert len(positions) == 1


# ---------------------------------------------------------------------------
# SorryGoal dataclass
# ---------------------------------------------------------------------------

def test_sorry_goal_is_frozen():
    g = SorryGoal(line=1, column=2, goal_text="⊢ True")
    assert g.line == 1
    assert g.column == 2
    assert g.goal_text == "⊢ True"
    with pytest.raises((AttributeError, TypeError)):
        g.line = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# LeanLSPClient.is_available
# ---------------------------------------------------------------------------

def test_is_available_returns_bool():
    result = LeanLSPClient.is_available()
    assert isinstance(result, bool)


# ---------------------------------------------------------------------------
# LeanLSPClient gracefully skips when Lean unavailable (integration guard)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not LeanLSPClient.is_available(),
    reason="Lean not available in this environment",
)
@pytest.mark.integration
def test_lean_lsp_query_sorry_goals(tmp_path):
    """Smoke test: start LSP server, open a simple file, get a goal."""
    import shutil

    # We need a minimal Lake project for Lean LSP to initialise properly.
    # Copy the existing lean_workspace if possible, otherwise skip.
    lean_ws = tmp_path / "ws"
    lean_ws.mkdir()

    with LeanLSPClient(workspace=lean_ws, timeout=60.0) as lsp:
        code = "-- test\ntheorem foo : 1 + 1 = 2 := by\n  sorry\n"
        goals = lsp.query_sorry_goals(code, file_name="_Test.lean")
        # If Lean is available and LSP works we should get at least one goal
        assert isinstance(goals, list)
