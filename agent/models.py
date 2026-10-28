from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class LeanResult:
    """Result returned by a Lean check."""

    ok: bool
    stdout: str
    stderr: str
    returncode: int
    elapsed_seconds: float
    timed_out: bool = False
    file_path: Optional[str] = None

    @property
    def output(self) -> str:
        return "\n".join(part for part in [self.stdout, self.stderr] if part)


@dataclass(frozen=True)
class Candidate:
    """A proposed Lean proof body or complete snippet."""

    code: str
    score: float = 0.0
    rationale: str = ""
    source: str = "heuristic"


@dataclass(frozen=True)
class RetrievalResult:
    """Knowledge retrieved from Mathlib source or theorem-search services."""

    source: str
    query: str
    name: str = ""
    file_path: Optional[str] = None
    line: Optional[int] = None
    snippet: str = ""
    content: str = ""
    score: float = 0.0
    diagnostic: str = ""


@dataclass
class RejectedDesign:
    """Structured record of a witness/skeleton previously rejected for the same node.

    Surfaced in the next split prompt so the LLM stops re-proposing close variants.
    """

    attempt: int
    failure_reason: str
    witness_summary: str = ""
    failing_conjunct: str = ""


@dataclass(frozen=True)
class ProofAnalysis:
    """Human-style reading of the theorem goal."""

    target_shape: str = "unknown"
    domain_hints: List[str] = field(default_factory=list)
    likely_methods: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class ProofState:
    """Search state for a single theorem attempt."""

    theorem: str
    header: str = "import Mathlib\n\n"
    goals: List[str] = field(default_factory=list)
    analysis: Optional[ProofAnalysis] = None
    retrieved_tools: List[str] = field(default_factory=list)
    retrieved_context: List[RetrievalResult] = field(default_factory=list)
    extra_retrieval_queries: List[str] = field(default_factory=list)
    task_specific_guidance: str = ""
    attempts: List[Candidate] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    error_kinds: List[str] = field(default_factory=list)
    # Witnesses/skeletons rejected by previous split attempts on the same node.
    # Rendered into the split prompt so the LLM does not re-propose them.
    rejected_designs: List[RejectedDesign] = field(default_factory=list)
    verified_code: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def with_attempt(
        self,
        candidate: Candidate,
        result: LeanResult,
        goals: Optional[List[str]] = None,
        error_kind: str = "none",
    ) -> "ProofState":
        next_state = ProofState(
            theorem=self.theorem,
            header=self.header,
            goals=list(self.goals if goals is None else goals),
            analysis=self.analysis,
            retrieved_tools=list(self.retrieved_tools),
            retrieved_context=list(self.retrieved_context),
            extra_retrieval_queries=list(self.extra_retrieval_queries),
            task_specific_guidance=self.task_specific_guidance,
            attempts=[*self.attempts, candidate],
            diagnostics=[*self.diagnostics, result.output],
            error_kinds=[*self.error_kinds, error_kind],
            rejected_designs=list(self.rejected_designs),
            verified_code=self.verified_code,
            created_at=self.created_at,
        )
        if result.ok:
            next_state.verified_code = candidate.code
        return next_state

    def to_json(self) -> Dict[str, Any]:
        return {
            "theorem": self.theorem,
            "header": self.header,
            "goals": self.goals,
            "analysis": self.analysis.__dict__ if self.analysis else None,
            "retrieved_tools": self.retrieved_tools,
            "retrieved_context": _dataclass_to_json(self.retrieved_context),
            "extra_retrieval_queries": self.extra_retrieval_queries,
            "task_specific_guidance": self.task_specific_guidance,
            "attempts": [candidate.__dict__ for candidate in self.attempts],
            "diagnostics": self.diagnostics,
            "error_kinds": self.error_kinds,
            "rejected_designs": _dataclass_to_json(self.rejected_designs),
            "verified_code": self.verified_code,
            "created_at": self.created_at,
        }


def _dataclass_to_json(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "__dataclass_fields__"):
        return {key: _dataclass_to_json(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, list):
        return [_dataclass_to_json(item) for item in value]
    return value
