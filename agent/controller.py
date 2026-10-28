from __future__ import annotations

from pathlib import Path
from typing import Optional

from .error_classifier import classify_lean_error
from .goal_extractor import extract_goals
from .lean_runner import LeanRunner, LeanUnavailableError
from .logging_utils import RunLogger
from .models import ProofState
from .nodes import Repairer, TacticProposer, VerifierRouter
from .ui import ConsoleReporter, truncate


class SearchController:
    def __init__(
        self,
        runner: LeanRunner,
        proposer: TacticProposer,
        repairer: Repairer,
        beam_width: int = 4,
        max_rounds: int = 3,
        logger: Optional[RunLogger] = None,
        reporter: Optional[ConsoleReporter] = None,
        artifact_dir: Optional[Path] = None,
        min_candidate_score: float = 0.0,
    ) -> None:
        self.runner = runner
        self.proposer = proposer
        self.repairer = repairer
        self.beam_width = beam_width
        self.max_rounds = max_rounds
        self.logger = logger
        self.reporter = reporter or ConsoleReporter(enabled=False)
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self.min_candidate_score = max(0.0, float(min_candidate_score))

    def solve(self, initial: ProofState) -> ProofState:
        state = initial
        self.reporter.step("Requesting initial proof candidates from LLM")
        if self.logger:
            self.logger.event(
                "search_started",
                {"beam_width": self.beam_width, "max_rounds": self.max_rounds, "theorem": state.theorem},
            )
        candidates = self.proposer.run(state, self.beam_width)
        self.reporter.info(f"Received {len(candidates)} candidate(s)")
        self._record_llm_response(round_index=0, phase="propose")
        self._record_proof_idea_if_no_candidates(round_index=0, phase="propose", candidates=candidates)
        candidates = self._filter_low_score(candidates, round_index=0, phase="propose")
        if self.logger:
            self.logger.event("candidates_proposed", {"round": 0, "count": len(candidates)})
        for round_index in range(self.max_rounds):
            if round_index > 0:
                self.reporter.step(f"Requesting repair candidates for round {round_index + 1}")
                candidates = self.repairer.run(state, self.beam_width)
                self.reporter.info(f"Received {len(candidates)} repair candidate(s)")
                self._record_llm_response(round_index=round_index, phase="repair")
                self._record_proof_idea_if_no_candidates(round_index=round_index, phase="repair", candidates=candidates)
                candidates = self._filter_low_score(candidates, round_index=round_index, phase="repair")
                if self.logger:
                    self.logger.event("candidates_proposed", {"round": round_index, "count": len(candidates)})
            if not candidates and self._should_stop_after_no_candidates(phase="propose" if round_index == 0 else "repair"):
                self.reporter.error("Stopping proof search because structured LLM output produced no usable candidates")
                break
            for candidate in candidates:
                full_code = state.header + candidate.code
                attempt_number = len(state.attempts) + 1
                self.reporter.step(
                    f"Checking candidate {attempt_number} "
                    f"(round {round_index + 1}, score={candidate.score:.2f}, source={candidate.source})"
                )
                self.reporter.detail(candidate.code)
                try:
                    result = self.runner.check(full_code)
                except LeanUnavailableError as exc:
                    state.diagnostics.append(str(exc))
                    self.reporter.error(str(exc))
                    if self.logger:
                        self.logger.event("lean_unavailable", {"message": str(exc)})
                    return state
                goals = extract_goals(result.output)
                error_kind = "none" if result.ok else classify_lean_error(result.output)
                state = state.with_attempt(candidate, result, goals, error_kind=error_kind)
                if result.ok:
                    self.reporter.success(f"Lean accepted candidate {attempt_number} in {result.elapsed_seconds:.2f}s")
                else:
                    self.reporter.warning(
                        f"Lean rejected candidate {attempt_number} in {result.elapsed_seconds:.2f}s "
                        f"(returncode={result.returncode}, error={error_kind})"
                    )
                    if result.output:
                        self.reporter.detail(truncate(result.output, 1200))
                if self.logger:
                    self.logger.event(
                        "attempt",
                        {
                            "round": round_index,
                            "candidate": candidate.__dict__,
                            "ok": result.ok,
                            "returncode": result.returncode,
                            "error_kind": error_kind,
                            "goals": goals,
                            "elapsed_seconds": result.elapsed_seconds,
                            "output": result.output,
                        },
                    )
                if VerifierRouter.is_done(state):
                    if self.logger:
                        self.logger.final(state.header + state.verified_code)
                        self.logger.event("search_finished", {"status": "verified", "attempts": len(state.attempts)})
                    self.reporter.success(f"Verified proof found after {len(state.attempts)} attempt(s)")
                    return state
        if self.logger:
            self.logger.event("search_finished", {"status": "failed", "attempts": len(state.attempts)})
        self.reporter.error(f"No verified proof found after {len(state.attempts)} attempt(s)")
        return state

    def _record_llm_response(self, round_index: int, phase: str) -> None:
        client = getattr(self.proposer if phase == "propose" else self.repairer, "client", None)
        text = getattr(client, "last_response_text", "")
        count = getattr(client, "last_candidate_count", None)
        mode = getattr(client, "last_mode", "unknown")
        error = getattr(client, "last_error", "")
        if text and self.reporter.verbose:
            self.reporter.detail(f"LLM response ({mode}, {phase}, round {round_index + 1}):\n{text[:4000]}")
        if error and self.reporter.verbose:
            self.reporter.detail(f"LLM structured output error: {error}")
        if self.logger:
            self.logger.event(
                "llm_response",
                {
                    "round": round_index,
                    "phase": phase,
                    "mode": mode,
                    "candidate_count": count,
                    "error": error,
                    "text": text,
                },
            )

    def _record_proof_idea_if_no_candidates(self, round_index: int, phase: str, candidates: list) -> None:
        if candidates:
            return
        client = getattr(self.proposer if phase == "propose" else self.repairer, "client", None)
        text = getattr(client, "last_response_text", "")
        count = getattr(client, "last_candidate_count", None)
        mode = getattr(client, "last_mode", "unknown")
        error = getattr(client, "last_error", "")
        if not text or not self.artifact_dir:
            return
        ideas_dir = self.artifact_dir / "proof_ideas"
        ideas_dir.mkdir(parents=True, exist_ok=True)
        path = ideas_dir / f"{phase}_round_{round_index + 1}.md"
        path.write_text(_proof_idea_markdown(phase, round_index, client, text), encoding="utf-8")
        self.reporter.path("Proof idea saved", path)
        if self.logger:
            self.logger.event(
                "proof_idea_saved",
                {
                    "round": round_index,
                    "phase": phase,
                    "path": str(path),
                    "mode": mode,
                },
            )
            self.logger.llm_interaction(
                f"{phase}_round_{round_index + 1}",
                {
                    "mode": mode,
                    "candidate_count": count,
                    "error": error,
                    "request_messages": getattr(client, "last_request_messages", []),
                    "response_text": text,
                },
            )

    def _filter_low_score(self, candidates: list, round_index: int, phase: str) -> list:
        if self.min_candidate_score <= 0.0 or not candidates:
            return list(candidates)
        kept, dropped = [], []
        for candidate in candidates:
            (kept if candidate.score >= self.min_candidate_score else dropped).append(candidate)
        if dropped:
            preview = ", ".join(f"score={c.score:.2f}" for c in dropped[:3])
            self.reporter.info(
                f"Dropped {len(dropped)} low-score candidate(s) (< {self.min_candidate_score:.2f}): {preview}"
            )
            if self.logger:
                self.logger.event(
                    "candidates_filtered",
                    {
                        "round": round_index,
                        "phase": phase,
                        "threshold": self.min_candidate_score,
                        "dropped": [
                            {"score": c.score, "rationale": c.rationale[:240], "source": c.source}
                            for c in dropped
                        ],
                        "kept_count": len(kept),
                    },
                )
        return kept

    def _should_stop_after_no_candidates(self, phase: str) -> bool:
        client = getattr(self.proposer if phase == "propose" else self.repairer, "client", None)
        error = getattr(client, "last_error", "")
        if not error:
            return False
        stop_markers = [
            "structured proof candidates returned None",
            "structured proof candidates parsing failed",
            "all structured candidates were rejected",
        ]
        return any(marker in error for marker in stop_markers)


def default_workspace() -> Path:
    return Path(__file__).resolve().parent.parent / "lean_workspace"


def _proof_idea_markdown(phase: str, round_index: int, client, text: str) -> str:
    return (
        "# Proof Idea\n\n"
        f"- Phase: `{phase}`\n"
        f"- Round: `{round_index + 1}`\n"
        f"- Mode: `{getattr(client, 'last_mode', 'unknown')}`\n"
        f"- Error: `{getattr(client, 'last_error', '') or 'none'}`\n\n"
        "## LLM Response\n\n"
        "```text\n"
        f"{text.strip()}\n"
        "```\n"
    )
