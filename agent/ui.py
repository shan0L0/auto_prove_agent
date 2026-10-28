from __future__ import annotations

import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO


class ConsoleReporter:
    """Small human-readable CLI reporter."""

    def __init__(self, enabled: bool = True, verbose: bool = False, stream: TextIO = sys.stdout) -> None:
        self.enabled = enabled
        self.verbose = verbose
        self.stream = stream

    def title(self, text: str) -> None:
        if not self.enabled:
            return
        self._write("")
        self._write("=" * 78)
        self._write(text)
        self._write("=" * 78)

    def step(self, text: str) -> None:
        if self.enabled:
            self._write(f"[{_now()}] -> {text}")

    def info(self, text: str) -> None:
        if self.enabled:
            self._write(f"   {text}")

    def detail(self, text: str) -> None:
        if self.enabled and self.verbose:
            self._write(_indent(text, "      "))

    def success(self, text: str) -> None:
        if self.enabled:
            self._write(f"[{_now()}] OK {text}")

    def warning(self, text: str) -> None:
        if self.enabled:
            self._write(f"[{_now()}] WARN {text}")

    def error(self, text: str) -> None:
        if self.enabled:
            self._write(f"[{_now()}] ERROR {text}")

    def path(self, label: str, path: Path) -> None:
        self.info(f"{label}: {path}")

    def _write(self, text: str) -> None:
        print(text, file=self.stream, flush=True)


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _indent(text: str, prefix: str) -> str:
    wrapped = []
    for line in textwrap.dedent(text).strip().splitlines():
        wrapped.append(prefix + line)
    return "\n".join(wrapped)


def truncate(text: str, limit: int = 500) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "\n... [truncated]"
