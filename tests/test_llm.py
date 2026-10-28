from agent.llm import LangChainProofClient, ProofCandidateOut, ProofCandidatesOut, RetryProfileOut
from agent.models import Candidate, LeanResult, ProofState


def test_tactic_completion_replaces_sorry_body():
    from agent.llm import _complete_with

    code = _complete_with("example : 1 = 1 := by\n  sorry", "rfl")
    assert code == "example : 1 = 1 := by\n  rfl\n"


def test_structured_output_schema_to_candidates():
    from agent.llm import _candidates_from_structured

    response = ProofCandidatesOut(candidates=[ProofCandidateOut(code="by\n  rfl", rationale="reflexive", score=0.9)])
    candidates, rejected, sorry_hints = _candidates_from_structured(response, "example : 1 = 1 := by\nsorry", "test")
    assert candidates[0].code == "example : 1 = 1 := by\n  rfl"
    assert candidates[0].rationale == "reflexive"
    assert rejected == []
    assert sorry_hints == []


def test_structured_output_filters_forbidden_candidates():
    from agent.llm import _candidates_from_structured

    response = ProofCandidatesOut(candidates=[ProofCandidateOut(code="by\n  sorry", score=0.9)])
    candidates, rejected, sorry_hints = _candidates_from_structured(response, "example : 1 = 1 := by\nsorry", "test")
    assert candidates == []
    assert rejected == ["candidate 1 contains forbidden Lean token"]
    # sorry_hints should capture context from the rejected candidate
    assert len(sorry_hints) == 1
    assert "Rejected candidate 1" in sorry_hints[0]


class _StructuredModel:
    def __init__(self, response):
        self.response = response
        self.raw_invoked = False

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        return self.response


class _NoRawModel(_StructuredModel):
    def invoke_raw(self, messages):
        self.raw_invoked = True
        raise AssertionError("Raw invoke must not be used for LLM output parsing.")


class _NoneStructuredModel:
    raw_invoked = False

    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        return None


class _ParsedNoneModel:
    def with_structured_output(self, schema, **kwargs):
        return self

    def invoke(self, messages):
        class Message:
            content = "plain prose, not a structured tool result"
            response_metadata = {"stop_reason": "end_turn"}

        return {"raw": Message(), "parsed": None, "parsing_error": None}


def test_propose_uses_only_structured_output():
    response = ProofCandidatesOut(candidates=[ProofCandidateOut(code="by\n  rfl", score=0.8)])
    model = _NoRawModel(response)
    client = LangChainProofClient(model, "test")

    candidates = client.propose(ProofState(theorem="example : 1 = 1 := by\n  sorry"), beam_width=1)

    assert len(candidates) == 1
    assert candidates[0].code == "example : 1 = 1 := by\n  rfl"
    assert client.last_mode == "structured"
    assert not model.raw_invoked


def test_heuristic_client_fork_has_independent_last_state():
    from agent.llm import HeuristicLLMClient

    parent = HeuristicLLMClient()
    child = parent.fork()

    assert child is not parent
    child.propose(ProofState(theorem="example : True := by\n  trivial"), beam_width=1)

    assert child.last_candidate_count == 1
    assert parent.last_candidate_count == 0


def test_propose_does_not_parse_raw_text_after_structured_failure():
    client = LangChainProofClient(_NoneStructuredModel(), "test")

    candidates = client.propose(ProofState(theorem="example : True := by\n  sorry"), beam_width=1)

    assert candidates == []
    assert client.last_mode == "no_usable_lean"
    assert "structured proof candidates returned None" in client.last_error


def test_proof_design_handles_none_structured_response():
    client = LangChainProofClient(_NoneStructuredModel(), "test")

    assert client.design_proofs(ProofState(theorem="example : True := by\n  sorry")) is None
    assert "structured proof design returned None" in client.last_error


def test_structured_output_none_records_raw_diagnostic_without_parsing():
    client = LangChainProofClient(_ParsedNoneModel(), "test")

    candidates = client.propose(ProofState(theorem="example : True := by\n  sorry"), beam_width=1)

    assert candidates == []
    assert "structured proof candidates returned None" in client.last_error
    assert "plain prose" in client.last_error


def test_sorry_direct_prompt_tells_model_to_return_empty_instead_of_sorry():
    from agent.llm import _messages_for_state

    state = ProofState(theorem="example : True := by\n  sorry")
    setattr(state, "prompt_mode", "sorry_direct")

    messages = _messages_for_state(state, beam_width=2)
    prompt = "\n".join(content for _, content in messages)

    assert "Step 1 of a sorry-driven proof agent" in prompt
    assert "return an empty candidates list" in prompt
    assert "If you cannot close the theorem now, return `candidates: []`" in prompt
    assert "sanity-check the proposed witness" in prompt
    assert "Nat.mod_lt" in prompt
    assert "placeholder tokens such as `...`" in prompt


def test_repair_prompt_preserves_recent_failed_candidate_construction():
    from agent.llm import _messages_for_state

    state = ProofState(theorem="example : True := by\n  sorry")
    setattr(state, "prompt_mode", "sorry_direct")
    state = state.with_attempt(
        Candidate(
            code="example : True := by\n  exact True.intro\n",
            rationale="Use direct constructor.",
            score=0.8,
            source="test",
        ),
        LeanResult(
            ok=False,
            stdout="",
            stderr="error: unknown constant True.intro",
            returncode=1,
            elapsed_seconds=0.01,
        ),
        goals=["⊢ True"],
        error_kind="unknown_identifier",
    )

    messages = _messages_for_state(state, beam_width=1)
    prompt = "\n".join(content for _, content in messages)

    assert "Repair context from recent failed Lean candidates" in prompt
    assert "exact True.intro" in prompt
    assert "unknown constant True.intro" in prompt
    assert "First decide whether its witness/construction is mathematically compatible" in prompt
    assert "Fix names, equality orientations, missing side conditions" in prompt


def test_sorry_split_prompt_enforces_child_sorry_parent_sorry_free_contract():
    from agent.llm import _sorry_split_messages_for_state

    messages = _sorry_split_messages_for_state(ProofState(theorem="example : False := by\n  sorry"), max_subgoals=3)
    prompt = "\n".join(content for _, content in messages)

    assert "Step 2 of a sorry-driven Lean proof agent" in prompt
    assert "Child lemmas may use `sorry`" in prompt
    assert "parent_proof_code" in prompt
    assert "parent declaration must be proved after the child lemmas with no `sorry`" in prompt
    assert "Do not create a child lemma that is merely the current theorem with a different name" in prompt
    assert "Do not state witness-specific properties for an arbitrary parameter" in prompt
    assert "noncomputable def" in prompt
    assert "Do not rely on `by omega` alone to prove goals of the form `(k % m) < m`" in prompt
    assert "There is no legacy `skeleton_code` field" in prompt


def test_sorry_split_prompt_includes_selected_proof_design():
    from agent.llm import _sorry_split_messages_for_state

    state = ProofState(theorem="example : False := by\n  sorry")
    setattr(state, "proof_design", "Selected proof design: epsilon construction")

    messages = _sorry_split_messages_for_state(state, max_subgoals=3)
    prompt = "\n".join(content for _, content in messages)

    assert "Selected proof design to implement" in prompt
    assert "epsilon construction" in prompt


def test_design_and_split_prompts_include_failed_candidate_diagnostics():
    from agent.llm import _proof_design_messages_for_state, _sorry_split_messages_for_state

    state = ProofState(
        theorem="example : False := by\n  sorry",
        diagnostics=[
            "Rejected direct proof candidate. First sanity-check whether this construction is mathematically plausible.\n"
            "Lean code:\nby\n  exact badConstruction\n"
            "Lean diagnostics:\nunknown constant badConstruction"
        ],
    )

    design_prompt = "\n".join(content for _, content in _proof_design_messages_for_state(state, max_designs=2))
    split_prompt = "\n".join(content for _, content in _sorry_split_messages_for_state(state, max_subgoals=3))

    assert "badConstruction" in design_prompt
    assert "plausible rejected direct proof candidate" in split_prompt
    assert "only after a quick mathematical sanity check" in design_prompt
    assert "badConstruction" in split_prompt


def test_sorry_prompts_include_task_specific_retry_guidance():
    from agent.llm import _messages_for_state, _sorry_split_messages_for_state

    state = ProofState(
        theorem="example : False := by\n  sorry",
        task_specific_guidance="Prefer identity kernel; avoid Metropolis.",
        extra_retrieval_queries=["Finset.sum_ite_eq"],
    )
    setattr(state, "prompt_mode", "sorry_direct")

    direct_prompt = "\n".join(content for _, content in _messages_for_state(state, beam_width=1))
    split_prompt = "\n".join(content for _, content in _sorry_split_messages_for_state(state, max_subgoals=3))

    assert "Prefer identity kernel" in direct_prompt
    assert "Finset.sum_ite_eq" in direct_prompt
    assert "Prefer identity kernel" in split_prompt


def test_failure_review_uses_structured_output_schema():
    response = RetryProfileOut(
        should_retry=True,
        failure_stage="split",
        failure_reason="The run repeatedly chose an overcomplicated construction.",
        task_specific_prompt="Try the identity construction first.",
        preferred_design="identity",
        forbidden_designs=["metropolis"],
        extra_retrieval_queries=["Finset.sum_ite_eq"],
    )
    client = LangChainProofClient(_NoRawModel(response), "test")

    profile = client.review_failure("failed run context")

    assert profile is not None
    assert profile.should_retry
    assert profile.preferred_design == "identity"
    assert client.last_mode == "failure_review_structured"


def test_validate_skeleton_witness_returns_falsified_sublemmas():
    from agent.llm_types import FalsifiedSublemma, WitnessValidationOut

    response = WitnessValidationOut(
        falsified=[
            FalsifiedSublemma(
                sublemma_name="hadamard94_offdiag_eq_one",
                falsifier_lean_code=(
                    "example (h : ∀ i j : Fin 94, i ≠ j → hadamard94 i j = (1 : ℂ)) : False := by\n"
                    "  have := h ⟨0, by decide⟩ ⟨1, by decide⟩ (by decide)\n"
                    "  simp [hadamard94] at this"
                ),
                rationale="Off-diagonal entries of the identity-style witness are 0, not 1.",
            )
        ]
    )
    client = LangChainProofClient(_NoRawModel(response), "test")

    out = client.validate_skeleton_witness(
        parent_statement="theorem foo : ... := by sorry",
        preamble="noncomputable def hadamard94 := ...",
        setup_code="",
        sublemma_blocks=["### Sublemma 1"],
    )

    assert out is not None
    assert len(out.falsified) == 1
    assert out.falsified[0].sublemma_name == "hadamard94_offdiag_eq_one"
    assert client.last_mode == "witness_validation_structured"


def test_validate_skeleton_witness_handles_none_response():
    client = LangChainProofClient(_NoneStructuredModel(), "test")

    out = client.validate_skeleton_witness(
        parent_statement="theorem foo : ... := by sorry",
        preamble="",
        setup_code="def helper := 1",
        sublemma_blocks=["### Sublemma 1"],
    )

    assert out is None
    assert "structured witness validation returned None" in client.last_error


def test_witness_validation_prompt_contains_required_fields():
    from agent.llm_prompts import witness_validation_messages

    messages = witness_validation_messages(
        parent_statement="theorem foo : ∀ i, P i := by sorry",
        preamble="def witness := 0",
        setup_code="def helper := 1",
        sublemma_blocks=[
            "### Sublemma 1: `child_one`\nClaim: child claim\n```lean\nlemma child_one : Q := by sorry\n```"
        ],
    )
    prompt = "\n".join(content for _, content in messages)

    assert "witness-validation" in prompt
    assert "example (h :" in prompt
    assert "child_one" in prompt
    assert "def witness := 0" in prompt
    assert "def helper := 1" in prompt
    assert "theorem foo" in prompt


def test_sorry_driven_prompt_is_loaded_from_file():
    from agent.llm import PROMPT_DIR, _sorry_driven_prompt

    prompt_path = PROMPT_DIR / "sorry_driven.md"
    assert prompt_path.exists()
    assert _sorry_driven_prompt() == prompt_path.read_text(encoding="utf-8").strip()
