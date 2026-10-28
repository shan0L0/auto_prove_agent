from agent.error_classifier import classify_lean_error


def test_classify_common_lean_errors():
    assert classify_lean_error("error: unsolved goals\n⊢ True") == "unsolved_goals"
    assert classify_lean_error("error: unknown identifier 'foo'") == "unknown_identifier"
    assert classify_lean_error("error: type mismatch") == "type_mismatch"
    assert classify_lean_error("warning: declaration uses `sorry`") == "uses_sorry"

