from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from .llm import LLMClient
from .llm_types import RetryProfileOut
from .logging_utils import RunLogger
from .sorry_driven import SorryDrivenConfig, solve_problem_directory_sorry_driven
from .ui import ConsoleReporter, truncate


def solve_problem_directory_with_review_retries(
    *,
    problem_dir: Path,
    output_dir: Path,
    client: LLMClient,
    work_root: Path,
    config: SorryDrivenConfig,
    review_retries: int,
    logger: Optional[RunLogger],
    reporter: ConsoleReporter,
    solver_fn: Callable = solve_problem_directory_sorry_driven,
) -> Path:
    """Run the solver, then optionally ask a reviewer agent for local retry guidance.

    The reviewer never edits global prompts. It writes ``retry_profile`` artifacts
    and injects the accepted profile into the next attempt's ``ProofState``.
    """

    current_config = config
    destination = Path()
    for attempt in range(review_retries + 1):
        if attempt:
            reporter.title(f"Retry Attempt {attempt}")
        destination = solver_fn(
            problem_dir=problem_dir,
            output_dir=output_dir,
            client=client,
            work_root=work_root,
            config=current_config,
            logger=logger,
            reporter=reporter,
        )
        status = _read_solver_status(destination)
        if status == "proved":
            return destination
        if attempt >= review_retries:
            return destination

        reporter.step("Reviewing failed run for task-specific retry guidance")
        profile = review_failed_run(
            client=client,
            problem_dir=problem_dir,
            output_dir=destination,
            logger=logger,
            reporter=reporter,
            attempt=attempt + 1,
        )
        if not profile or not profile.should_retry:
            reporter.warning("Reviewer did not recommend another retry")
            return destination

        current_config = apply_retry_profile(current_config, profile)
        reporter.info(f"Reviewer failure stage: {profile.failure_stage}")
        reporter.info(f"Reviewer reason: {truncate(profile.failure_reason, 500)}")
        if profile.preferred_design:
            reporter.info(f"Preferred design: {profile.preferred_design}")
        if profile.forbidden_designs:
            reporter.info(f"Avoid designs: {', '.join(profile.forbidden_designs)}")
        if profile.extra_retrieval_queries:
            reporter.info(f"Extra retrieval queries: {', '.join(profile.extra_retrieval_queries)}")

    return destination


def review_failed_run(
    *,
    client: LLMClient,
    problem_dir: Path,
    output_dir: Path,
    logger: Optional[RunLogger],
    reporter: ConsoleReporter,
    attempt: int,
) -> Optional[RetryProfileOut]:
    context = build_failure_review_context(problem_dir=problem_dir, output_dir=output_dir, logger=logger)
    profile = client.review_failure(context)
    if logger:
        logger.llm_interaction(
            f"failure_review_{attempt}",
            {
                "mode": getattr(client, "last_mode", "unknown"),
                "error": getattr(client, "last_error", ""),
                "request_messages": getattr(client, "last_request_messages", []),
                "response_text": getattr(client, "last_response_text", ""),
            },
        )
    if not profile:
        reporter.warning(getattr(client, "last_error", "Reviewer returned no profile."))
        return None
    write_retry_profile(profile, output_dir, logger, attempt)
    reporter.path("Retry profile", output_dir / f"retry_profile_{attempt}.json")
    return profile


def apply_retry_profile(config: SorryDrivenConfig, profile: RetryProfileOut) -> SorryDrivenConfig:
    retry_config = profile.retry_config
    guidance = retry_profile_guidance(profile)
    extra_queries = list(dict.fromkeys([*profile.extra_retrieval_queries, *config.extra_retrieval_queries]))
    return replace(
        config,
        beam_width=retry_config.beam_width or config.beam_width,
        max_rounds=retry_config.max_rounds or config.max_rounds,
        max_depth=retry_config.max_depth if retry_config.max_depth is not None else config.max_depth,
        max_split_retries=retry_config.max_split_retries or config.max_split_retries,
        task_specific_guidance="\n\n".join(
            item for item in [config.task_specific_guidance.strip(), guidance] if item
        ),
        extra_retrieval_queries=extra_queries,
    )


def retry_profile_guidance(profile: RetryProfileOut) -> str:
    lines = [
        f"Failure stage: {profile.failure_stage}",
        f"Failure reason: {profile.failure_reason}",
    ]
    if profile.task_specific_prompt.strip():
        lines.extend(["", "Task-specific instruction:", profile.task_specific_prompt.strip()])
    if profile.preferred_design.strip():
        lines.extend(["", f"Preferred design: {profile.preferred_design.strip()}"])
    if profile.forbidden_designs:
        lines.extend(["", "Avoid these designs: " + ", ".join(profile.forbidden_designs)])
    if profile.extra_retrieval_queries:
        lines.extend(["", "Extra retrieval queries: " + ", ".join(profile.extra_retrieval_queries)])
    return "\n".join(lines).strip()


def build_failure_review_context(problem_dir: Path, output_dir: Path, logger: Optional[RunLogger]) -> str:
    parts: list[str] = []
    for label, path in [
        ("problem.md", Path(problem_dir) / "problem.md"),
        ("prove.lean", Path(problem_dir) / "prove.lean"),
        ("sorry_tree.md", Path(output_dir) / "sorry_tree.md"),
        ("sorry_tree.json", Path(output_dir) / "sorry_tree.json"),
        ("llm_trace.md", logger.llm_markdown_path if logger else None),
        ("trace.jsonl", logger.trace_path if logger else None),
    ]:
        if path and path.exists():
            parts.append(_file_block(label, path, max_chars=12000))

    for path in sorted((Path(output_dir) / "sorry_nodes").glob("*/splits/*.diagnostics.txt"))[-8:]:
        parts.append(_file_block(str(path.relative_to(output_dir)), path, max_chars=4000))
    for path in sorted((Path(output_dir) / "sorry_nodes").glob("*/attempts/*.diagnostics.txt"))[-8:]:
        parts.append(_file_block(str(path.relative_to(output_dir)), path, max_chars=3000))
    return "\n\n".join(parts)


def write_retry_profile(profile: RetryProfileOut, output_dir: Path, logger: Optional[RunLogger], attempt: int) -> None:
    json_text = profile.model_dump_json(indent=2)
    markdown = _retry_profile_markdown(profile)
    output_dir.joinpath(f"retry_profile_{attempt}.json").write_text(json_text, encoding="utf-8")
    output_dir.joinpath(f"retry_profile_{attempt}.md").write_text(markdown, encoding="utf-8")
    if logger:
        logger.run_dir.joinpath(f"retry_profile_{attempt}.json").write_text(json_text, encoding="utf-8")
        logger.run_dir.joinpath(f"retry_profile_{attempt}.md").write_text(markdown, encoding="utf-8")
        logger.event("retry_profile_created", profile.model_dump(mode="json") | {"attempt": attempt})


def _read_solver_status(output_dir: Path) -> str:
    path = Path(output_dir) / "sorry_tree.json"
    if not path.exists():
        return "unknown"
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("status", "unknown"))
    except json.JSONDecodeError:
        return "unknown"


def _file_block(label: str, path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    return f"## {label}\nPath: {path}\n\n{text.strip()}"


def _retry_profile_markdown(profile: RetryProfileOut) -> str:
    lines = [
        "# Retry Profile",
        "",
        f"- Should retry: `{profile.should_retry}`",
        f"- Failure stage: `{profile.failure_stage}`",
        f"- Failure reason: {profile.failure_reason}",
    ]
    if profile.preferred_design:
        lines.append(f"- Preferred design: `{profile.preferred_design}`")
    if profile.forbidden_designs:
        lines.append(f"- Forbidden designs: {', '.join(profile.forbidden_designs)}")
    if profile.extra_retrieval_queries:
        lines.append(f"- Extra retrieval queries: {', '.join(profile.extra_retrieval_queries)}")
    if profile.task_specific_prompt:
        lines.extend(["", "## Task-Specific Prompt", "", profile.task_specific_prompt.strip()])
    return "\n".join(lines).rstrip() + "\n"
