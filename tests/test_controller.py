from pathlib import Path

from agent.controller import SearchController
from agent.lean_runner import LeanRunner
from agent.llm import HeuristicLLMClient
from agent.logging_utils import RunLogger
from agent.models import LeanResult, ProofState
from agent.nodes import Repairer, TacticProposer


class FakeRunner(LeanRunner):
    def __init__(self):
        super().__init__(Path("."))

    def check(self, code: str, file_name: str = "Attempt.lean") -> LeanResult:
        ok = "rfl" in code
        return LeanResult(
            ok=ok,
            stdout="",
            stderr="" if ok else "error: unsolved goals\n⊢ 1 + 1 = 2",
            returncode=0 if ok else 1,
            elapsed_seconds=0.01,
        )


def test_controller_stops_on_verified_candidate():
    client = HeuristicLLMClient()
    controller = SearchController(
        runner=FakeRunner(),
        proposer=TacticProposer(client),
        repairer=Repairer(client),
        beam_width=2,
        max_rounds=1,
        logger=None,
    )
    state = controller.solve(ProofState(theorem="example : 1 = 1"))
    assert state.verified_code is not None
    assert "rfl" in state.verified_code
    assert state.error_kinds[-1] == "none"


class NoLeanClient(HeuristicLLMClient):
    def propose(self, state: ProofState, beam_width: int):
        self.last_request_messages = [{"role": "human", "content": "try"}]
        self.last_response_text = "Here is a proof idea, but not Lean code."
        self.last_candidate_count = 0
        self.last_mode = "no_usable_lean"
        self.last_error = ""
        return []


def test_controller_saves_proof_idea_when_llm_returns_no_lean(tmp_path):
    client = NoLeanClient()
    controller = SearchController(
        runner=FakeRunner(),
        proposer=TacticProposer(client),
        repairer=Repairer(client),
        beam_width=2,
        max_rounds=1,
        logger=RunLogger(root=tmp_path / "runs"),
        artifact_dir=tmp_path,
    )

    state = controller.solve(ProofState(theorem="example : True"))

    assert state.attempts == []
    idea = tmp_path / "proof_ideas" / "propose_round_1.md"
    assert idea.exists()
    assert "not Lean code" in idea.read_text(encoding="utf-8")
    llm_trace = next((tmp_path / "runs").glob("*/llm_trace.md"))
    assert "no_usable_lean" in llm_trace.read_text(encoding="utf-8")
