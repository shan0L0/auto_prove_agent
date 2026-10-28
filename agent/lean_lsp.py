"""
lean_lsp.py – Minimal Lean 4 LSP client.

Speaks the Language Server Protocol (JSON-RPC over stdin/stdout) with the Lean
language server to extract **proof-state at ``sorry`` positions**.

No external dependencies.  Works with Python 3.10 and later.

Usage::

    from agent.lean_lsp import LeanLSPClient

    with LeanLSPClient(workspace=Path("lean_workspace")) as lsp:
        goals = lsp.query_sorry_goals(code)
        for g in goals:
            print(f"Line {g.line}: {g.goal_text}")
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Regex for parsing the LSP Content-Length header
# ---------------------------------------------------------------------------
_CONTENT_LEN = re.compile(rb"Content-Length:\s*(\d+)", re.IGNORECASE)
_SORRY_RE = re.compile(r"\bsorry\b")

# Default elan bin path on macOS / Linux
_ELAN_BIN = Path.home() / ".elan" / "bin"


class LeanLSPError(RuntimeError):
    """Raised when the LSP client encounters an unrecoverable error."""


@dataclass(frozen=True)
class SorryGoal:
    """Proof state extracted at a single ``sorry`` position."""

    line: int        # 0-indexed line number of the sorry
    column: int      # 0-indexed column where sorry starts
    goal_text: str   # rendered goal: hypotheses + ``⊢ target``


class LeanLSPClient:
    """
    Minimal Lean 4 LSP client for extracting proof-state at ``sorry`` positions.

    The client starts ``lake env lean --server`` (or a custom command), sends
    LSP ``initialize`` / ``textDocument/didOpen``, waits for the
    ``$/lean/fileProgress`` completion notification, then calls
    ``$/lean/plainGoal`` at each ``sorry`` position.

    :param workspace: Path to the Lake project root (where ``lakefile.lean``
        lives).  The server is started with ``cwd=workspace``.
    :param timeout: Maximum seconds to wait for elaboration or any single
        request.
    :param lean_command: Override the server command list.  Defaults to
        ``[lake, "env", "lean", "--server"]`` or ``[lean, "--server"]``.
    """

    def __init__(
        self,
        workspace: Path,
        timeout: float = 180.0,
        lean_command: Optional[List[str]] = None,
    ) -> None:
        self.workspace = Path(workspace)
        self.timeout = timeout
        self._cmd: List[str] = lean_command or _default_server_command()

        self._proc: Optional[subprocess.Popen] = None
        self._running = False

        # Request ID counter
        self._next_id = 1
        self._id_lock = threading.Lock()

        # Pending responses keyed by request id
        self._responses: Dict[int, dict] = {}
        self._resp_lock = threading.Lock()
        self._resp_ready = threading.Event()

        # All server-push notifications (appended by reader thread, GIL-safe)
        self._notifications: List[dict] = []

        self._reader: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "LeanLSPClient":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the Lean server process and perform LSP handshake."""
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen(
            self._cmd,
            cwd=str(self.workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._running = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        self._handshake()

    def stop(self) -> None:
        """Terminate the server."""
        self._running = False
        if self._proc:
            try:
                self._notify("exit", {})
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                pass
            self._proc = None

    @classmethod
    def is_available(cls) -> bool:
        """Return True if a Lean server executable can be found."""
        return bool(_default_server_command())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query_sorry_goals(
        self,
        code: str,
        file_name: str = "_SorryQuery.lean",
    ) -> List[SorryGoal]:
        """
        Open *code* as a virtual document and return the proof state at
        every ``sorry`` keyword.

        :param code: Complete Lean source including any ``import`` lines.
        :param file_name: Virtual filename for the LSP document (must end in
            ``.lean``).
        :returns: One :class:`SorryGoal` per sorry, in source order.
        """
        sorry_positions = _find_sorrys(code)
        if not sorry_positions:
            return []

        uri = self._file_uri(file_name)
        self._open_document(uri, code)
        try:
            self._wait_elaboration(uri)
            results: List[SorryGoal] = []
            for line, col in sorry_positions:
                goal = self._plain_goal(uri, line, col)
                if goal:
                    results.append(SorryGoal(line=line, column=col, goal_text=goal))
            return results
        finally:
            self._close_document(uri)

    # ------------------------------------------------------------------
    # LSP operations
    # ------------------------------------------------------------------

    def _handshake(self) -> None:
        rid = self._request("initialize", {
            "processId": os.getpid(),
            "rootUri": self.workspace.as_uri(),
            "capabilities": {
                "textDocument": {
                    "synchronization": {"dynamicRegistration": False},
                }
            },
            "initializationOptions": {},
        })
        self._wait_response(rid)
        self._notify("initialized", {})

    def _open_document(self, uri: str, text: str) -> None:
        self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": "lean4",
                "version": 1,
                "text": text,
            }
        })

    def _close_document(self, uri: str) -> None:
        try:
            self._notify("textDocument/didClose", {
                "textDocument": {"uri": uri}
            })
        except Exception:
            pass

    def _plain_goal(self, uri: str, line: int, col: int) -> Optional[str]:
        """Send ``$/lean/plainGoal`` and return the rendered goal text."""
        rid = self._request("$/lean/plainGoal", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })
        try:
            resp = self._wait_response(rid, timeout=30.0)
        except LeanLSPError:
            return None
        result = resp.get("result")
        if not result:
            return None
        return result.get("rendered") or result.get("goal") or None

    def _wait_elaboration(self, uri: str) -> None:
        """
        Block until Lean finishes elaborating *uri*.

        Lean sends ``$/lean/fileProgress`` notifications while working.
        The final notification for the file has an empty ``processing`` list.
        We wait for at least one non-empty notification (so Lean has started),
        then wait for an empty one (Lean has finished).  If neither pattern
        appears within *timeout* seconds we return anyway and let goal queries
        succeed or fail on their own.
        """
        deadline = time.monotonic() + self.timeout
        seen_busy = False

        while time.monotonic() < deadline:
            for msg in list(self._notifications):   # snapshot — safe under GIL
                if msg.get("method") != "$/lean/fileProgress":
                    continue
                params = msg.get("params", {})
                if params.get("textDocument", {}).get("uri") != uri:
                    continue
                processing = params.get("processing", [])
                if processing:
                    seen_busy = True
                elif seen_busy:
                    return   # empty processing after having been busy → done
            time.sleep(0.25)

        # Timed out waiting for the progress cycle — proceed anyway; the server
        # may have been extremely fast or the notification may have been missed.

    # ------------------------------------------------------------------
    # JSON-RPC transport
    # ------------------------------------------------------------------

    def _request(self, method: str, params: dict) -> int:
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, msg: dict) -> None:
        assert self._proc and self._proc.stdin, "Server not running"
        body = json.dumps(msg, ensure_ascii=False).encode()
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        self._proc.stdin.write(frame)
        self._proc.stdin.flush()

    def _wait_response(self, rid: int, timeout: Optional[float] = None) -> dict:
        deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
        while time.monotonic() < deadline:
            with self._resp_lock:
                if rid in self._responses:
                    return self._responses.pop(rid)
            self._resp_ready.wait(timeout=0.1)
            self._resp_ready.clear()
        raise LeanLSPError(f"Timeout waiting for LSP response to request {rid}")

    # ------------------------------------------------------------------
    # Reader thread
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        """Background thread: parse Content-Length-framed JSON-RPC messages."""
        try:
            stdout = self._proc.stdout
            while self._running:
                # ---- Read header section (up to \r\n\r\n) ----
                raw_header = b""
                while b"\r\n\r\n" not in raw_header:
                    ch = stdout.read(1)
                    if not ch:
                        return
                    raw_header += ch

                m = _CONTENT_LEN.search(raw_header)
                if not m:
                    continue
                content_len = int(m.group(1))

                # ---- Read body ----
                body = b""
                while len(body) < content_len:
                    chunk = stdout.read(content_len - len(body))
                    if not chunk:
                        return
                    body += chunk

                # ---- Dispatch ----
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    continue
                self._dispatch(msg)
        except Exception:
            pass  # Server died; main thread will handle timeouts

    def _dispatch(self, msg: dict) -> None:
        """Route an incoming message to the right bucket."""
        if "id" in msg and "method" not in msg:
            # Response to one of our requests
            with self._resp_lock:
                self._responses[msg["id"]] = msg
            self._resp_ready.set()
        else:
            # Server notification or server-initiated request
            self._notifications.append(msg)   # GIL-safe append

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _file_uri(self, file_name: str) -> str:
        return (self.workspace / file_name).as_uri()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _default_server_command() -> List[str]:
    """Build the command list for starting the Lean LSP server."""
    lake = shutil.which("lake") or str(_ELAN_BIN / "lake")
    if Path(lake).exists() or shutil.which(lake):
        return [lake, "env", "lean", "--server"]
    lean = shutil.which("lean") or str(_ELAN_BIN / "lean")
    if Path(lean).exists() or shutil.which(lean):
        return [lean, "--server"]
    return []


def _find_sorrys(code: str) -> List[Tuple[int, int]]:
    """Return ``(line, column)`` for every ``sorry`` keyword in *code* (0-indexed)."""
    positions: List[Tuple[int, int]] = []
    for lineno, line in enumerate(code.splitlines()):
        stripped = line.strip()
        if stripped.startswith("--"):
            continue   # pure comment line
        # Ignore inline comments: everything after `--`
        code_part = line.split("--", 1)[0]
        for m in _SORRY_RE.finditer(code_part):
            positions.append((lineno, m.start()))
    return positions
