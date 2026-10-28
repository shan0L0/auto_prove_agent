from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class TokenTracker:
    """Thread-safe accumulator for LLM input/output token usage.

    Forked LLM clients share a single tracker by reference, so concurrent
    workers all contribute to the same totals. Use :meth:`snapshot` /
    :meth:`report_since` to extract the slice of records produced during a
    bounded run (e.g. one workspace's solve).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.records: List[Dict[str, Any]] = []

    def add(
        self,
        phase: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "",
    ) -> None:
        with self._lock:
            self.records.append(
                {
                    "time": datetime.now().isoformat(timespec="seconds"),
                    "phase": phase,
                    "model": model,
                    "input_tokens": int(input_tokens or 0),
                    "output_tokens": int(output_tokens or 0),
                }
            )

    def snapshot(self) -> int:
        """Return the current record count for later delta computation."""
        with self._lock:
            return len(self.records)

    def report_since(self, start: int = 0) -> Dict[str, Any]:
        with self._lock:
            slice_records = list(self.records[start:])
        input_total = sum(r["input_tokens"] for r in slice_records)
        output_total = sum(r["output_tokens"] for r in slice_records)
        by_phase: Dict[str, Dict[str, int]] = {}
        for record in slice_records:
            bucket = by_phase.setdefault(
                record["phase"], {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += record["input_tokens"]
            bucket["output_tokens"] += record["output_tokens"]
        return {
            "input_tokens": input_total,
            "output_tokens": output_total,
            "total_tokens": input_total + output_total,
            "calls": len(slice_records),
            "by_phase": by_phase,
            "records": slice_records,
        }

    def write_json(self, path: Path, start: int = 0) -> Path:
        report = self.report_since(start)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return path


def extract_usage_metadata(raw_message: Any) -> Optional[Dict[str, int]]:
    """Pull `{input_tokens, output_tokens}` from a LangChain raw message.

    LangChain normalises provider responses onto ``BaseMessage.usage_metadata``
    (a dict with ``input_tokens`` / ``output_tokens`` / ``total_tokens``).
    Returns ``None`` if the message does not carry usage info — e.g. mock
    objects in unit tests or providers that omit the field.
    """
    if raw_message is None:
        return None
    usage = getattr(raw_message, "usage_metadata", None)
    if not usage:
        return None
    if isinstance(usage, dict):
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }
    return None
