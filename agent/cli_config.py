from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from .sorry_driven import SorryDrivenConfig


@dataclass(frozen=True)
class CliProfile:
    beam_width: int = 4
    max_rounds: int = 3
    max_workers: int = 3
    tactic_workers: int = 2
    max_depth: int = 3
    max_iterations: int = 50
    max_split_retries: int = 3
    max_subgoals: int = 5
    mathlib_search_limit: int = 8
    state_search_limit: int = 6
    review_retries: int = 1


PROFILES: dict[str, CliProfile] = {
    "quick": CliProfile(beam_width=2, max_rounds=1, max_workers=1, tactic_workers=1, max_depth=1, max_iterations=12, max_split_retries=1),
    "standard": CliProfile(),
    "deep": CliProfile(beam_width=5, max_rounds=4, max_workers=4, tactic_workers=2, max_depth=4, max_iterations=80, max_split_retries=4, mathlib_search_limit=12),
    "review": CliProfile(review_retries=1),
}


def build_parser(show_advanced: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Lean 4 proof agent MVP")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--problem", help="Natural-language or Lean theorem text.")
    group.add_argument("--lean", help="Lean theorem/lemma/example statement.")
    group.add_argument("--prepare-dataset", type=Path, help="Path to an ArXivLean parquet dataset.")
    group.add_argument("--task-dir", type=Path, help="Directory containing problem.md and prove.lean.")
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="standard",
        help="Preset search profile: quick, standard, deep, or review.",
    )
    parser.add_argument("--timeout", type=float, default=300.0, help="Lean verifier timeout in seconds; 0 disables the timeout.")
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--tui", action="store_true", help="Show live proof-tree TUI during solving.")
    parser.add_argument("--quiet", action="store_true", help="Disable human-readable progress output.")
    parser.add_argument("--verbose", action="store_true", help="Show candidate code and Lean diagnostics.")
    parser.add_argument("--advanced-help", action="store_true", help=argparse.SUPPRESS)

    advanced_help = None if show_advanced else argparse.SUPPRESS
    advanced = parser.add_argument_group("advanced options")
    advanced.add_argument("--beam-width", type=int, default=None, help=advanced_help)
    advanced.add_argument("--max-rounds", type=int, default=None, help=advanced_help)
    advanced.add_argument("--workers", "--max-workers", dest="max_workers", type=int, default=None, help="Maximum parallel sorry nodes to solve." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--tactic-workers", type=int, default=None, help="Maximum parallel direct Lean tactic probes inside one node." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--max-depth", type=int, default=None, help="Maximum recursive sorry-driven split depth." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--max-iterations", type=int, default=None, help="Maximum sorry-driven queue iterations." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--max-split-retries", type=int, default=None, help="Maximum LLM skeleton repair attempts." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--max-subgoals", type=int, default=None, help="Maximum child obligations per sorry-driven split." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--mathlib-search-limit", type=int, default=None, help="Maximum local Mathlib snippets to retrieve." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--enable-state-search", action="store_true", help="Query Lean State Search through LeanSearchClient." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument(
        "--ignore-candidate-score",
        action="store_true",
        help="Run Lean against every LLM proof candidate, including ones the LLM scored as low-confidence." if show_advanced else argparse.SUPPRESS,
    )
    advanced.add_argument(
        "--no-triage",
        action="store_true",
        help="Skip the upstream research-triage LLM call." if show_advanced else argparse.SUPPRESS,
    )
    advanced.add_argument(
        "--triage-abandon",
        action="store_true",
        help="Deprecated compatibility flag; triage now records a research plan and continues." if show_advanced else argparse.SUPPRESS,
    )
    advanced.add_argument("--state-search-limit", type=int, default=None, help="Maximum Lean State Search results to request." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--state-search-revision", help="Lean State Search revision, for example v4.22.0." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--dataset-dir", type=Path, default=Path("dataset"), help="Destination for --prepare-dataset." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--work-root", type=Path, default=Path("runs/workspaces"), help="Directory for copied writable workspaces." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument("--no-log", action="store_true", help="Disable run trace logging." if show_advanced else argparse.SUPPRESS)
    advanced.add_argument(
        "--review-retries",
        type=int,
        default=None,
        help="After a failed run, ask a reviewer agent for task-specific retry guidance this many times." if show_advanced else argparse.SUPPRESS,
    )
    return parser


def make_config(args: argparse.Namespace) -> SorryDrivenConfig:
    profile = PROFILES[args.profile]
    return SorryDrivenConfig(
        beam_width=args.beam_width if args.beam_width is not None else profile.beam_width,
        max_rounds=args.max_rounds if args.max_rounds is not None else profile.max_rounds,
        timeout_seconds=args.timeout,
        max_workers=max(1, args.max_workers if args.max_workers is not None else profile.max_workers),
        tactic_workers=max(1, args.tactic_workers if args.tactic_workers is not None else profile.tactic_workers),
        max_iterations=args.max_iterations if args.max_iterations is not None else profile.max_iterations,
        max_depth=args.max_depth if args.max_depth is not None else profile.max_depth,
        max_split_retries=args.max_split_retries if args.max_split_retries is not None else profile.max_split_retries,
        max_subgoals=args.max_subgoals if args.max_subgoals is not None else profile.max_subgoals,
        mathlib_search_limit=args.mathlib_search_limit if args.mathlib_search_limit is not None else profile.mathlib_search_limit,
        enable_state_search=args.enable_state_search,
        state_search_limit=args.state_search_limit if args.state_search_limit is not None else profile.state_search_limit,
        state_search_revision=args.state_search_revision,
        min_candidate_score=0.0 if args.ignore_candidate_score else SorryDrivenConfig.__dataclass_fields__["min_candidate_score"].default,
        enable_triage=not args.no_triage,
        triage_abandons=args.triage_abandon,
    )


def review_retries(args: argparse.Namespace) -> int:
    profile = PROFILES[args.profile]
    value = args.review_retries if args.review_retries is not None else profile.review_retries
    return max(0, value)
