from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cli_config import build_parser, make_config, review_retries
from .dataset import prepare_dataset
from .env import load_dotenv
from .langsmith_utils import langsmith_is_configured, langsmith_project, wait_for_langsmith_traces
from .llm import client_from_env
from .logging_utils import RunLogger
from .nodes import Formalizer
from .retry_review import solve_problem_directory_with_review_retries
from .sorry_driven import solve_problem_directory_sorry_driven
from .ui import ConsoleReporter

# Backwards-compatible names used by tests and interactive debugging.
_make_config = make_config
_review_retries = review_retries


def main(argv: list[str] | None = None) -> int:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--advanced-help" in argv:
        argv.remove("--advanced-help")
        build_parser(show_advanced=True).parse_args([*argv, "--help"])
    args = build_parser().parse_args(argv)
    reporter = ConsoleReporter(enabled=not args.quiet, verbose=args.verbose)
    logger = None if args.no_log else RunLogger()
    if logger:
        reporter.path("Run directory", logger.run_dir)
    if args.prepare_dataset:
        written = prepare_dataset(args.prepare_dataset, args.dataset_dir, reporter=reporter, logger=logger)
        if args.quiet:
            print(f"Wrote {len(written)} problems to {args.dataset_dir}")
        return 0

    reporter.title("Auto Prove")
    client = client_from_env()
    reporter.info(f"LLM client: {type(client).__name__}")
    if langsmith_is_configured():
        reporter.info(f"LangSmith tracing: enabled (project: {langsmith_project()})")
    if args.task_dir:
        try:
            destination = _solve_with_optional_review(args, args.task_dir, client, logger, reporter)
            print(destination)
            return 0
        except Exception as exc:
            reporter.error(str(exc))
            if logger:
                logger.event("cli_failed", {"error": str(exc)})
            if args.verbose:
                raise
            return 1
        finally:
            wait_for_langsmith_traces()

    formalizer = Formalizer()
    theorem = args.lean if args.lean else formalizer.run(args.problem)
    task_dir = args.work_root / "_inline_task"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_dir.joinpath("problem.md").write_text((args.problem or "Inline Lean theorem").strip() + "\n", encoding="utf-8")
    task_dir.joinpath("prove.lean").write_text("import Mathlib\n\nset_option autoImplicit false\n\n" + theorem.strip() + "\n", encoding="utf-8")
    try:
        destination = _solve_with_optional_review(args, task_dir, client, logger, reporter)
        print(destination)
        return 0
    except Exception as exc:
        reporter.error(str(exc))
        if logger:
            logger.event("cli_failed", {"error": str(exc)})
        if args.verbose:
            raise
        print("No verified proof found.", file=sys.stderr)
        return 1
    finally:
        wait_for_langsmith_traces()


def _solve_with_optional_review(
    args: argparse.Namespace,
    task_dir: Path,
    client,
    logger: RunLogger | None,
    reporter: ConsoleReporter,
) -> Path:
    solver_fn = solve_problem_directory_sorry_driven
    if args.tui:
        from .tui import run_with_tui

        def solver_fn(**kwargs):
            kwargs.pop("reporter", None)
            return run_with_tui(solve_problem_directory_sorry_driven, **kwargs)

    return solve_problem_directory_with_review_retries(
        problem_dir=task_dir,
        output_dir=args.output_dir,
        client=client,
        work_root=args.work_root,
        config=make_config(args),
        review_retries=review_retries(args),
        logger=logger,
        reporter=reporter,
        solver_fn=solver_fn,
    )


if __name__ == "__main__":
    raise SystemExit(main())
