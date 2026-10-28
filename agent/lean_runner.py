from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional, Sequence

from .models import LeanResult


DEFAULT_ELAN_BIN = Path("/Users/yuqiang/.elan/bin")


class LeanUnavailableError(RuntimeError):
    """Raised when no Lean executable can be found."""


class LeanRunner:
    """Runs Lean files through a Lake workspace when available."""

    def __init__(
        self,
        workspace: Path,
        timeout_seconds: Optional[float] = None,
        lean_command: Optional[Sequence[str]] = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.timeout_seconds = _normalize_timeout(timeout_seconds)
        self.lean_command = list(lean_command) if lean_command else None

    def is_available(self) -> bool:
        if self.lean_command:
            return _command_exists(self.lean_command[0])
        return _find_executable("lake") is not None or _find_executable("lean") is not None

    def command(self, file_path: Path) -> Sequence[str]:
        if self.lean_command:
            return [*self.lean_command, str(file_path)]
        lake = _find_executable("lake")
        if lake:
            return [lake, "env", "lean", str(file_path)]
        lean = _find_executable("lean")
        if lean:
            return [lean, str(file_path)]
        raise LeanUnavailableError("Lean is not installed or not on PATH.")

    def check(self, code: str, file_name: str = "Attempt.lean", allow_sorry: bool = False) -> LeanResult:
        if not self.is_available():
            raise LeanUnavailableError("Lean is not installed or not on PATH.")

        self.workspace.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="auto_prove_", dir=str(self.workspace)) as tmp_dir:
            file_path = Path(tmp_dir) / file_name
            file_path.write_text(code, encoding="utf-8")
            started = time.monotonic()
            try:
                completed = subprocess.run(
                    self.command(file_path),
                    cwd=self.workspace,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout_seconds,
                    check=False,
                )
                elapsed = time.monotonic() - started
                output = "\n".join(part for part in [completed.stdout, completed.stderr] if part)
                uses_sorry = (not allow_sorry) and _uses_sorry(output)
                return LeanResult(
                    ok=completed.returncode == 0 and not uses_sorry,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    returncode=completed.returncode,
                    elapsed_seconds=elapsed,
                    file_path=str(file_path),
                )
            except subprocess.TimeoutExpired as exc:
                elapsed = time.monotonic() - started
                return LeanResult(
                    ok=False,
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or f"Lean timed out after {self.timeout_seconds} seconds.",
                    returncode=124,
                    elapsed_seconds=elapsed,
                    timed_out=True,
                    file_path=str(file_path),
                )


def _find_executable(name: str) -> Optional[str]:
    env_name = f"AUTO_PROVE_{name.upper()}_BIN"
    configured = os.getenv(env_name)
    if configured and _command_exists(configured):
        return configured
    found = shutil.which(name)
    if found:
        return found
    elan_candidate = DEFAULT_ELAN_BIN / name
    if _command_exists(str(elan_candidate)):
        return str(elan_candidate)
    return None


def _command_exists(command: str) -> bool:
    path = Path(command)
    if path.is_absolute() or "/" in command:
        return path.exists() and os.access(path, os.X_OK)
    return shutil.which(command) is not None


def _normalize_timeout(timeout_seconds: Optional[float]) -> Optional[float]:
    if timeout_seconds is None or timeout_seconds <= 0:
        return None
    return timeout_seconds


def _uses_sorry(output: str) -> bool:
    return "declaration uses `sorry`" in output or "depends on axioms: [sorryAx]" in output
