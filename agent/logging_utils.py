from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict


class RunLogger:
    def __init__(self, root: Path = Path("runs")) -> None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = root / stamp
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.run_dir / "trace.jsonl"
        self.llm_trace_path = self.run_dir / "llm_trace.jsonl"
        self.llm_markdown_path = self.run_dir / "llm_trace.md"
        self.llm_markdown_path.write_text("# LLM Trace\n\n", encoding="utf-8")

    def event(self, name: str, payload: Dict[str, Any]) -> None:
        with self.trace_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"time": datetime.now().isoformat(timespec="seconds"), "event": name, **payload},
                    ensure_ascii=False,
                )
                + "\n"
            )

    def final(self, code: str) -> Path:
        path = self.run_dir / "final.lean"
        path.write_text(code, encoding="utf-8")
        return path

    def llm_interaction(self, phase: str, payload: Dict[str, Any]) -> None:
        record = {"time": datetime.now().isoformat(timespec="seconds"), "phase": phase, **payload}
        with self.llm_trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        with self.llm_markdown_path.open("a", encoding="utf-8") as handle:
            handle.write(_llm_markdown(record))


def _llm_markdown(record: Dict[str, Any]) -> str:
    lines = [
        f"## {record.get('phase', 'unknown')} - {record.get('time', '')}",
        "",
        f"- Mode: `{record.get('mode', 'unknown')}`",
        f"- Error: `{record.get('error', '') or 'none'}`",
    ]
    if "candidate_count" in record:
        lines.append(f"- Candidate count: `{record.get('candidate_count')}`")
    if record.get("request_messages"):
        lines.extend(["", "### Prompt Messages"])
        for index, item in enumerate(record["request_messages"], start=1):
            role = item.get("role", "unknown") if isinstance(item, dict) else "unknown"
            content = item.get("content", item) if isinstance(item, dict) else item
            lines.extend(["", f"#### {index}. {role}", "", "```text", str(content).strip(), "```"])
    if record.get("response_text"):
        lines.extend(["", "### Response", "", "```text", str(record["response_text"]).strip(), "```"])
    return "\n".join(lines).rstrip() + "\n\n"
