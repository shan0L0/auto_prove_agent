from __future__ import annotations


def classify_lean_error(output: str) -> str:
    text = output.lower()
    if not text:
        return "none"
    if "timed out" in text:
        return "timeout"
    if "declaration uses `sorry`" in output or "depends on axioms: [sorryAx]" in output:
        return "uses_sorry"
    if "unexpected token" in text or "expected" in text and "command" in text:
        return "syntax_error"
    if "unknown identifier" in text or "unknown constant" in text or "unknown declaration" in text:
        return "unknown_identifier"
    if "unknown module prefix" in text:
        return "missing_import_or_dependency"
    if "type mismatch" in text or "application type mismatch" in text:
        return "type_mismatch"
    if "unsolved goals" in text:
        return "unsolved_goals"
    if "tactic" in text and "failed" in text:
        return "tactic_failed"
    return "lean_error"
