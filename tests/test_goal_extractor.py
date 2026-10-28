from agent.goal_extractor import extract_goals, inject_trace_state


def test_extracts_unsolved_goal_block():
    output = """
error: unsolved goals
x : Nat
⊢ x = x
"""
    assert extract_goals(output) == ["x : Nat\n⊢ x = x"]


def test_extracts_turnstile_goal():
    output = "some diagnostic\ncase h\n  n : Nat\n  ⊢ n + 0 = n\n"
    goals = extract_goals(output)
    assert any("⊢ n + 0 = n" in goal for goal in goals)


def test_inject_trace_state_replaces_by_body():
    theorem = "example : True := by\n  trivial"
    traced = inject_trace_state(theorem)
    assert "trace_state" in traced
    assert "sorry" in traced

