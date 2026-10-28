from __future__ import annotations

import json
from dataclasses import asdict

from ..ui import truncate
from .types import SorryDrivenResult, SorryNode


def write_sorry_report(result: SorryDrivenResult) -> None:
    result.workspace_dir.mkdir(parents=True, exist_ok=True)
    result.workspace_dir.joinpath("sorry_tree.json").write_text(
        json.dumps(_result_json(result), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    result.workspace_dir.joinpath("sorry_tree.md").write_text(_markdown_report(result), encoding="utf-8")
    result.workspace_dir.joinpath("research_notebook.md").write_text(
        _research_notebook(result), encoding="utf-8"
    )


def _result_json(result: SorryDrivenResult) -> dict:
    return {
        "status": result.status,
        "workspace_dir": str(result.workspace_dir),
        "diagnostics": result.diagnostics,
        "research_memory": result.research_memory,
        "nodes": [_node_json(node) for node in result.nodes],
    }


def _node_json(node: SorryNode) -> dict:
    return asdict(node)


def _markdown_report(result: SorryDrivenResult) -> str:
    lines = ["# Sorry-Driven Proof Tree", "", f"- Status: `{result.status}`", f"- Nodes: `{len(result.nodes)}`", ""]
    if result.research_memory:
        blocked = result.research_memory.get("blocked_on") or []
        if blocked:
            lines.append("## Research Status")
            lines.extend(f"- Blocked on: `{item}`" for item in blocked)
            lines.append("")
    if result.diagnostics:
        lines.append("## Diagnostics")
        lines.extend(f"- {item}" for item in result.diagnostics)
        lines.append("")
    lines.append("## Nodes")
    for node in result.nodes:
        lines.extend(
            [
                "",
                f"### {node.id}",
                "",
                f"- Status: `{node.status}`",
                f"- Parent: `{node.parent_id or 'none'}`",
                f"- Depth: `{node.depth}`",
                f"- Children: `{', '.join(node.children) or 'none'}`",
            ]
        )
        if node.claim:
            lines.append(f"- Claim: {node.claim}")
        if node.diagnostics:
            lines.append("- Recent diagnostics:")
            lines.extend(f"  - {truncate(item, 300)}" for item in node.diagnostics[-3:])
    return "\n".join(lines).rstrip() + "\n"


def _research_notebook(result: SorryDrivenResult) -> str:
    memory = result.research_memory or {}
    lines = [
        "# Research Notebook",
        "",
        f"- Solver status: `{result.status}`",
        f"- Nodes: `{len(result.nodes)}`",
        "",
    ]
    sections = [
        ("Problem Blockers", "blocked_on"),
        ("Research Plan", "research_plan"),
        ("Invalid Witnesses", "invalid_witnesses"),
        ("Sanity Checks", "sanity_checks"),
        ("Promising Directions", "promising_directions"),
        ("Open Questions", "open_questions"),
        ("Notes", "notes"),
    ]
    wrote = False
    for title, key in sections:
        items = memory.get(key) or []
        if not items:
            continue
        wrote = True
        lines.extend([f"## {title}", ""])
        for item in items:
            if isinstance(item, dict):
                summary = item.get("summary") or item.get("reason") or json.dumps(item, ensure_ascii=False)
                lines.append(f"- {summary}")
                detail = item.get("detail") or item.get("evidence") or ""
                if detail:
                    lines.append(f"  Evidence: {truncate(str(detail), 500)}")
            else:
                lines.append(f"- {item}")
        lines.append("")
    if not wrote:
        lines.extend([
            "## Notes",
            "",
            "- No research-specific blockers or witness lessons were recorded in this run.",
            "",
        ])
    failed = [node for node in result.nodes if node.status == "failed"]
    if failed:
        lines.extend(["## Failed Nodes", ""])
        for node in failed:
            lines.append(f"- `{node.id}`: {node.claim or node.statement.splitlines()[0]}")
            if node.diagnostics:
                lines.append(f"  Last diagnostic: {truncate(node.diagnostics[-1], 500)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
