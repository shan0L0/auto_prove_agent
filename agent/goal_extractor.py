from __future__ import annotations

import re
from typing import TYPE_CHECKING, List, Optional

from .lean_runner import LeanRunner, LeanUnavailableError

if TYPE_CHECKING:
    from .lean_lsp import LeanLSPClient


GOAL_MARKERS = (
    "unsolved goals",
    "case ",
    "⊢",
)


def extract_goals(lean_output: str) -> List[str]:
    """Extract useful goal blocks from Lean diagnostics.

    Lean prints goal states in several shapes depending on tactic and options.
    This parser stays intentionally permissive for the MVP: it returns blocks
    that include turnstile lines or the text following "unsolved goals".
    """

    text = lean_output.strip()
    if not text:
        return []

    blocks: List[str] = []
    unsolved = re.split(r"unsolved goals", text, flags=re.IGNORECASE)
    if len(unsolved) > 1:
        for block in unsolved[1:]:
            cleaned = _clean_goal_block(block)
            if cleaned:
                blocks.append(cleaned)

    current: List[str] = []
    for line in text.splitlines():
        if "⊢" in line or line.strip().startswith("case "):
            if current:
                blocks.append(_clean_goal_block("\n".join(current)))
                current = []
            current.append(line)
        elif current and (line.startswith(" ") or line.startswith("\t") or ":" in line):
            current.append(line)
        elif current:
            blocks.append(_clean_goal_block("\n".join(current)))
            current = []
    if current:
        blocks.append(_clean_goal_block("\n".join(current)))

    deduped: List[str] = []
    for block in blocks:
        if not block:
            continue
        if any(block == existing or block in existing for existing in deduped):
            continue
        deduped = [existing for existing in deduped if existing not in block]
        if block not in deduped:
            deduped.append(block)
    return deduped


def _clean_goal_block(block: str) -> str:
    lines = [line.rstrip() for line in block.strip().splitlines()]
    useful = []
    for line in lines:
        if line.startswith("error:") or line.startswith("warning:"):
            continue
        useful.append(line)
    return "\n".join(useful).strip()


def inject_trace_state(theorem: str) -> str:
    """Replace a placeholder proof with a traceable incomplete proof."""

    if ":= by" in theorem:
        prefix = theorem.split(":= by", 1)[0]
        return f"{prefix}:= by\n  trace_state\n  sorry\n"
    return theorem.rstrip() + "\n"


def extract_initial_goals(runner: LeanRunner, header: str, theorem: str) -> List[str]:
    traced = header + inject_trace_state(theorem)
    try:
        result = runner.check(traced, file_name="TraceState.lean", allow_sorry=True)
    except LeanUnavailableError:
        return []
    return extract_goals(result.output)


def extract_sorry_goals_lsp(
    lsp: "LeanLSPClient",
    code: str,
) -> List[str]:
    """Extract goal states at every ``sorry`` in *code* using the LSP server.

    This is more reliable than the ``trace_state`` + regex approach: the Lean
    server returns structured goal information (all hypotheses + the turnstile
    target) at the exact tactic position of each sorry.

    :param lsp: A started :class:`~agent.lean_lsp.LeanLSPClient`.
    :param code: Complete Lean source including imports and any preamble.
    :returns: Rendered goal strings, one per sorry, in source order.
              Returns an empty list if the LSP query fails.
    """
    try:
        goals = lsp.query_sorry_goals(code)
        return [g.goal_text for g in goals]
    except Exception:
        return []
