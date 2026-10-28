from pathlib import Path

from agent.llm_types import RetryConfigOut, RetryProfileOut
from agent.retry_review import (
    apply_retry_profile,
    build_failure_review_context,
    retry_profile_guidance,
    solve_problem_directory_with_review_retries,
    write_retry_profile,
)
from agent.sorry_driven import SorryDrivenConfig
from agent.ui import ConsoleReporter


def test_apply_retry_profile_adds_task_local_guidance_and_queries():
    profile = RetryProfileOut(
        should_retry=True,
        failure_stage="parent_assembly",
        failure_reason="The parent proof used the wrong helper lemma names.",
        task_specific_prompt="Use exact declared child lemma names.",
        preferred_design="short parent assembly",
        forbidden_designs=["large algebraic rewrite"],
        extra_retrieval_queries=["Finset.sum_ite_eq"],
        retry_config=RetryConfigOut(max_rounds=2, max_split_retries=2),
    )

    config = apply_retry_profile(SorryDrivenConfig(max_rounds=3), profile)

    assert config.max_rounds == 2
    assert config.max_split_retries == 2
    assert "Use exact declared child lemma names" in config.task_specific_guidance
    assert "large algebraic rewrite" in config.task_specific_guidance
    assert config.extra_retrieval_queries == ["Finset.sum_ite_eq"]


def test_retry_profile_artifacts_and_context_are_task_local(tmp_path):
    problem_dir = tmp_path / "problem"
    output_dir = tmp_path / "output"
    problem_dir.mkdir()
    output_dir.mkdir()
    problem_dir.joinpath("problem.md").write_text("# Problem\n", encoding="utf-8")
    problem_dir.joinpath("prove.lean").write_text("example : True := by\n  sorry\n", encoding="utf-8")
    output_dir.joinpath("sorry_tree.md").write_text("# Sorry tree\nfailed\n", encoding="utf-8")

    profile = RetryProfileOut(
        should_retry=True,
        failure_stage="direct_close",
        failure_reason="No candidate closed the goal.",
        task_specific_prompt="Try `trivial` first.",
    )
    write_retry_profile(profile, output_dir, logger=None, attempt=1)
    context = build_failure_review_context(problem_dir=problem_dir, output_dir=output_dir, logger=None)

    assert output_dir.joinpath("retry_profile_1.json").exists()
    assert output_dir.joinpath("retry_profile_1.md").exists()
    assert "Try `trivial` first" in retry_profile_guidance(profile)
    assert "prove.lean" in context
    assert "sorry_tree.md" in context


class ReviewClient:
    last_mode = "test"
    last_error = ""
    last_request_messages = []
    last_response_text = ""

    def __init__(self):
        self.review_calls = 0

    def review_failure(self, failure_context: str):
        self.review_calls += 1
        self.last_mode = "failure_review_structured"
        self.last_response_text = "retry profile"
        return RetryProfileOut(
            should_retry=True,
            failure_stage="direct",
            failure_reason="Need one more local retry.",
            task_specific_prompt="Prefer the simple construction.",
            retry_config=RetryConfigOut(max_rounds=2),
        )


def test_review_retry_runs_solver_again_with_task_guidance(tmp_path):
    problem_dir = tmp_path / "problem"
    output_root = tmp_path / "output"
    problem_dir.mkdir()
    problem_dir.joinpath("problem.md").write_text("# Problem\n", encoding="utf-8")
    problem_dir.joinpath("prove.lean").write_text("example : True := by\n  sorry\n", encoding="utf-8")
    seen_guidance = []

    def fake_solver_fn(**kwargs):
        attempt_dir = output_root / f"attempt_{len(seen_guidance) + 1}"
        attempt_dir.mkdir(parents=True)
        seen_guidance.append(kwargs["config"].task_specific_guidance)
        status = "failed" if len(seen_guidance) == 1 else "proved"
        attempt_dir.joinpath("sorry_tree.json").write_text(f'{{"status": "{status}", "nodes": []}}', encoding="utf-8")
        attempt_dir.joinpath("sorry_tree.md").write_text(f"Status: `{status}`\n", encoding="utf-8")
        return attempt_dir

    client = ReviewClient()
    destination = solve_problem_directory_with_review_retries(
        problem_dir=problem_dir,
        output_dir=output_root,
        client=client,
        work_root=tmp_path / "work",
        config=SorryDrivenConfig(max_rounds=1),
        review_retries=1,
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        solver_fn=fake_solver_fn,
    )

    assert destination.name == "attempt_2"
    assert client.review_calls == 1
    assert seen_guidance[0] == ""
    assert "Prefer the simple construction" in seen_guidance[1]


def test_review_retry_does_not_treat_conditional_proof_as_success(tmp_path):
    problem_dir = tmp_path / "problem"
    output_root = tmp_path / "output"
    problem_dir.mkdir()
    problem_dir.joinpath("problem.md").write_text("# Problem\n", encoding="utf-8")
    problem_dir.joinpath("prove.lean").write_text("example : True := by\n  sorry\n", encoding="utf-8")
    statuses = ["proved_conditionally", "proved"]
    seen_guidance = []

    def fake_solver_fn(**kwargs):
        attempt_dir = output_root / f"attempt_{len(seen_guidance) + 1}"
        attempt_dir.mkdir(parents=True)
        seen_guidance.append(kwargs["config"].task_specific_guidance)
        status = statuses[len(seen_guidance) - 1]
        attempt_dir.joinpath("sorry_tree.json").write_text(f'{{"status": "{status}", "nodes": []}}', encoding="utf-8")
        attempt_dir.joinpath("sorry_tree.md").write_text(f"Status: `{status}`\n", encoding="utf-8")
        return attempt_dir

    client = ReviewClient()
    destination = solve_problem_directory_with_review_retries(
        problem_dir=problem_dir,
        output_dir=output_root,
        client=client,
        work_root=tmp_path / "work",
        config=SorryDrivenConfig(max_rounds=1),
        review_retries=1,
        logger=None,
        reporter=ConsoleReporter(enabled=False),
        solver_fn=fake_solver_fn,
    )

    assert destination.name == "attempt_2"
    assert client.review_calls == 1
