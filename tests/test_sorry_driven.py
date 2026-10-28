from pathlib import Path
import threading
import time

import pytest

from agent.llm import HeuristicLLMClient, LLMClient, SorrySkeletonOut, SorrySublemmaOut
from agent.lean_runner import LeanRunner
from agent.models import Candidate, LeanResult, ProofState, RejectedDesign
from agent.sorry_driven import SorryDrivenConfig, solve_problem_directory_sorry_driven
from agent.sorry_driven.core import SorryDrivenProver
from agent.sorry_driven.types import SorryNode
from agent.ui import ConsoleReporter


@pytest.mark.integration
def test_sorry_driven_solver_directly_proves_simple_task(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task_dir.joinpath("problem.md").write_text("# Simple\n", encoding="utf-8")
    task_dir.joinpath("prove.lean").write_text("import Mathlib\n\nexample : 1 = 1 := by\nsorry\n", encoding="utf-8")

    output = solve_problem_directory_sorry_driven(
        problem_dir=task_dir,
        output_dir=tmp_path / "output",
        client=HeuristicLLMClient(),
        work_root=tmp_path / "work",
        config=SorryDrivenConfig(timeout_seconds=30, max_iterations=5),
    )

    assert output.joinpath("sorry_tree.md").exists()
    assert "Status: `proved`" in output.joinpath("sorry_tree.md").read_text(encoding="utf-8")
    assert output.joinpath("sorry_nodes", "root", "proof.lean").exists()


class SplitOnlyClient(LLMClient):
    def __init__(self) -> None:
        self.last_request_messages = []
        self.last_response_text = ""
        self.last_candidate_count = 0
        self.last_mode = "test"
        self.last_error = ""

    def propose(self, state: ProofState, beam_width: int):
        self.last_response_text = "No direct proof."
        self.last_candidate_count = 0
        self.last_mode = "test_no_candidates"
        self.last_error = ""
        return []

    def design_proofs(self, state: ProofState, max_designs: int = 3):
        return None

    def split_with_sorries(self, state: ProofState, max_subgoals: int = 5):
        result = SorrySkeletonOut(
            setup_code="def helperValue : Nat := 1",
            parent_proof_code="by\n  exact child_false",
            sublemmas=[
                SorrySublemmaOut(
                    name="child_false",
                    claim="A deliberately impossible child used to test skeleton scheduling.",
                    lean_statement="lemma child_false : False := by\n  sorry",
                )
            ],
            rationale="If the child lemma is assumed, the parent closes.",
        )
        self.last_response_text = result.model_dump_json()
        self.last_mode = "test_split"
        self.last_error = ""
        return result


@pytest.mark.integration
def test_sorry_driven_accepts_typechecking_skeleton_and_creates_child(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task_dir.joinpath("problem.md").write_text("# Split\n", encoding="utf-8")
    task_dir.joinpath("prove.lean").write_text("import Mathlib\n\nexample : False := by\nsorry\n", encoding="utf-8")

    output = solve_problem_directory_sorry_driven(
        problem_dir=task_dir,
        output_dir=tmp_path / "output",
        client=SplitOnlyClient(),
        work_root=tmp_path / "work",
        config=SorryDrivenConfig(timeout_seconds=30, max_iterations=2, max_depth=1, max_rounds=1),
    )

    assert output.joinpath("sorry_nodes", "root", "skeleton.lean").exists()
    assert output.joinpath("sorry_nodes", "sg1", "statement.lean").exists()
    assert output.joinpath("sorry_nodes", "sg1", "prove.lean").exists()
    parts_dir = output / "proof_parts" / "root"
    assert parts_dir.joinpath("parent_assembly.lean").exists()
    child_files = list(parts_dir.glob("child_01_*.lean"))
    assert child_files
    assert "def helperValue" in child_files[0].read_text(encoding="utf-8")
    assert "lemma child_false" in child_files[0].read_text(encoding="utf-8")
    report = output.joinpath("sorry_tree.md").read_text(encoding="utf-8")
    assert "root" in report
    assert "sg1" in report


class ParentSorrySplitClient(SplitOnlyClient):
    def split_with_sorries(self, state: ProofState, max_subgoals: int = 5):
        result = SorrySkeletonOut(
            parent_proof_code="by\n  sorry",
            sublemmas=[
                SorrySublemmaOut(
                    name="child_true",
                    claim="A child lemma is allowed to remain sorry.",
                    lean_statement="lemma child_true : True := by\n  sorry",
                )
            ],
            rationale="Invalid split because the parent still uses sorry.",
        )
        self.last_response_text = result.model_dump_json()
        self.last_mode = "test_split"
        self.last_error = ""
        return result


@pytest.mark.integration
def test_sorry_driven_rejects_skeleton_when_parent_proof_has_sorry(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task_dir.joinpath("problem.md").write_text("# Split\n", encoding="utf-8")
    task_dir.joinpath("prove.lean").write_text("import Mathlib\n\nexample : False := by\nsorry\n", encoding="utf-8")

    output = solve_problem_directory_sorry_driven(
        problem_dir=task_dir,
        output_dir=tmp_path / "output",
        client=ParentSorrySplitClient(),
        work_root=tmp_path / "work",
        config=SorryDrivenConfig(timeout_seconds=30, max_iterations=1, max_depth=1, max_rounds=1, max_split_retries=1),
    )

    assert not output.joinpath("sorry_nodes", "sg1").exists()
    report = output.joinpath("sorry_tree.md").read_text(encoding="utf-8")
    assert "parent_proof_code must be sorry-free" in report


class BadSetupSplitClient(SplitOnlyClient):
    def split_with_sorries(self, state: ProofState, max_subgoals: int = 5):
        result = SorrySkeletonOut(
            setup_code="def bad : True := by\n  sorry",
            parent_proof_code="by\n  exact child_false",
            sublemmas=[
                SorrySublemmaOut(
                    name="child_false",
                    claim="A child lemma is allowed to remain sorry.",
                    lean_statement="lemma child_false : False := by\n  sorry",
                )
            ],
            rationale="Invalid split because setup_code contains sorry.",
        )
        self.last_response_text = result.model_dump_json()
        self.last_mode = "test_split"
        self.last_error = ""
        return result


@pytest.mark.integration
def test_sorry_driven_rejects_setup_code_with_sorry_before_lean(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    task_dir.joinpath("problem.md").write_text("# Split\n", encoding="utf-8")
    task_dir.joinpath("prove.lean").write_text("import Mathlib\n\nexample : False := by\nsorry\n", encoding="utf-8")

    output = solve_problem_directory_sorry_driven(
        problem_dir=task_dir,
        output_dir=tmp_path / "output",
        client=BadSetupSplitClient(),
        work_root=tmp_path / "work",
        config=SorryDrivenConfig(timeout_seconds=30, max_iterations=1, max_depth=1, max_rounds=1, max_split_retries=1),
    )

    report = output.joinpath("sorry_tree.md").read_text(encoding="utf-8")
    assert "setup_code contains forbidden token: sorry" in report
    rejected = output / "sorry_nodes" / "root" / "splits" / "split_01_rejected.diagnostics.txt"
    assert rejected.exists()


def test_sorry_driven_precheck_allows_lean_to_judge_omega_with_bound_context():
    from agent.sorry_driven import _validate_sorry_skeleton_fields

    skeleton = SorrySkeletonOut(
        setup_code=(
            "noncomputable def nextFin (m : ℕ) (hm : 3 ≤ m) (i : Fin m) : Fin m :=\n"
            "  let hm0 : 0 < m := by omega\n"
            "  ⟨(i.1 + 1) % m, Nat.mod_lt _ hm0⟩"
        ),
        parent_proof_code="by\n  exact child_bound",
        sublemmas=[
            SorrySublemmaOut(
                name="child_bound",
                claim="The bound 3 ≤ m holds.",
                lean_statement="lemma child_bound (m : ℕ) (hm : 3 ≤ m) : 0 < m := by\n  sorry",
            )
        ],
        rationale="The hard Lean judgment should be left to Lean.",
    )

    assert _validate_sorry_skeleton_fields(skeleton) == ""


def test_failed_attempt_summaries_preserve_plausible_construction_context():
    from agent.sorry_driven.core import _failed_attempt_summaries

    state = ProofState(theorem="example : True := by\n  sorry").with_attempt(
        Candidate(
            code="example : True := by\n  exact candidateConstruction",
            rationale="Use the simple construction.",
            score=0.7,
            source="test",
        ),
        LeanResult(
            ok=False,
            stdout="",
            stderr="error: unknown constant candidateConstruction",
            returncode=1,
            elapsed_seconds=0.01,
        ),
        error_kind="unknown_identifier",
    )

    summaries = _failed_attempt_summaries(state)

    assert len(summaries) == 1
    assert "First sanity-check whether this construction is mathematically plausible" in summaries[0]
    assert "candidateConstruction" in summaries[0]
    assert "unknown constant candidateConstruction" in summaries[0]


class ParallelProbeProver(SorryDrivenProver):
    def __init__(self, tmp_path: Path):
        super().__init__(
            client=SplitOnlyClient(),
            runner=LeanRunner(tmp_path / "lean_workspace"),
            config=SorryDrivenConfig(max_workers=2, max_iterations=5),
            logger=None,
            reporter=ConsoleReporter(enabled=False),
            workspace_dir=tmp_path / "workspace",
        )
        self.active = 0
        self.max_active = 0
        self.probe_lock = threading.Lock()

    def _try_direct_close(self, node: SorryNode) -> bool:
        if node.depth == 0:
            return False
        with self.probe_lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.05)
        with self.probe_lock:
            self.active -= 1
        node.status = "proved"
        self._save_node(node)
        return True

    def _try_llm_proof(self, node: SorryNode) -> bool:
        return False

    def _split_node(self, node: SorryNode) -> None:
        node.status = "in_progress"
        for index in range(2):
            child = SorryNode(
                id=f"sg{index + 1}",
                statement="example : True := by\n  sorry",
                status="sorry",
                parent_id=node.id,
                depth=node.depth + 1,
            )
            with self._state_lock:
                self.nodes.append(child)
                self.queue.append(child.id)
                node.children.append(child.id)
        self._save_node(node)


def test_sorry_driven_runs_split_children_in_parallel(tmp_path):
    prover = ParallelProbeProver(tmp_path)

    result = prover.run("example : False := by\n  sorry")

    assert result.status == "proved_conditionally"
    assert prover.max_active == 2


class AlwaysFailRunner(LeanRunner):
    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)

    def check(self, code: str, file_name: str = "Attempt.lean", allow_sorry: bool = False) -> LeanResult:
        if file_name == "TraceState.lean":
            return LeanResult(
                ok=False,
                stdout="",
                stderr="error: unsolved goals\n⊢ True",
                returncode=1,
                elapsed_seconds=0.01,
            )
        return LeanResult(
            ok=False,
            stdout="",
            stderr="error: unknown constant badConstruction\n⊢ True",
            returncode=1,
            elapsed_seconds=0.01,
        )


class AlwaysOkRunner(LeanRunner):
    def __init__(self, tmp_path: Path):
        super().__init__(tmp_path)
        self.checked = []

    def check(self, code: str, file_name: str = "Attempt.lean", allow_sorry: bool = False) -> LeanResult:
        self.checked.append((file_name, code, allow_sorry))
        return LeanResult(ok=True, stdout="", stderr="", returncode=0, elapsed_seconds=0.01)


class FailingCandidateClient(LLMClient):
    def __init__(self):
        self.last_request_messages = []
        self.last_response_text = ""
        self.last_candidate_count = 0
        self.last_mode = "test"
        self.last_error = ""
        self.last_sorry_hints = []

    def propose(self, state: ProofState, beam_width: int):
        self.last_candidate_count = 1
        self.last_response_text = "badConstruction"
        self.last_mode = "test_candidate"
        return [
            Candidate(
                code="example : True := by\n  exact badConstruction",
                rationale="Use a plausible direct construction.",
                score=0.6,
                source="test",
            )
        ]


def test_failed_direct_candidate_context_flows_into_next_split_state(tmp_path):
    node = SorryNode(id="root", statement="example : True := by\n  sorry")
    prover = SorryDrivenProver(
        client=FailingCandidateClient(),
        runner=AlwaysFailRunner(tmp_path / "lean_workspace"),
        config=SorryDrivenConfig(max_rounds=1),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )

    proved = prover._try_llm_proof(node)
    split_state = prover._prepare_state(node)

    assert not proved
    diagnostics = "\n".join(node.diagnostics)
    assert "sanity-check whether this construction is mathematically plausible" in diagnostics
    assert "badConstruction" in diagnostics
    assert split_state.diagnostics == node.diagnostics


class _ScriptedRunner(LeanRunner):
    """LeanRunner that returns scripted ok/fail by file_name suffix."""

    def __init__(self, tmp_path: Path, scripted_ok: dict[str, bool]):
        super().__init__(tmp_path)
        self.scripted_ok = scripted_ok
        self.checked = []

    def check(self, code: str, file_name: str = "Attempt.lean", allow_sorry: bool = False) -> LeanResult:
        self.checked.append((file_name, code, allow_sorry))
        ok = self.scripted_ok.get(file_name, True)
        return LeanResult(
            ok=ok,
            stdout="",
            stderr="" if ok else "error: probe rejected",
            returncode=0 if ok else 1,
            elapsed_seconds=0.01,
        )


class _ValidatingClient(LLMClient):
    def __init__(self, falsifier_payload):
        from agent.llm_types import WitnessValidationOut
        self._payload = falsifier_payload
        self.last_request_messages = []
        self.last_response_text = ""
        self.last_candidate_count = 0
        self.last_mode = "test"
        self.last_error = ""
        self.last_sorry_hints = []

    def propose(self, state, beam_width):
        return []

    def validate_skeleton_witness(self, parent_statement, preamble, setup_code, sublemma_blocks):
        return self._payload


def test_falsify_witness_rejects_when_lean_accepts_falsifier(tmp_path):
    from agent.llm_types import FalsifiedSublemma, WitnessValidationOut

    payload = WitnessValidationOut(
        falsified=[
            FalsifiedSublemma(
                sublemma_name="bad_sublemma",
                falsifier_lean_code=(
                    "example (h : (0 : Nat) = 1) : False := by\n  exact Nat.zero_ne_one h"
                ),
                rationale="Witness implies 0 = 1.",
            )
        ]
    )
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={
        "root_falsify_01_bad_sublemma.lean": True,
    })
    prover = SorryDrivenProver(
        client=_ValidatingClient(payload),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(
        id="root",
        statement="theorem parent_goal : False := by\n  sorry",
        preamble="def witness : Nat := 0",
    )
    skeleton = SorrySkeletonOut(
        setup_code="",
        parent_proof_code="by\n  exact bad_sublemma",
        sublemmas=[
            SorrySublemmaOut(
                name="bad_sublemma",
                claim="A bad sublemma.",
                lean_statement="lemma bad_sublemma : (0 : Nat) = 1 := by\n  sorry",
            )
        ],
        rationale="x",
    )

    error = prover._falsify_witness(node, skeleton, prover.client, attempt=1)

    assert error.startswith("Witness validation:")
    assert "bad_sublemma" in error
    falsify_dir = (tmp_path / "workspace" / "sorry_nodes" / "root" / "splits" / "falsify_01")
    assert falsify_dir.exists()
    assert any(falsify_dir.glob("*.lean"))


def test_falsify_witness_passes_when_lean_rejects_falsifier(tmp_path):
    """If the LLM proposes a falsifier but Lean rejects it, accept the split."""
    from agent.llm_types import FalsifiedSublemma, WitnessValidationOut

    payload = WitnessValidationOut(
        falsified=[
            FalsifiedSublemma(
                sublemma_name="ok_sublemma",
                falsifier_lean_code="example (h : True) : False := by\n  exact h.elim",
                rationale="LLM was wrong.",
            )
        ]
    )
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={"root_falsify_01_ok_sublemma.lean": False},
    )
    prover = SorryDrivenProver(
        client=_ValidatingClient(payload),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(
        id="root",
        statement="theorem parent_goal : True := by\n  sorry",
        preamble="def witness : Nat := 0",
    )
    skeleton = SorrySkeletonOut(
        setup_code="",
        parent_proof_code="by\n  exact ok_sublemma",
        sublemmas=[
            SorrySublemmaOut(
                name="ok_sublemma",
                claim="An OK sublemma the LLM mislabeled.",
                lean_statement="lemma ok_sublemma : True := by\n  sorry",
            )
        ],
        rationale="x",
    )

    error = prover._falsify_witness(node, skeleton, prover.client, attempt=1)

    assert error == ""


class _TriagingClient(LLMClient):
    def __init__(self, triage_payload):
        self._triage = triage_payload
        self.triage_calls = 0
        self.last_request_messages = []
        self.last_response_text = ""
        self.last_candidate_count = 0
        self.last_mode = "test"
        self.last_error = ""
        self.last_sorry_hints = []

    def propose(self, state, beam_width):
        return []

    def triage_problem(self, theorem: str, retrieved_context: str = ""):
        self.triage_calls += 1
        return self._triage


def test_legacy_triage_abandon_becomes_research_plan_and_continues(tmp_path):
    from agent.llm_types import TriageOut

    triage = TriageOut(
        difficulty="research_level",
        required_external_theorems=["Some Hadamard construction"],
        likely_in_mathlib=[False],
        recommended_strategy="abandon",
        recommended_mode="construct_witness",
        blocked_on="construction",
        research_plan=["Find an explicit construction and test off-diagonal orthogonality."],
        rationale="needs explicit construction",
    )
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_TriagingClient(triage), runner=runner,
        config=SorryDrivenConfig(enable_triage=True, triage_abandons=True, max_workers=1),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )

    abandoned = prover._triage_says_abandon(SorryNode(id="root", statement="x"))

    assert abandoned is False
    assert prover.client.triage_calls == 1
    assert (tmp_path / "workspace" / "triage.json").exists()
    assert "construction" in prover.research_memory["blocked_on"]
    assert any("explicit construction" in item for item in prover.research_memory["research_plan"])
    assert any("Legacy triage-abandon" in item for item in prover.research_memory["notes"])


def test_triage_is_advisory_by_default(tmp_path):
    from agent.llm_types import TriageOut

    triage = TriageOut(
        difficulty="research_level",
        required_external_theorems=["Some missing theorem"],
        likely_in_mathlib=[False],
        recommended_strategy="abandon",
        blocked_on="library_theorem",
        rationale="probably out of scope",
    )
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_TriagingClient(triage), runner=runner,
        config=SorryDrivenConfig(enable_triage=True, max_workers=1),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )

    abandoned = prover._triage_says_abandon(SorryNode(id="root", statement="x"))

    assert abandoned is False
    assert (tmp_path / "workspace" / "triage.json").exists()
    assert "library_theorem" in prover.research_memory["blocked_on"]


def test_triage_does_not_abandon_when_strategy_is_split(tmp_path):
    """LLM may flag research_level but recommend split — still proceed."""
    from agent.llm_types import TriageOut

    triage = TriageOut(
        difficulty="research_level",
        required_external_theorems=["X"],
        likely_in_mathlib=[False],
        recommended_strategy="split",
        rationale="hard but worth attempting",
    )
    client = _TriagingClient(triage)
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=client, runner=runner,
        config=SorryDrivenConfig(enable_triage=True, max_workers=1),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )

    abandoned = prover._triage_says_abandon(SorryNode(id="root", statement="x"))

    assert abandoned is False
    assert (tmp_path / "workspace" / "triage.json").exists()


def test_triage_skipped_when_disabled(tmp_path):
    from agent.llm_types import TriageOut

    class _AssertNoCallClient(_TriagingClient):
        def triage_problem(self, *args, **kwargs):
            raise AssertionError("triage must not be called when enable_triage=False")

    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_AssertNoCallClient(triage_payload=None), runner=runner,
        config=SorryDrivenConfig(enable_triage=False, max_workers=1),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )

    assert prover._triage_says_abandon(SorryNode(id="root", statement="x")) is False


class _ParentValidatingClient(LLMClient):
    def __init__(self, payload):
        self._payload = payload
        self.calls = 0
        self.last_request_messages = []
        self.last_response_text = ""
        self.last_candidate_count = 0
        self.last_mode = "test"
        self.last_error = ""
        self.last_sorry_hints = []

    def propose(self, state, beam_width):
        return []

    def validate_parent_witness(self, parent_statement, preamble, setup_code):
        self.calls += 1
        return self._payload


def _make_skeleton(setup_code: str = "noncomputable def H94 : Nat := 0") -> SorrySkeletonOut:
    return SorrySkeletonOut(
        setup_code=setup_code,
        parent_proof_code="by\n  exact c",
        sublemmas=[
            SorrySublemmaOut(
                name="c", claim="c", lean_statement="lemma c : True := by\n  sorry"
            )
        ],
        rationale="x",
    )


def test_falsify_parent_witness_rejects_when_lean_accepts_falsifier(tmp_path):
    from agent.llm_types import ConjunctCheck, ParentWitnessFalsificationOut

    payload = ParentWitnessFalsificationOut(
        conjunct_checks=[
            ConjunctCheck(conjunct="P x", verdict="holds", reasoning="trivially"),
            ConjunctCheck(conjunct="(0 : Nat) = 1", verdict="fails", reasoning="numerals differ"),
        ],
        falsifier_lean_code="example (h : (0 : Nat) = 1) : False := by exact Nat.zero_ne_one h",
        failing_conjunct="H94 * H94* = 94 • I",
        rationale="off-diagonal mismatch",
    )
    client = _ParentValidatingClient(payload)
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={"root_falsify_parent_01.lean": True},
    )
    prover = SorryDrivenProver(
        client=client, runner=runner,
        config=SorryDrivenConfig(),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="theorem foo : ∃ x, P x := by sorry")
    skeleton = _make_skeleton()

    error = prover._falsify_parent_witness(node, skeleton, prover.client, attempt=1)

    assert "Parent-witness validation" in error
    assert "off-diagonal mismatch" in error
    falsify_dir = tmp_path / "workspace" / "sorry_nodes" / "root" / "splits" / "falsify_parent_01"
    assert falsify_dir.joinpath("probe.lean").exists()


def test_falsify_parent_witness_passes_when_lean_rejects_falsifier(tmp_path):
    from agent.llm_types import ConjunctCheck, ParentWitnessFalsificationOut

    payload = ParentWitnessFalsificationOut(
        conjunct_checks=[
            ConjunctCheck(conjunct="True", verdict="fails", reasoning="LLM was wrong"),
        ],
        falsifier_lean_code="example (h : True) : False := by exact h.elim",
        rationale="LLM was wrong",
    )
    client = _ParentValidatingClient(payload)
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={"root_falsify_parent_01.lean": False},
    )
    prover = SorryDrivenProver(
        client=client, runner=runner,
        config=SorryDrivenConfig(),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="example : True := by sorry")
    skeleton = _make_skeleton()

    assert prover._falsify_parent_witness(node, skeleton, prover.client, attempt=1) == ""


def test_falsify_parent_witness_records_soft_warning_when_lean_cannot_confirm(tmp_path):
    """Even when Lean rejects the LLM's falsifier, surface 'fails' verdicts as soft hints."""
    from agent.llm_types import ConjunctCheck, ParentWitnessFalsificationOut

    payload = ParentWitnessFalsificationOut(
        conjunct_checks=[
            ConjunctCheck(conjunct="‖H i j‖ = 1", verdict="holds", reasoning="every entry is 1"),
            ConjunctCheck(
                conjunct="H * H* = 94 • I",
                verdict="fails",
                reasoning="off-diagonal of H * H* is 94 but RHS is 0",
            ),
        ],
        falsifier_lean_code="example (h : True) : False := by exact h.elim",
        rationale="suspected matrix-product mismatch",
    )
    client = _ParentValidatingClient(payload)
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={"root_falsify_parent_01.lean": False},  # Lean rejects probe
    )
    prover = SorryDrivenProver(
        client=client, runner=runner,
        config=SorryDrivenConfig(),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="theorem foo : ∃ x, P x := by sorry")
    skeleton = _make_skeleton()

    error = prover._falsify_parent_witness(node, skeleton, prover.client, attempt=1)

    assert error == "", "soft warning must NOT reject the witness"
    assert any(
        "soft warning" in d.lower() and "H * H* = 94 • I" in d
        for d in node.diagnostics
    ), f"expected soft warning in diagnostics, got: {node.diagnostics}"
    assess_path = (
        tmp_path / "workspace" / "sorry_nodes" / "root" / "splits"
        / "falsify_parent_01" / "conjunct_checks.json"
    )
    assert assess_path.exists()


def test_falsify_parent_witness_skips_when_no_witness(tmp_path):
    class _NoCallClient(_ParentValidatingClient):
        def validate_parent_witness(self, *args, **kwargs):
            raise AssertionError("should not be called when no witness in scope")

    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_NoCallClient(payload=None), runner=runner,
        config=SorryDrivenConfig(),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="example : True := by sorry")
    skeleton = SorrySkeletonOut(
        setup_code="",
        parent_proof_code="by exact c",
        sublemmas=[SorrySublemmaOut(name="c", claim="c", lean_statement="lemma c : True := by sorry")],
        rationale="x",
    )

    assert prover._falsify_parent_witness(node, skeleton, prover.client, attempt=1) == ""
    assert runner.checked == []


def test_falsify_parent_witness_caches_blocklist_to_skip_repeat_llm_calls(tmp_path):
    from agent.llm_types import ConjunctCheck, ParentWitnessFalsificationOut

    payload = ParentWitnessFalsificationOut(
        conjunct_checks=[
            ConjunctCheck(conjunct="(0 : Nat) = 1", verdict="fails", reasoning="numerals differ"),
        ],
        falsifier_lean_code="example (h : (0 : Nat) = 1) : False := by exact Nat.zero_ne_one h",
        rationale="bad witness",
    )
    client = _ParentValidatingClient(payload)
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={
            "root_falsify_parent_01.lean": True,
            "root_falsify_parent_02.lean": True,
        },
    )
    prover = SorryDrivenProver(
        client=client, runner=runner,
        config=SorryDrivenConfig(),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="theorem foo : ∃ x, P x := by sorry")
    skeleton = _make_skeleton()

    err1 = prover._falsify_parent_witness(node, skeleton, prover.client, attempt=1)
    err2 = prover._falsify_parent_witness(node, skeleton, prover.client, attempt=2)

    assert err1 and err2 and err1 == err2
    assert client.calls == 1, "second identical witness must hit the cache, no second LLM call"
    # Lean should only be invoked once for the actual probe; the second attempt is short-circuited.
    assert sum(1 for fn, *_ in runner.checked if fn.startswith("root_falsify_parent_")) == 1


def test_backtracking_retires_child_subtree_and_requeues_witness_ancestor(tmp_path):
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_ParentValidatingClient(payload=None),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    root = SorryNode(
        id="root",
        statement="theorem parent : True := by sorry",
        status="in_progress",
        skeleton_code=(
            "noncomputable def H94 : Matrix (Fin 94) (Fin 94) ℂ := Matrix.of fun _ _ => (1 : ℂ)\n\n"
            "lemma child : True := by sorry\n\n"
            "theorem parent : True := by exact child\n"
        ),
        children=["sg3"],
    )
    sg3 = SorryNode(
        id="sg3",
        statement="lemma gram : True := by sorry",
        status="in_progress",
        parent_id="root",
        depth=1,
        children=["sg3_3"],
        preamble="noncomputable def H94 : Matrix (Fin 94) (Fin 94) ℂ := Matrix.of fun _ _ => (1 : ℂ)",
    )
    sg3_3 = SorryNode(
        id="sg3_3",
        statement="lemma offdiag : False := by sorry",
        status="sorry",
        parent_id="sg3",
        depth=2,
        preamble=sg3.preamble,
    )
    prover.nodes.extend([root, sg3, sg3_3])
    skeleton = SorrySkeletonOut(
        setup_code="open Classical",
        parent_proof_code="by exact c",
        sublemmas=[SorrySublemmaOut(name="c", claim="c", lean_statement="lemma c : True := by sorry")],
        rationale="x",
    )

    did_backtrack = prover._backtrack_from_falsified_child(
        sg3_3,
        "Lean confirmed the inherited H94 all-ones witness makes offdiag false.",
        attempt=1,
        skeleton=skeleton,
    )

    assert did_backtrack is True
    assert root.status == "sorry"
    assert root.children == []
    assert root.skeleton_code is None
    assert root.rejected_designs
    assert "H94" in root.rejected_designs[0].witness_summary
    assert sg3.status == "backtracked"
    assert sg3_3.status == "backtracked"
    assert prover.queue == ["root"]


def test_replacement_split_does_not_reuse_retired_child_ids(tmp_path):
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_ParentValidatingClient(payload=None),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    root = SorryNode(
        id="root",
        statement="theorem parent : True := by sorry",
        status="sorry",
    )
    retired_child = SorryNode(
        id="sg1",
        statement="lemma old_child : True := by sorry",
        status="backtracked",
        parent_id="root",
        depth=1,
    )
    prover.nodes.extend([root, retired_child])
    prover._retired_node_ids.add("sg1")

    skeleton = SorrySkeletonOut(
        setup_code="",
        parent_proof_code="by\n  exact new_child",
        sublemmas=[
            SorrySublemmaOut(
                name="new_child",
                claim="new child",
                lean_statement="lemma new_child : True := by sorry",
            )
        ],
        rationale="replacement split",
    )

    prover._accept_split(root, skeleton, "lemma new_child : True := by sorry\n\ntheorem parent : True := by exact new_child\n")

    assert root.children == ["sg1_b2"]
    assert "sg1_b2" not in prover._retired_node_ids
    assert prover._node("sg1_b2").status == "sorry"


def test_rejected_designs_render_into_split_prompt():
    from agent.llm_prompts import sorry_split_messages_for_state, rejected_designs_text
    from agent.models import RejectedDesign

    state = ProofState(theorem="example : False := by sorry")
    state.rejected_designs = [
        RejectedDesign(
            attempt=1,
            failure_reason="parent-witness validator confirmed",
            witness_summary="noncomputable def H94 := all-1 matrix",
            failing_conjunct="H94 * H94* = 94 • I",
        ),
        RejectedDesign(
            attempt=2,
            failure_reason="typecheck failed at parent assembly",
            witness_summary="def alt := variant",
        ),
    ]

    rendered = rejected_designs_text(state)
    assert "Attempt 1" in rendered and "Attempt 2" in rendered
    assert "all-1 matrix" in rendered
    assert "94 • I" in rendered

    messages = sorry_split_messages_for_state(state, max_subgoals=3)
    prompt = "\n".join(content for _, content in messages)
    assert "Previously rejected designs" in prompt
    assert "all-1 matrix" in prompt


def test_record_rejected_design_appends_to_node_and_state(tmp_path):
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})
    prover = SorryDrivenProver(
        client=_ValidatingClient(falsifier_payload=None), runner=runner,
        config=SorryDrivenConfig(),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="example : True := by sorry")
    state = ProofState(theorem="example : True := by sorry")
    skeleton = SorrySkeletonOut(
        setup_code="noncomputable def w : Nat := 0",
        parent_proof_code="by exact c",
        sublemmas=[SorrySublemmaOut(name="c", claim="c", lean_statement="lemma c : True := by sorry")],
        rationale="x",
    )

    prover._record_rejected_design(
        node, state, attempt=1, skeleton=skeleton,
        failure_reason="test reason",
        failing_conjunct="P x",
    )

    assert len(node.rejected_designs) == 1
    assert len(state.rejected_designs) == 1
    entry = node.rejected_designs[0]
    assert entry.attempt == 1
    assert entry.failure_reason == "test reason"
    assert entry.failing_conjunct == "P x"
    assert "noncomputable def w" in entry.witness_summary


def test_proof_state_preserves_rejected_designs_across_attempts():
    state = ProofState(theorem="example : True := by sorry")
    state.rejected_designs.append(
        RejectedDesign(
            attempt=1,
            failure_reason="bad witness",
            witness_summary="def w := 0",
        )
    )

    next_state = state.with_attempt(
        Candidate(code="by trivial", score=1.0),
        LeanResult(ok=False, stdout="", stderr="error", returncode=1, elapsed_seconds=0.01),
        error_kind="type_error",
    )

    assert len(next_state.rejected_designs) == 1
    assert next_state.to_json()["rejected_designs"][0]["failure_reason"] == "bad witness"


def test_parallel_direct_close_returns_first_ok_and_reports_each_attempt(tmp_path):
    """All scripted_ok tactics succeed in parallel; node is proved by one of them."""
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={},  # default True for everything
    )
    prover = SorryDrivenProver(
        client=HeuristicLLMClient(), runner=runner,
        config=SorryDrivenConfig(max_workers=4),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="example : 1 = 1 := by sorry")

    proved = prover._try_direct_close(node)

    assert proved is True
    assert node.status == "proved"
    assert node.proof_code is not None
    # The runner was invoked for at least one direct_* file in parallel; the loop
    # exits as soon as one succeeds, so we don't require ALL to be checked.
    direct_calls = [fn for fn, *_ in runner.checked if "_direct_" in fn]
    assert len(direct_calls) >= 1


def test_parallel_direct_close_returns_false_when_all_fail(tmp_path):
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={},  # all default True — but we override below
    )
    runner.scripted_ok = {}  # default False for any unmapped name
    # Override _ScriptedRunner.check to return False unconditionally for direct probes.
    original_check = runner.check

    def _check(code, file_name="Attempt.lean", allow_sorry=False):
        if "_direct_" in file_name:
            runner.checked.append((file_name, code, allow_sorry))
            return LeanResult(ok=False, stdout="", stderr="error", returncode=1, elapsed_seconds=0.01)
        return original_check(code, file_name, allow_sorry)

    runner.check = _check

    prover = SorryDrivenProver(
        client=HeuristicLLMClient(), runner=runner,
        config=SorryDrivenConfig(max_workers=4),
        logger=None, reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="example : 1 = 2 := by sorry")

    proved = prover._try_direct_close(node)

    assert proved is False
    assert node.status == "sorry"


def test_existential_node_skips_direct_close_tactics():
    from agent.sorry_driven.tactics import _tactics_for_node

    node = SorryNode(
        id="root",
        statement=(
            "theorem foo : ∃ H : Matrix (Fin 94) (Fin 94) ℂ, "
            "(∀ i j, ‖H i j‖ = 1) := by sorry"
        ),
    )
    assert _tactics_for_node(node) == []


def test_existential_conjunction_skips_direct_close_tactics():
    from agent.sorry_driven.tactics import _tactics_for_node

    node = SorryNode(
        id="root",
        statement="theorem foo : ∃ x, P x ∧ Q x := by sorry",
    )
    assert _tactics_for_node(node) == []


def test_falsify_witness_skips_when_no_witness_in_scope(tmp_path):
    """No setup_code and no preamble → nothing to validate; skip the LLM call."""
    runner = _ScriptedRunner(tmp_path / "lean_workspace", scripted_ok={})

    class _ShouldNotBeCalledClient(_ValidatingClient):
        def validate_skeleton_witness(self, *args, **kwargs):
            raise AssertionError("validate_skeleton_witness should not be called when no witness in scope")

    prover = SorryDrivenProver(
        client=_ShouldNotBeCalledClient(falsifier_payload=None),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="example : True := by\n  sorry")
    skeleton = SorrySkeletonOut(
        setup_code="",
        parent_proof_code="by\n  exact c",
        sublemmas=[SorrySublemmaOut(name="c", claim="c", lean_statement="lemma c : True := by\n  sorry")],
        rationale="x",
    )

    assert prover._falsify_witness(node, skeleton, prover.client, attempt=1) == ""
    assert runner.checked == []


def test_falsify_witness_checks_concrete_matrix_sublemma_without_setup(tmp_path):
    from agent.llm_types import FalsifiedSublemma, WitnessValidationOut

    payload = WitnessValidationOut(
        falsified=[
            FalsifiedSublemma(
                sublemma_name="norm_one_entries_94",
                falsifier_lean_code="example : False := by\n  contradiction",
                rationale="The identity matrix has zero off-diagonal entries.",
            )
        ]
    )
    runner = _ScriptedRunner(
        tmp_path / "lean_workspace",
        scripted_ok={"root_falsify_01_norm_one_entries_94.lean": True},
    )
    prover = SorryDrivenProver(
        client=_ValidatingClient(payload),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    node = SorryNode(id="root", statement="theorem parent_goal : True := by\n  sorry")
    skeleton = SorrySkeletonOut(
        setup_code="",
        parent_proof_code="by\n  exact ⟨norm_one_entries_94⟩",
        sublemmas=[
            SorrySublemmaOut(
                name="norm_one_entries_94",
                claim="Identity matrix entries have norm one.",
                lean_statement=(
                    "lemma norm_one_entries_94 : ∀ i j : Fin 94, "
                    "‖(1 : Matrix (Fin 94) (Fin 94) ℂ) i j‖ = 1 := by sorry"
                ),
            )
        ],
        rationale="x",
    )

    error = prover._falsify_witness(node, skeleton, prover.client, attempt=1)

    assert error.startswith("Witness validation:")
    assert "norm_one_entries_94" in error


def test_completed_sorry_tree_merges_child_proofs_into_final_file(tmp_path):
    runner = AlwaysOkRunner(tmp_path / "lean_workspace")
    prover = SorryDrivenProver(
        client=SplitOnlyClient(),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    root = SorryNode(
        id="root",
        statement="theorem parent_goal : True := by\n  sorry",
        status="in_progress",
        skeleton_code=(
            "lemma child_goal : True := by sorry\n\n"
            "theorem parent_goal : True := by\n"
            "  exact child_goal\n"
        ),
        children=["sg1"],
    )
    child = SorryNode(
        id="sg1",
        statement="lemma child_goal : True := by sorry",
        status="proved",
        parent_id="root",
        proof_code="import Mathlib\n\nlemma child_goal : True := by\n  trivial\n",
    )
    prover.nodes.extend([root, child])

    result = prover._result()

    assert result.status == "proved"
    final = tmp_path / "workspace" / "final.lean"
    assert final.exists()
    final_code = final.read_text(encoding="utf-8")
    assert "lemma child_goal : True := by\n  trivial" in final_code
    assert "theorem parent_goal : True := by\n  exact child_goal" in final_code
    assert "sorry" not in final_code
    assert root.proof_code == final_code


def test_final_merge_keeps_child_helper_declarations(tmp_path):
    runner = AlwaysOkRunner(tmp_path / "lean_workspace")
    prover = SorryDrivenProver(
        client=SplitOnlyClient(),
        runner=runner,
        config=SorryDrivenConfig(),
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        workspace_dir=tmp_path / "workspace",
    )
    root = SorryNode(
        id="root",
        statement="theorem parent_goal : True := by\n  sorry",
        status="in_progress",
        skeleton_code=(
            "lemma child_goal : True := by sorry\n\n"
            "theorem parent_goal : True := by\n"
            "  exact child_goal\n"
        ),
        children=["sg1"],
    )
    child = SorryNode(
        id="sg1",
        statement="lemma child_goal : True := by sorry",
        status="proved",
        parent_id="root",
        proof_code=(
            "import Mathlib\n\n"
            "lemma child_helper : True := by\n"
            "  trivial\n\n"
            "lemma child_goal : True := by\n"
            "  exact child_helper\n"
        ),
    )
    prover.nodes.extend([root, child])

    result = prover._result()

    assert result.status == "proved"
    final_code = (tmp_path / "workspace" / "final.lean").read_text(encoding="utf-8")
    assert "lemma child_helper : True := by" in final_code
    assert "lemma child_goal : True := by\n  exact child_helper" in final_code
