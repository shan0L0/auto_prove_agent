"""
tui.py — Live proof-tree TUI for the sorry-driven proof search.

Renders a continuously-updated terminal view showing:

  • the proof-obligation tree (node id, status, claim) with Unicode box-drawing
  • a scrolling activity log with timestamps
  • elapsed time and node-count statistics in the panel border

Usage (from main.py)::

    from agent.tui import run_with_tui
    destination = run_with_tui(
        solve_problem_directory_sorry_driven,
        problem_dir=..., output_dir=..., client=..., ...
    )

The solver runs in a background thread; the Rich Live widget runs in the
main thread so that Ctrl-C is handled cleanly.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.tree import Tree

from .ui import ConsoleReporter

# ---------------------------------------------------------------------------
# Status icons and colours
# ---------------------------------------------------------------------------

_STATUS_ICON: Dict[str, Tuple[str, str]] = {
    "sorry":       ("○", "dim white"),
    "in_progress": ("◉", "blue"),
    "proved":      ("✓", "bold green"),
    "failed":      ("✗", "bold red"),
}
_ACTIVE_ICON:  Tuple[str, str] = ("⟳", "bold yellow")
_STATUS_COLOR: Dict[str, str] = {
    "sorry":       "dim white",
    "in_progress": "blue",
    "proved":      "green",
    "failed":      "red",
}

# Phase-specific display: (icon, icon_style, label, label_style)
# Lean phases → cyan;  LLM phases → magenta
_PHASE_DISPLAY: Dict[str, Tuple[str, str, str, str]] = {
    "lean_closing":  ("▶", "bold cyan",    "Lean: auto",      "cyan"),
    "lean_verifying":("▶", "bold cyan",    "Lean: verifying", "cyan"),
    "lean_checking": ("▶", "bold cyan",    "Lean: checking",  "cyan"),
    "llm_proving":   ("✦", "bold magenta", "LLM: proving",    "magenta"),
    "llm_designing": ("✦", "bold magenta", "LLM: designing",  "magenta"),
    "llm_splitting": ("✦", "bold magenta", "LLM: splitting",  "magenta"),
}

# Picks a node id out of a log message ("Splitting sg2 into …" → "sg2")
_NODE_ID_RE = re.compile(r"\b(root|sg\d+(?:_\d+)*)\b")

# ---------------------------------------------------------------------------
# TUIState — shared between the prover thread and the render thread
# ---------------------------------------------------------------------------

class TUIState:
    """
    Thread-safe snapshot of the prover's current state.

    The prover thread writes via ``update_node`` / ``log``.
    The render thread reads via ``snapshot_*`` helpers.
    All mutations are protected by a single ``threading.Lock``.
    """

    def __init__(self) -> None:
        self.theorem: str = ""
        self._nodes: Dict[str, dict] = {}     # id → node dict
        self._current_id: str = ""            # node currently being worked on
        self._logs: deque = deque(maxlen=30)  # (timestamp, style, message)
        self.start_time: float = time.monotonic()
        self.finished: bool = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ writes

    def update_node(self, node_data: dict) -> None:
        """Upsert a node snapshot from a structured event payload."""
        nid = node_data.get("id")
        if not nid:
            return
        with self._lock:
            existing = self._nodes.get(nid, {})
            merged = {**existing, **node_data}
            # Clear phase when the node reaches any non-active status:
            #   proved / failed  → terminal
            #   in_progress      → split accepted, node is done being worked on
            if merged.get("status") in {"proved", "failed", "in_progress"}:
                merged.pop("phase", None)
            self._nodes[nid] = merged

    def set_phase(self, node_id: str, phase: str) -> None:
        """Update the activity phase of a node (e.g. 'llm_splitting')."""
        with self._lock:
            node = self._nodes.get(node_id)
            if node is not None:
                node["phase"] = phase

    def set_active(self, node_id: str) -> None:
        with self._lock:
            self._current_id = node_id

    def log(self, style: str, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        with self._lock:
            self._logs.append((ts, style, message))
        # Opportunistically track which node is being worked on
        m = _NODE_ID_RE.search(message)
        if m:
            with self._lock:
                self._current_id = m.group(1)

    # ------------------------------------------------------------------ reads

    def snapshot_nodes(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._nodes)

    def snapshot_logs(self) -> List[Tuple[str, str, str]]:
        with self._lock:
            return list(self._logs)

    def snapshot_current(self) -> str:
        with self._lock:
            return self._current_id

    def stats(self) -> Dict[str, int]:
        with self._lock:
            nodes = list(self._nodes.values())
        return {
            "total":       len(nodes),
            "proved":      sum(1 for n in nodes if n.get("status") == "proved"),
            "in_progress": sum(1 for n in nodes if n.get("status") == "in_progress"),
            "waiting":     sum(1 for n in nodes if n.get("status") == "sorry"),
            "failed":      sum(1 for n in nodes if n.get("status") == "failed"),
        }

    def elapsed(self) -> str:
        secs = int(time.monotonic() - self.start_time)
        m, s = divmod(secs, 60)
        h, m = divmod(m, 60)
        return f"{h}h {m:02d}m {s:02d}s" if h else f"{m:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# TUIReporter — ConsoleReporter subclass that feeds TUIState
# ---------------------------------------------------------------------------

class TUIReporter(ConsoleReporter):
    """
    Captures every reporter call and feeds it into a :class:`TUIState`
    instead of printing to stdout.

    Also implements ``on_event(event_type, payload)`` which is called by
    ``SorryDrivenProver._event()`` for structured node-lifecycle events.
    """

    def __init__(self, state: TUIState) -> None:
        super().__init__(enabled=False)   # suppress all stdout printing
        self.state = state

    # ---------------------------------------------------------------- reporter

    def title(self, text: str) -> None:
        self.state.log("bold white", text)

    def step(self, text: str) -> None:
        self.state.log("cyan", f"→ {text}")

    def info(self, text: str) -> None:
        self.state.log("dim", f"  {text}")

    def detail(self, text: str) -> None:
        self.state.log("dim", f"  {text}")

    def success(self, text: str) -> None:
        self.state.log("green", f"✓ {text}")

    def warning(self, text: str) -> None:
        self.state.log("yellow", f"⚠ {text}")

    def error(self, text: str) -> None:
        self.state.log("red", f"✗ {text}")

    def path(self, label: str, path: Any) -> None:
        self.state.log("dim", f"  {label}: {path}")

    # ----------------------------------------------------------------- events

    def on_event(self, event_type: str, data: dict) -> None:
        """
        Called by ``SorryDrivenProver._event()`` for every structured event.
        Updates the node snapshot and active-node tracker.
        """
        # Extract node snapshot from various event shapes
        node_data: Optional[dict] = None
        if isinstance(data, dict):
            if "node" in data and isinstance(data["node"], dict):
                node_data = data["node"]
            elif "id" in data and "status" in data:
                node_data = data

        if node_data:
            self.state.update_node(node_data)

        # Track root theorem text
        if event_type == "sorry_node_created":
            nd = node_data or {}
            if nd.get("id") == "root":
                self.state.theorem = nd.get("statement", "")

        # Mark the node that is about to be iterated over as active
        if event_type == "sorry_iteration_started" and isinstance(data, dict):
            nd = data.get("node", {})
            if nd.get("id"):
                self.state.set_active(nd["id"])

        # Track per-node activity phase (lean_closing / llm_proving / …)
        if event_type == "sorry_phase_changed" and isinstance(data, dict):
            nid   = data.get("node_id")
            phase = data.get("phase", "")
            if nid:
                self.state.set_phase(nid, phase)


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _node_label(node: dict, current_id: str) -> Text:
    nid    = node.get("id", "?")
    status = node.get("status", "sorry")
    claim  = _clip(node.get("claim", ""), 50)
    # Phase is set while a node is being actively worked on (status still
    # "sorry" at that point), so read it regardless of status.
    phase  = node.get("phase", "")
    active = (nid == current_id)

    phase_info = _PHASE_DISPLAY.get(phase) if phase else None

    if active and phase_info:
        # Active + known phase: phase icon overrides the spinner
        p_icon, p_icon_style, p_label, p_label_style = phase_info
        icon, icon_style = p_icon, p_icon_style
        status_label = p_label
        label_color  = p_label_style
    elif active:
        icon, icon_style = _ACTIVE_ICON
        status_label = status
        label_color  = _STATUS_COLOR.get(status, "white")
    elif phase_info:
        # Not the current node but has a phase (shouldn't normally happen,
        # but handle it gracefully)
        p_icon, p_icon_style, p_label, p_label_style = phase_info
        icon, icon_style = p_icon, p_icon_style
        status_label = p_label
        label_color  = p_label_style
    else:
        icon, icon_style = _STATUS_ICON.get(status, ("?", "white"))
        status_label = status
        label_color  = _STATUS_COLOR.get(status, "white")

    t = Text()
    t.append(f"{icon} ", style=icon_style)
    t.append(nid, style=f"bold {label_color}")
    if claim:
        t.append(f"  {claim}", style="dim")
    t.append(f"  [{status_label}]", style=label_color)
    return t


def _build_tree(nodes: Dict[str, dict], current_id: str) -> Optional[Tree]:
    """Recursively build a rich Tree from the node snapshot dict."""
    if not nodes:
        return None
    roots = [n for n in nodes.values() if not n.get("parent_id")]
    if not roots:
        return None

    def _add(tree_node: Tree, node: dict) -> None:
        for child_id in node.get("children", []):
            child = nodes.get(child_id)
            if not child:
                continue
            branch = tree_node.add(_node_label(child, current_id))
            _add(branch, child)

    root = roots[0]
    widget = Tree(_node_label(root, current_id))
    _add(widget, root)
    return widget


def _render(state: TUIState) -> Panel:
    """Build the full panel renderable from the current :class:`TUIState`."""
    nodes      = state.snapshot_nodes()
    current_id = state.snapshot_current()
    logs       = state.snapshot_logs()
    stats      = state.stats()
    elapsed    = state.elapsed()

    parts: List[Any] = []

    # ── Theorem header ────────────────────────────────────────────────────
    theorem = _clip(state.theorem.strip(), 90) or "…"
    hdr = Text()
    hdr.append("Proving  ", style="dim")
    hdr.append(theorem, style="italic")
    parts.append(hdr)
    parts.append(Text(""))

    # ── Proof tree ────────────────────────────────────────────────────────
    parts.append(Rule("[dim]Proof Tree[/dim]", style="dim"))
    parts.append(Text(""))
    tree_widget = _build_tree(nodes, current_id)
    if tree_widget:
        parts.append(tree_widget)
    else:
        parts.append(Text("  (waiting for first node…)", style="dim"))
    parts.append(Text(""))

    # ── Activity log ──────────────────────────────────────────────────────
    parts.append(Rule("[dim]Activity[/dim]", style="dim"))
    parts.append(Text(""))
    visible_logs = logs[-14:]
    if visible_logs:
        for ts, style, msg in visible_logs:
            line = Text()
            line.append(f"{ts}  ", style="dim")
            # escape markup in arbitrary log messages
            line.append(escape(msg), style=style)
            parts.append(line)
    else:
        parts.append(Text("  (no activity yet)", style="dim"))
    parts.append(Text(""))

    # ── Panel title (stats bar) ───────────────────────────────────────────
    if state.finished:
        title_icon = "✓" if stats["failed"] == 0 else "✗"
    else:
        title_icon = "⟳"

    title = Text(justify="left")
    title.append(f" {title_icon} auto-prove", style="bold")
    title.append(f"  {elapsed}", style="dim")
    if stats["total"]:
        title.append("   ")
        title.append(f"{stats['proved']}✓", style="green")
        title.append(" ")
        title.append(f"{stats['waiting']}○", style="dim white")
        title.append(" ")
        title.append(f"{stats['in_progress']}◉", style="blue")
        if stats["failed"]:
            title.append(" ")
            title.append(f"{stats['failed']}✗", style="red")

    border = "green" if (state.finished and stats["failed"] == 0) \
        else "red"  if (state.finished and stats["failed"] > 0) \
        else "blue"

    return Panel(
        Group(*parts),
        title=title,
        border_style=border,
        padding=(0, 1),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_with_tui(solver_fn: Callable, **kwargs: Any) -> Any:
    """
    Run *solver_fn* in a background thread while a Rich TUI renders in the
    main thread.

    ``solver_fn`` must accept a ``reporter`` keyword argument (of type
    :class:`~agent.ui.ConsoleReporter`).  All other keyword arguments in
    *kwargs* are forwarded to *solver_fn* unchanged.

    Returns whatever *solver_fn* returns, or re-raises any exception it
    raises.

    Example::

        result = run_with_tui(
            solve_problem_directory_sorry_driven,
            problem_dir=..., output_dir=..., client=..., config=...,
        )
    """
    state    = TUIState()
    reporter = TUIReporter(state)

    result_box: List[Any]       = [None]
    error_box:  List[Optional[Exception]] = [None]

    def _target() -> None:
        try:
            result_box[0] = solver_fn(reporter=reporter, **kwargs)
        except Exception as exc:       # noqa: BLE001
            error_box[0] = exc
        finally:
            state.finished = True

    t = threading.Thread(target=_target, daemon=True)
    t.start()

    console = Console()
    try:
        with Live(
            _render(state),
            console=console,
            auto_refresh=False,
            screen=False,
            transient=False,
        ) as live:
            while not state.finished:
                live.update(_render(state))
                live.refresh()
                time.sleep(0.25)
            # One final render after the solver returns
            time.sleep(0.15)
            live.update(_render(state))
            live.refresh()
    except KeyboardInterrupt:
        state.finished = True

    t.join(timeout=10)

    if error_box[0] is not None:
        raise error_box[0]
    return result_box[0]


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------

def _clip(text: str, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"
