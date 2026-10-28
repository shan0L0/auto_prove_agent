from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import replace
from pathlib import Path
from typing import List, Optional

from ..controller import SearchController, default_workspace
from ..dataset import _export_workspace, _fresh_workspace_dir, _lean_file, _strip_imports_and_options
from ..error_classifier import classify_lean_error
from ..goal_extractor import extract_initial_goals, extract_sorry_goals_lsp
from ..lean_lsp import LeanLSPClient
from ..lean_runner import LeanRunner
from ..llm import FalsifiedSublemma, LLMClient, ParentWitnessFalsificationOut, SorrySkeletonOut
from ..logging_utils import RunLogger
from ..models import ProofState, RejectedDesign
from ..nodes import Analyzer, Repairer, Retriever, TacticProposer
from ..retrieval import retrieve_knowledge
from ..ui import ConsoleReporter, truncate
from .reporting import _node_json, _result_json, write_sorry_report
from .tactics import _complete_with_tactic, _header, _tactics_for_node
from .types import SorryDrivenConfig, SorryDrivenResult, SorryNode
from .validation import (
    _apply_design_review,
    _build_skeleton_code,
    _child_part_file,
    _declaration_prefix,
    _enrich_split_error,
    _extract_child_names_from_skeleton,
    _lean_safe_name,
    _parent_proof_sorry_error,
    _phase_error,
    _proof_design_markdown,
    _selected_design_text,
    _validate_sorry_skeleton_fields,
)


def solve_problem_directory_sorry_driven(
    problem_dir: Path,
    output_dir: Path,
    client: LLMClient,
    work_root: Path = Path("runs/workspaces"),
    config: Optional[SorryDrivenConfig] = None,
    logger: Optional[RunLogger] = None,
    reporter: Optional[ConsoleReporter] = None,
) -> Path:
    config = config or SorryDrivenConfig()
    reporter = reporter or ConsoleReporter(enabled=False)
    problem_dir = Path(problem_dir)
    workspace_dir = _fresh_workspace_dir(work_root, problem_dir.name)
    reporter.title(f"Sorry-Driven Solve {problem_dir.name}")
    reporter.step("Copying problem directory to sorry-driven workspace")
    reporter.path("Source", problem_dir)
    reporter.path("Workspace", workspace_dir)
    shutil.copytree(problem_dir, workspace_dir)

    runner = LeanRunner(default_workspace(), timeout_seconds=config.timeout_seconds)
    theorem = _strip_imports_and_options((workspace_dir / "prove.lean").read_text(encoding="utf-8"))
    prover = SorryDrivenProver(client, runner, config, logger, reporter, workspace_dir)
    result = prover.run(theorem)
    prover.write_token_usage()
    write_sorry_report(result)
    if logger:
        logger.event("sorry_driven_finished", _result_json(result))
    return _export_workspace(workspace_dir, output_dir, problem_dir.name, reporter)


class SorryDrivenProver:
    def __init__(
        self,
        client: LLMClient,
        runner: LeanRunner,
        config: SorryDrivenConfig,
        logger: Optional[RunLogger],
        reporter: ConsoleReporter,
        workspace_dir: Path,
    ) -> None:
        self.client = client
        self.runner = runner
        self.config = config
        self.reporter = reporter
        self.workspace_dir = workspace_dir
        self.nodes: List[SorryNode] = []
        self.queue: List[str] = []
        self._state_lock = threading.RLock()
        self._io_lock = threading.RLock()
        self._llm_lock = threading.RLock()
        self._lsp_lock = threading.RLock()
        self._thread_local = threading.local()
        self.logger = _LockedRunLogger(logger, self._io_lock) if logger else None
        # LSP client — started lazily in run(), stopped when run() returns.
        self._lsp: Optional[LeanLSPClient] = None
        # Snapshot the shared token tracker so we can report only the calls
        # made during this run() — the same client may be reused across
        # retries / multiple workspaces.
        tracker = getattr(client, "token_tracker", None)
        self._token_tracker = tracker
        self._token_baseline = tracker.snapshot() if tracker is not None else 0
        # Cache of (node_id, witness_hash) → error string for witnesses we have
        # already proven incompatible with the parent claim. Lets retry attempts
        # that reuse the same witness short-circuit without another LLM round.
        self._parent_witness_blocklist: dict[tuple[str, str], str] = {}
        # Nodes retired by cross-level backtracking are kept on disk for debug,
        # but ignored by scheduling, status calculation, and final merging.
        self._retired_node_ids: set[str] = set()
        # Node ids are permanent for the lifetime of a run. Retired nodes stay
        # in the debug tree, so replacement branches must never reuse their ids.
        self._allocated_node_ids: set[str] = set()
        # A run-local research notebook. It captures lessons that should survive
        # individual Lean failures: invalid witnesses, current blockers, and
        # promising next directions.
        self.research_memory: dict[str, list] = {
            "blocked_on": [],
            "research_plan": [],
            "invalid_witnesses": [],
            "sanity_checks": [],
            "promising_directions": [],
            "open_questions": [],
            "notes": [],
        }
        # Normalized triage strategy for the root node, set by _run_research_triage.
        self._root_triage_strategy: str = ""

    def run(self, theorem: str) -> SorryDrivenResult:
        self._lsp = self._try_start_lsp()
        try:
            return self._run_inner(theorem)
        finally:
            if self._lsp:
                self._lsp.stop()
                self._lsp = None

    def write_token_usage(self) -> None:
        """Persist token usage for this run to ``workspace_dir/token_usage.json``."""
        tracker = self._token_tracker
        if tracker is None:
            return
        path = self.workspace_dir / "token_usage.json"
        with self._io_lock:
            tracker.write_json(path, start=self._token_baseline)
            report = tracker.report_since(self._token_baseline)
        if self.logger:
            self.logger.event(
                "token_usage_written",
                {
                    "path": str(path),
                    "input_tokens": report["input_tokens"],
                    "output_tokens": report["output_tokens"],
                    "total_tokens": report["total_tokens"],
                    "calls": report["calls"],
                },
            )
        self.reporter.info(
            f"Token usage: input={report['input_tokens']} output={report['output_tokens']} "
            f"total={report['total_tokens']} calls={report['calls']}"
        )

    def _try_start_lsp(self) -> Optional[LeanLSPClient]:
        """Start the Lean LSP server if Lean is available; return None on failure."""
        if not self.runner.is_available():
            return None
        if not LeanLSPClient.is_available():
            return None
        try:
            lsp = LeanLSPClient(
                workspace=self.runner.workspace,
                timeout=max(120.0, (self.config.timeout_seconds or 0) or 180.0),
            )
            lsp.start()
            self.reporter.info("Lean LSP server started — goal states will be extracted at sorry positions")
            self._event("lsp_started", {"workspace": str(self.runner.workspace)})
            return lsp
        except Exception as exc:
            self.reporter.info(f"LSP unavailable (falling back to trace_state): {exc}")
            return None

    def _run_inner(self, theorem: str) -> SorryDrivenResult:
        root = SorryNode(id="root", statement=theorem, claim="Main theorem")
        with self._state_lock:
            self.nodes.append(root)
            self.queue.append(root.id)
            self._allocated_node_ids.add(root.id)
        self._save_node(root)
        self._event("sorry_node_created", _node_json(root))

        self._run_research_triage(root)

        if self.config.max_workers <= 1:
            return self._run_queue_sequential()
        return self._run_queue_parallel()

    def _run_research_triage(self, root: SorryNode) -> None:
        """Run upstream triage as research planning, never as a stop signal."""
        if not self.config.enable_triage:
            return
        try:
            triage = self.client.triage_problem(theorem=root.statement)
        except Exception as exc:
            self.reporter.info(f"Triage call raised {type(exc).__name__}: {exc}; continuing.")
            return
        if triage is None:
            return
        with self._io_lock:
            self.workspace_dir.mkdir(parents=True, exist_ok=True)
            self.workspace_dir.joinpath("triage.json").write_text(
                triage.model_dump_json(indent=2), encoding="utf-8"
            )
        self._event(
            "sorry_triage_completed",
            {
                "difficulty": triage.difficulty,
                "recommended_strategy": triage.recommended_strategy,
                "recommended_mode": triage.recommended_mode,
                "rationale": triage.rationale,
                "required_external_theorems": triage.required_external_theorems,
                "likely_in_mathlib": triage.likely_in_mathlib,
                "research_plan": triage.research_plan,
                "blocked_on": triage.blocked_on,
            },
        )
        self.reporter.info(
            f"Triage: difficulty={triage.difficulty}, strategy={triage.recommended_strategy}"
        )
        normalized_strategy = _normalize_triage_strategy(triage.recommended_strategy)
        self._root_triage_strategy = normalized_strategy
        plan = list(triage.research_plan or [])
        if not plan and triage.rationale:
            plan = [triage.rationale]
        blocked_on = triage.blocked_on.strip()
        if not blocked_on and triage.difficulty in {"construction_required", "research_level"}:
            blocked_on = "construction"
        if normalized_strategy == "research_plan" and not blocked_on:
            blocked_on = "unknown"
        if blocked_on:
            self._remember("blocked_on", blocked_on)
        for step in plan:
            self._remember("research_plan", step)
        for theorem, in_lib in zip(triage.required_external_theorems, triage.likely_in_mathlib):
            if in_lib is False:
                self._remember(
                    "open_questions",
                    f"Find or formalize missing external theorem/construction: {theorem}",
                )
        if normalized_strategy in {"construct_witness", "explore_small_cases", "research_plan"}:
            root.diagnostics.append(
                "Research triage: "
                f"difficulty={triage.difficulty}, mode={triage.recommended_mode or normalized_strategy}. "
                f"Proceeding with proof search; blocker={blocked_on or 'none'}. "
                f"Rationale: {triage.rationale}"
            )
            self._save_node(root)
        if triage.recommended_strategy == "abandon" or self.config.triage_abandons:
            self._remember(
                "notes",
                "Legacy triage-abandon signal was treated as a research-plan hint; proof search continues.",
            )
            self._event(
                "sorry_triage_research_plan",
                {
                    "legacy_strategy": triage.recommended_strategy,
                    "normalized_strategy": normalized_strategy,
                    "blocked_on": blocked_on,
                },
            )

    def _triage_says_abandon(self, root: SorryNode) -> bool:
        """Backward-compatible wrapper: triage never abandons anymore."""
        self._run_research_triage(root)
        return False

    def _remember(self, key: str, item) -> None:
        bucket = self.research_memory.setdefault(key, [])
        if item not in bucket:
            bucket.append(item)

    def _research_memory_text(self) -> str:
        blocks = []
        for key in [
            "blocked_on",
            "research_plan",
            "invalid_witnesses",
            "sanity_checks",
            "promising_directions",
            "open_questions",
            "notes",
        ]:
            items = self.research_memory.get(key) or []
            if not items:
                continue
            rendered = []
            for item in items[-6:]:
                if isinstance(item, dict):
                    rendered.append(item.get("summary") or item.get("reason") or json.dumps(item, ensure_ascii=False))
                else:
                    rendered.append(str(item))
            blocks.append(f"{key}:\n- " + "\n- ".join(rendered))
        return "\n\n".join(blocks)

    def _run_queue_sequential(self) -> SorryDrivenResult:
        iterations = 0
        while self.queue and iterations < self.config.max_iterations:
            iterations += 1
            with self._state_lock:
                node_id = self.queue.pop(0)
            self._process_node(node_id, iterations)

        return self._result()

    def _run_queue_parallel(self) -> SorryDrivenResult:
        iterations = 0
        futures = {}
        worker_count = max(1, self.config.max_workers)
        self.reporter.info(f"Parallel sorry solving enabled with {worker_count} worker(s)")
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="sorry-node") as executor:
            while futures or iterations < self.config.max_iterations:
                submitted = False
                with self._state_lock:
                    while self.queue and len(futures) < worker_count and iterations < self.config.max_iterations:
                        node_id = self.queue.pop(0)
                        iterations += 1
                        futures[executor.submit(self._process_node, node_id, iterations)] = node_id
                        submitted = True
                if not futures:
                    if not submitted:
                        break
                    continue
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    node_id = futures.pop(future)
                    try:
                        future.result()
                    except Exception as exc:
                        self._mark_node_failed(node_id, f"Unhandled worker error: {type(exc).__name__}: {exc}")
        return self._result()

    def _process_node(self, node_id: str, iteration: int) -> None:
        with self._state_lock:
            node = self._node(node_id)
            if node.status != "sorry" or node.id in self._retired_node_ids:
                return
        self.reporter.step(f"Sorry iteration {iteration}: solving node {node.id} at depth {node.depth}")
        self._event("sorry_iteration_started", {"iteration": iteration, "node": _node_json(node)})

        if self._try_direct_close(node):
            return
        if self._node_is_retired(node.id):
            return
        skip_strategies = self.config.skip_direct_proof_strategies
        if (
            node.depth == 0
            and skip_strategies
            and self._root_triage_strategy in skip_strategies
        ):
            self.reporter.info(
                f"Triage strategy '{self._root_triage_strategy}' — skipping direct LLM proof, "
                "going straight to split"
            )
            self._event(
                "sorry_direct_proof_skipped",
                {"node_id": node.id, "triage_strategy": self._root_triage_strategy},
            )
        else:
            if self._try_llm_proof(node):
                return
            if self._node_is_retired(node.id):
                return
        self._split_node(node)

    def _result(self) -> SorryDrivenResult:
        self._try_finalize_completed_tree()
        status = self._status()
        diagnostics = []
        with self._state_lock:
            queue_remaining = bool(self.queue)
            failed_nodes = [
                node for node in self.nodes
                if node.status == "failed" and node.id not in self._retired_node_ids
            ]
            nodes = list(self.nodes)
        if queue_remaining:
            diagnostics.append("Reached iteration limit before the sorry queue was exhausted.")
        if failed_nodes:
            diagnostics.extend(f"{node.id}: {'; '.join(node.diagnostics[-2:])}" for node in failed_nodes)
        result = SorryDrivenResult(
            status=status,
            workspace_dir=self.workspace_dir,
            nodes=nodes,
            diagnostics=diagnostics,
            research_memory=dict(self.research_memory),
        )
        return result

    def _try_finalize_completed_tree(self) -> None:
        with self._state_lock:
            active_nodes = [node for node in self.nodes if node.id not in self._retired_node_ids]
            if self.queue or any(node.status == "failed" for node in active_nodes):
                return
            if not active_nodes:
                return
            root = active_nodes[0]
            if root.status == "proved" and root.proof_code:
                self._write_final_proof(root.proof_code)
                return
            if root.status != "in_progress":
                return
        merged_by_id: dict[str, str] = {}
        try:
            merged_body = self._merge_node_code(root, merged_by_id)
        except ValueError as exc:
            root.diagnostics.append(f"Final proof merge failed: {exc}")
            self._save_node(root)
            self._event("sorry_final_merge_failed", {"node_id": root.id, "error": str(exc)})
            return
        final_code = _header() + merged_body.strip() + "\n"
        result = self.runner.check(final_code, file_name="Final.lean", allow_sorry=False)
        if not result.ok:
            root.diagnostics.append(f"Final proof merge failed Lean check:\n{truncate(result.output, 2000)}")
            self._save_node(root)
            self._event("sorry_final_merge_failed", {"node_id": root.id, "output": result.output})
            return
        with self._state_lock:
            for node_id, merged in merged_by_id.items():
                node = self._node(node_id)
                if node.id in self._retired_node_ids:
                    continue
                node.status = "proved"
                node.proof_code = _header() + merged.strip() + "\n"
                self._save_node(node)
        self._write_final_proof(final_code)
        self._event("sorry_final_proof_merged", {"root_id": root.id, "elapsed_seconds": result.elapsed_seconds})

    def _merge_node_code(self, node: SorryNode, merged_by_id: dict[str, str]) -> str:
        if node.status == "proved" and node.proof_code:
            return _extract_declaration_block(
                _strip_imports_and_options(node.proof_code),
                _declaration_prefix(node.statement),
                context_prefix=node.preamble,
            )
        if node.status != "in_progress" or not node.skeleton_code:
            raise ValueError(f"node {node.id} is not ready to merge (status={node.status})")
        merged = _strip_imports_and_options(node.skeleton_code)
        for child_id in node.children:
            child = self._node(child_id)
            child_code = self._merge_node_code(child, merged_by_id)
            merged = _replace_declaration(merged, _declaration_prefix(child.statement), child_code)
        if re.search(r"\bsorry\b", merged):
            raise ValueError(f"merged proof for node {node.id} still contains sorry")
        merged_by_id[node.id] = merged
        return merged

    def _write_final_proof(self, final_code: str) -> None:
        with self._io_lock:
            self.workspace_dir.joinpath("final.lean").write_text(final_code, encoding="utf-8")
            if self.logger:
                self.logger.final(final_code)

    # ------------------------------------------------------------------
    # Phase tracking (feeds TUI colour changes)
    # ------------------------------------------------------------------

    def _phase(self, node: SorryNode, phase: str) -> None:
        """Emit a phase-change event so the TUI can colour the node by activity.

        *phase* is one of:
          ``"lean_closing"``   – trying automation tactics via Lean
          ``"llm_proving"``    – asking the LLM for a complete proof
          ``"lean_verifying"`` – Lean is checking an LLM candidate
          ``"llm_designing"``  – asking the LLM to design a proof strategy
          ``"llm_splitting"``  – asking the LLM to generate a sorry skeleton
          ``"lean_checking"``  – Lean is validating the skeleton
        """
        self._event("sorry_phase_changed", {"node_id": node.id, "phase": phase})

    def _try_direct_close(self, node: SorryNode) -> bool:
        """Attempt to close *node* with a short list of automation tactics.

        The tactic list is chosen based on the theorem's structural shape
        (from :func:`analyze_theorem_shape`) so that irrelevant tactics are
        skipped and domain-appropriate ones are tried first.

        Tactics are dispatched to a small thread pool. Each ``runner.check``
        spawns its own ``lake env lean`` subprocess in an isolated tempdir, so
        parallel calls are safe. We record every dispatched attempt before
        accepting the first successful result; this keeps traces complete and
        avoids untracked background Lean processes.
        """
        self._phase(node, "lean_closing")
        tactics = _tactics_for_node(node)
        if not tactics:
            return False

        workers = max(1, min(self.config.tactic_workers, len(tactics)))
        self.reporter.step(
            f"Trying {len(tactics)} direct Lean closure(s) for {node.id} "
            f"(parallelism={workers})"
        )
        preamble = (node.preamble.strip() + "\n\n") if node.preamble.strip() else ""

        # Build the (tactic, code, file_name, attempt_name) records up front so
        # every spawned future has a deterministic mapping back to identifiers.
        plan = []
        for tactic in tactics:
            safe_name = re.sub(r"[^A-Za-z0-9_]", "_", tactic)
            code = _complete_with_tactic(node.statement, tactic)
            plan.append((tactic, safe_name, code))

        if workers == 1:
            return self._try_direct_close_serial(node, preamble, plan)

        executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"direct-{node.id}")
        futures: dict = {}
        for tactic, safe_name, code in plan:
            future = executor.submit(
                self.runner.check,
                _header() + preamble + code,
                f"{node.id}_direct_{safe_name}.lean",
            )
            futures[future] = (tactic, safe_name, code)

        winner: Optional[tuple[str, str]] = None
        try:
            for future in as_completed(futures):
                tactic, safe_name, code = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    self.reporter.warning(
                        f"direct closure '{tactic}' raised {type(exc).__name__}: {exc}"
                    )
                    continue
                self._write_attempt(node, f"direct_{safe_name}", code, result)
                if result.ok and winner is None:
                    winner = (tactic, code)
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

        if winner is None:
            return False
        tactic, code = winner
        with self._state_lock:
            if node.id in self._retired_node_ids:
                return False
            node.status = "proved"
            node.proof_code = code
        self._save_node(node)
        self.reporter.success(f"{node.id} closed directly with `{tactic}`")
        self._event(
            "sorry_node_proved",
            {"node": _node_json(node), "method": f"direct:{tactic}"},
        )
        return True

    def _try_direct_close_serial(self, node: SorryNode, preamble: str, plan) -> bool:
        for tactic, safe_name, code in plan:
            result = self.runner.check(
                _header() + preamble + code,
                file_name=f"{node.id}_direct_{safe_name}.lean",
            )
            self._write_attempt(node, f"direct_{safe_name}", code, result)
            if result.ok:
                with self._state_lock:
                    if node.id in self._retired_node_ids:
                        return False
                    node.status = "proved"
                    node.proof_code = code
                self._save_node(node)
                self.reporter.success(f"{node.id} closed directly with `{tactic}`")
                self._event(
                    "sorry_node_proved",
                    {"node": _node_json(node), "method": f"direct:{tactic}"},
                )
                return True
        return False

    def _try_llm_proof(self, node: SorryNode) -> bool:
        self._phase(node, "llm_proving")
        self.reporter.step(f"Requesting full Lean proof for {node.id}")
        state = self._prepare_state(node)
        setattr(state, "prompt_mode", "sorry_direct")
        client = self._worker_client()
        controller = SearchController(
            runner=self.runner,
            proposer=TacticProposer(client),
            repairer=Repairer(client),
            beam_width=self.config.beam_width,
            max_rounds=self.config.max_rounds,
            logger=self.logger,
            reporter=self.reporter,
            artifact_dir=self._node_dir(node),
            min_candidate_score=self.config.min_candidate_score,
        )
        result_state = controller.solve(state)
        if result_state.verified_code:
            with self._state_lock:
                if node.id in self._retired_node_ids:
                    return False
                node.status = "proved"
                node.proof_code = result_state.header + result_state.verified_code
            self._save_node(node)
            self.reporter.success(f"{node.id} proved by LLM")
            self._event("sorry_node_proved", {"node": _node_json(node), "method": "llm"})
            return True
        node.diagnostics.extend(_failed_attempt_summaries(result_state))
        node.diagnostics.extend(result_state.diagnostics[-3:])
        # Forward any sorry-hint diagnostics from rejected candidates so the
        # subsequent split / design stage has context about what sub-goal the
        # LLM got stuck on.
        sorry_hints = getattr(client, "last_sorry_hints", [])
        if sorry_hints:
            node.diagnostics.extend(sorry_hints)
            self.reporter.info(
                f"{node.id}: captured {len(sorry_hints)} sorry-hint(s) for split stage"
            )
        self._save_node(node)
        return False

    def _split_node(self, node: SorryNode) -> None:
        if self._node_is_retired(node.id):
            return
        if node.depth >= self.config.max_depth:
            with self._state_lock:
                if node.id in self._retired_node_ids:
                    return
                node.status = "failed"
                node.diagnostics.append("Reached maximum sorry-driven split depth.")
            self._save_node(node)
            self._event("sorry_node_failed", _node_json(node))
            return

        self.reporter.step(f"Splitting {node.id} into sorry-backed child lemmas")
        state = self._prepare_state(node)
        setattr(state, "prompt_mode", "sorry_split")
        client = self._worker_client()
        design = self._choose_proof_design(node, state, client)
        if design:
            setattr(state, "proof_design", design)
            node.proof_design = design
            self._save_node(node)
        last_error = ""
        for attempt in range(1, self.config.max_split_retries + 1):
            self._phase(node, "llm_splitting")
            skeleton = client.split_with_sorries(state, max_subgoals=self.config.max_subgoals)
            self._log_split_interaction(node, attempt, client)
            if not skeleton:
                last_error = getattr(client, "last_error", "LLM did not return a skeleton.")
                node.diagnostics.append(last_error)
                continue
            validation_error = _validate_sorry_skeleton_fields(skeleton)
            if validation_error and "parent_proof_code references" in validation_error:
                child_names = _extract_child_names_from_skeleton(skeleton)
                if child_names:
                    self.reporter.info(f"{node.id}: naming error detected, attempting parent assembly repair")
                    repaired = client.repair_parent_assembly(
                        theorem=node.statement,
                        parent_proof_code=skeleton.parent_proof_code,
                        child_names=child_names,
                        setup_code=skeleton.setup_code,
                        goals="\n".join(state.goals),
                    )
                    if repaired:
                        skeleton = skeleton.model_copy(update={"parent_proof_code": repaired})
                        validation_error = _validate_sorry_skeleton_fields(skeleton)
            if validation_error:
                last_error = validation_error
                enriched = _enrich_split_error(validation_error)
                state.diagnostics.append(enriched)
                node.diagnostics.append(f"Skeleton attempt {attempt} failed: {truncate(enriched, 600)}")
                self._write_rejected_skeleton(node, attempt, skeleton, validation_error)
                continue
            parent_falsifier_error = self._falsify_parent_witness(node, skeleton, client, attempt)
            if parent_falsifier_error:
                if self._backtrack_from_falsified_child(
                    node,
                    parent_falsifier_error,
                    attempt=attempt,
                    skeleton=skeleton,
                ):
                    return
                last_error = parent_falsifier_error
                state.diagnostics.append(parent_falsifier_error)
                node.diagnostics.append(
                    f"Skeleton attempt {attempt} rejected by parent-witness validation: "
                    f"{truncate(parent_falsifier_error, 600)}"
                )
                self._record_rejected_design(
                    node, state, attempt, skeleton,
                    failure_reason=f"parent-witness validator confirmed: {truncate(parent_falsifier_error, 300)}",
                )
                self._write_rejected_skeleton(node, attempt, skeleton, parent_falsifier_error)
                continue
            skeleton_code = _build_skeleton_code(node.statement, skeleton)
            self._phase(node, "lean_checking")
            result = self._check_skeleton_in_phases(node, skeleton, skeleton_code)
            boundary_error = _parent_proof_sorry_error(skeleton)
            if result.ok and not boundary_error:
                falsifier_error = self._falsify_witness(node, skeleton, client, attempt)
                if falsifier_error:
                    if self._backtrack_from_falsified_child(
                        node,
                        falsifier_error,
                        attempt=attempt,
                        skeleton=skeleton,
                    ):
                        return
                    self._write_skeleton_attempt(
                        node, attempt, skeleton, skeleton_code, result,
                        enriched_error=falsifier_error,
                    )
                    last_error = falsifier_error
                    state.diagnostics.append(falsifier_error)
                    node.diagnostics.append(
                        f"Skeleton attempt {attempt} rejected by witness validation: {truncate(falsifier_error, 600)}"
                    )
                    self._record_rejected_design(
                        node, state, attempt, skeleton,
                        failure_reason=f"sublemma-witness validator confirmed: {truncate(falsifier_error, 300)}",
                    )
                    continue
                self._write_skeleton_attempt(node, attempt, skeleton, skeleton_code, result, enriched_error="")
                self._accept_split(node, skeleton, skeleton_code)
                return
            raw_error = boundary_error or result.output
            enriched_error = _enrich_split_error(raw_error)
            self._write_skeleton_attempt(node, attempt, skeleton, skeleton_code, result, enriched_error=enriched_error)
            last_error = enriched_error
            state.diagnostics.append(enriched_error)
            node.diagnostics.append(f"Skeleton attempt {attempt} failed: {truncate(enriched_error, 600)}")
            self._record_rejected_design(
                node, state, attempt, skeleton,
                failure_reason=f"parent-assembly typecheck failed: {truncate(enriched_error, 300)}",
            )

        with self._state_lock:
            if node.id in self._retired_node_ids:
                return
            node.status = "failed"
            node.diagnostics.append(last_error or "Could not produce a type-checking sorry skeleton.")
        self._save_node(node)
        self._event("sorry_node_failed", _node_json(node))

    def _choose_proof_design(self, node: SorryNode, state: ProofState, client: LLMClient) -> str:
        self._phase(node, "llm_designing")
        self.reporter.step(f"Designing proof strategy for {node.id}")
        designs = client.design_proofs(state, max_designs=4)
        self._log_design_interaction(node, client)
        if not designs:
            error = getattr(client, "last_error", "LLM did not return proof designs.")
            node.diagnostics.append(f"Proof design unavailable: {truncate(error, 600)}")
            return ""

        if self.config.enable_design_review:
            self._phase(node, "llm_design_review")
            self.reporter.step(f"Reviewing proof designs for {node.id}")
            review = client.review_proof_designs(designs, state)
            if review:
                designs = _apply_design_review(designs, review)
                self._event("sorry_design_reviewed", {
                    "node_id": node.id,
                    "selected": review.selected_design,
                    "approved": review.approved_designs,
                    "rejection_reasons": review.rejection_reasons,
                    "mathematical_warnings": review.mathematical_warnings,
                    "api_concerns": review.api_concerns,
                })
                if review.mathematical_warnings:
                    node.diagnostics.extend(
                        f"Design review warning: {w}" for w in review.mathematical_warnings
                    )
                if review.api_concerns:
                    node.diagnostics.extend(
                        f"Design review: possible missing API — {c}" for c in review.api_concerns
                    )
                self.reporter.info(
                    f"Design reviewer selected '{review.selected_design}' "
                    f"({len(review.approved_designs)} approved, "
                    f"{len(review.rejection_reasons)} rejected)"
                )
            else:
                self.reporter.info(f"Design review unavailable: {getattr(client, 'last_error', '')}")

        selected = _selected_design_text(designs)
        with self._io_lock:
            design_dir = self._node_dir(node) / "designs"
            design_dir.mkdir(parents=True, exist_ok=True)
            design_dir.joinpath("proof_design.json").write_text(designs.model_dump_json(indent=2), encoding="utf-8")
            design_dir.joinpath("proof_design.md").write_text(_proof_design_markdown(designs), encoding="utf-8")
            self.reporter.path("Proof design saved", design_dir / "proof_design.md")
        self._event("sorry_proof_design_selected", {"node_id": node.id, "design": designs.model_dump(mode="json")})
        return selected

    def _check_skeleton_in_phases(self, node: SorryNode, skeleton: SorrySkeletonOut, skeleton_code: str):
        # Include the node's accumulated preamble (ancestor setup_code) so that
        # child-node splits can reference helper definitions from parent splits.
        preamble = (node.preamble.strip() + "\n\n") if node.preamble.strip() else ""
        setup = _strip_imports_and_options(skeleton.setup_code).strip()
        if setup:
            setup_result = self.runner.check(_header() + preamble + setup + "\n", file_name=f"{node.id}_setup.lean", allow_sorry=False)
            if not setup_result.ok:
                return replace(setup_result, stderr=_phase_error("setup_code", setup_result.output))
        for index, sublemma in enumerate(skeleton.sublemmas, start=1):
            child_code = _header() + preamble + (setup + "\n\n" if setup else "") + _strip_imports_and_options(sublemma.lean_statement).strip() + "\n"
            child_result = self.runner.check(child_code, file_name=f"{node.id}_child_{index:02d}.lean", allow_sorry=True)
            if not child_result.ok:
                return replace(child_result, stderr=_phase_error(f"child lemma {index}", child_result.output))
        parent_result = self.runner.check(_header() + preamble + skeleton_code, file_name=f"{node.id}_parent_assembly.lean", allow_sorry=True)
        if not parent_result.ok:
            parent_result = replace(parent_result, stderr=_phase_error("parent assembly", parent_result.output))
        return parent_result

    def _falsify_parent_witness(
        self,
        node: SorryNode,
        skeleton: SorrySkeletonOut,
        client: LLMClient,
        attempt: int,
    ) -> str:
        """Direct witness falsification against the parent claim, BEFORE typecheck.

        Catches witnesses that obviously fail one of the parent's conjuncts (e.g. the
        all-1 matrix proposed for a Hadamard claim) so we don't waste 3+ retries on
        skeletons that can never close the parent. Returns an error string if Lean
        confirms the falsifier; "" otherwise.
        """
        preamble = node.preamble.strip()
        setup = _strip_imports_and_options(skeleton.setup_code).strip()
        if not preamble and not setup:
            return ""
        cache_key = (node.id, _witness_hash(preamble, setup))
        cached = self._parent_witness_blocklist.get(cache_key)
        if cached:
            self._event(
                "sorry_parent_witness_blocklisted",
                {"node_id": node.id, "attempt": attempt, "reason": cached},
            )
            return cached

        self._phase(node, "llm_validating")
        validation = client.validate_parent_witness(
            parent_statement=node.statement,
            preamble=preamble,
            setup_code=setup,
        )
        self._log_parent_validation_interaction(node, attempt, client)
        if not validation:
            return ""
        probe = (validation.falsifier_lean_code or "").strip()
        if not probe or "sorry" in probe:
            self._record_validation_suspicions(node, attempt, validation, lean_confirmed=False)
            return ""

        preamble_block = (preamble + "\n\n") if preamble else ""
        setup_block = (setup + "\n\n") if setup else ""
        probe_code = _header() + preamble_block + setup_block + probe + "\n"
        file_name = f"{node.id}_falsify_parent_{attempt:02d}.lean"
        result = self.runner.check(probe_code, file_name=file_name, allow_sorry=False)
        self._write_parent_falsifier_attempt(node, attempt, validation, probe_code, result)
        if not result.ok:
            self._record_validation_suspicions(node, attempt, validation, lean_confirmed=False)
            return ""
        error = (
            f"Parent-witness validation: Lean confirmed the chosen witness fails the parent claim. "
            f"Failing conjunct: {validation.failing_conjunct or '(unspecified)'}. "
            f"Reason: {validation.rationale or '(none)'}"
        )
        self._parent_witness_blocklist[cache_key] = error
        self._event(
            "sorry_parent_witness_falsified",
            {
                "node_id": node.id,
                "attempt": attempt,
                "failing_conjunct": validation.failing_conjunct,
                "rationale": validation.rationale,
                "probe_file": file_name,
            },
        )
        self._remember(
            "invalid_witnesses",
            {
                "summary": f"Witness rejected for `{node.id}`: {validation.failing_conjunct or '(unspecified conjunct)'}",
                "reason": validation.rationale or error,
                "evidence": result.output or file_name,
            },
        )
        self._remember(
            "sanity_checks",
            f"Lean-confirmed witness sanity check failed at attempt {attempt} for node `{node.id}`.",
        )
        self._record_validation_suspicions(node, attempt, validation, lean_confirmed=True)
        return error

    def _record_rejected_design(
        self,
        node: SorryNode,
        state: ProofState,
        attempt: int,
        skeleton: SorrySkeletonOut,
        failure_reason: str,
        failing_conjunct: str = "",
    ) -> None:
        """Append a structured `RejectedDesign` so future split attempts see it.

        The summary captures the witness identity (setup_code prefix or, if absent,
        a fingerprint of the parent_proof_code so we can still distinguish reruns).
        Both ``state`` and ``node`` get the entry so retry-loop iterations and
        subsequent runs of `_prepare_state` share one ledger.
        """
        setup = _strip_imports_and_options(skeleton.setup_code).strip()
        if setup:
            witness_summary = setup[:600]
        else:
            preamble = node.preamble.strip()
            head = preamble.splitlines()[0] if preamble else ""
            witness_summary = (
                f"(no new setup; inherited preamble: {head[:200]})"
                if head
                else "(no witness in scope; rejection was structural)"
            )
        entry = RejectedDesign(
            attempt=attempt,
            failure_reason=failure_reason,
            witness_summary=witness_summary,
            failing_conjunct=failing_conjunct,
        )
        with self._state_lock:
            node.rejected_designs.append(entry)
        state.rejected_designs.append(entry)
        self._event(
            "sorry_design_rejected",
            {
                "node_id": node.id,
                "attempt": attempt,
                "failure_reason": failure_reason,
                "witness_summary": witness_summary,
                "failing_conjunct": failing_conjunct,
            },
        )

    def _backtrack_from_falsified_child(
        self,
        node: SorryNode,
        reason: str,
        attempt: int,
        skeleton: SorrySkeletonOut,
    ) -> bool:
        """Retire a stale subtree when Lean confirms an inherited witness is false.

        A split rejection usually belongs to the current node. But if the current
        split did not introduce a substantive declaration and the falsifier only
        becomes meaningful because of inherited preamble definitions, the useful
        action is to reject the ancestor construction that introduced that
        preamble and ask it to split again.
        """
        if node.parent_id is None or not node.preamble.strip():
            return False
        setup = _strip_imports_and_options(skeleton.setup_code).strip()
        if _setup_introduces_substantive_declaration(setup):
            return False
        target = self._ancestor_introducing_preamble(node)
        if target is None or target.id == node.id:
            return False

        witness_summary = _preamble_witness_summary(node.preamble)
        entry = RejectedDesign(
            attempt=attempt,
            failure_reason=f"Backtracked from child `{node.id}`: {truncate(reason, 500)}",
            witness_summary=witness_summary,
        )
        retired: list[SorryNode] = []
        with self._state_lock:
            target.rejected_designs.append(entry)
            target.diagnostics.append(
                f"Backtracking from child `{node.id}` because Lean confirmed the inherited "
                f"witness/design cannot satisfy that child goal: {truncate(reason, 700)}"
            )
            retired_ids = set(self._descendant_ids(target.id))
            self._retired_node_ids.update(retired_ids)
            for child in self.nodes:
                if child.id in retired_ids:
                    child.status = "backtracked"
                    child.diagnostics.append(
                        f"Retired by backtracking to `{target.id}` after `{node.id}` falsified the inherited witness."
                    )
                    retired.append(child)
            self.queue = [
                queued for queued in self.queue
                if queued not in retired_ids and queued != target.id
            ]
            target.status = "sorry"
            target.proof_code = None
            target.skeleton_code = None
            target.children = []
            self.queue.append(target.id)

        self._save_node(target)
        for retired_node in retired:
            self._save_node(retired_node)
        self.reporter.warning(
            f"Backtracking to {target.id}: child {node.id} falsified the inherited witness"
        )
        self._event(
            "sorry_backtrack",
            {
                "from_node": node.id,
                "target_node": target.id,
                "retired_nodes": [retired_node.id for retired_node in retired],
                "reason": reason,
                "witness_summary": witness_summary,
            },
        )
        return True

    def _ancestor_introducing_preamble(self, node: SorryNode) -> Optional[SorryNode]:
        names = _top_level_decl_names(node.preamble)
        ancestors: list[SorryNode] = []
        current = node
        while current.parent_id is not None:
            parent = self._node(current.parent_id)
            ancestors.append(parent)
            current = parent
        if names:
            for ancestor in ancestors:
                code = ancestor.skeleton_code or ""
                if any(re.search(rf"\b{re.escape(name)}\b", code) for name in names):
                    return ancestor
        return ancestors[0] if ancestors else None

    def _descendant_ids(self, node_id: str) -> list[str]:
        descendants: list[str] = []
        stack = [node_id]
        while stack:
            parent_id = stack.pop()
            children = [node for node in self.nodes if node.parent_id == parent_id]
            for child in children:
                descendants.append(child.id)
                stack.append(child.id)
        return descendants

    def _node_is_retired(self, node_id: str) -> bool:
        with self._state_lock:
            return node_id in self._retired_node_ids

    def _record_validation_suspicions(
        self,
        node: SorryNode,
        attempt: int,
        validation: ParentWitnessFalsificationOut,
        lean_confirmed: bool,
    ) -> None:
        """Surface soft hints from the validator's per-conjunct checks.

        Even when the LLM's Lean falsifier doesn't compile, its `conjunct_checks`
        list often pinpoints which conjunct it suspected. Forwarding that text
        into the node's diagnostics lets the *next* split prompt see "the model
        previously suspected conjunct X is unsatisfied by witness Y", nudging it
        toward a different construction without us needing a Lean confirmation.
        """
        suspicions = [check for check in validation.conjunct_checks if check.verdict == "fails"]
        if not suspicions:
            return
        prefix = (
            "Witness-validator confirmed (Lean-checked)"
            if lean_confirmed
            else "Witness-validator soft warning (no Lean confirmation)"
        )
        for check in suspicions:
            line = (
                f"{prefix} — attempt {attempt} witness was suspected to fail conjunct: "
                f"{check.conjunct}\nReasoning: {check.reasoning or '(none)'}"
            )
            node.diagnostics.append(line)
            self._remember(
                "sanity_checks",
                {
                    "summary": f"{prefix}: `{node.id}` attempt {attempt} may fail `{check.conjunct}`",
                    "reason": check.reasoning or "(none)",
                },
            )
        # Persist the full assessment to disk for offline review.
        with self._io_lock:
            assess_dir = self._node_dir(node) / "splits" / f"falsify_parent_{attempt:02d}"
            assess_dir.mkdir(parents=True, exist_ok=True)
            assess_dir.joinpath("conjunct_checks.json").write_text(
                json.dumps(
                    [check.model_dump() for check in validation.conjunct_checks],
                    indent=2,
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

    def _write_parent_falsifier_attempt(
        self,
        node: SorryNode,
        attempt: int,
        validation: ParentWitnessFalsificationOut,
        probe_code: str,
        result,
    ) -> None:
        with self._io_lock:
            falsify_dir = self._node_dir(node) / "splits" / f"falsify_parent_{attempt:02d}"
            falsify_dir.mkdir(parents=True, exist_ok=True)
            falsify_dir.joinpath("probe.lean").write_text(probe_code, encoding="utf-8")
            falsify_dir.joinpath("probe.diagnostics.txt").write_text(
                result.output or "", encoding="utf-8"
            )
            falsify_dir.joinpath("rationale.txt").write_text(
                f"failing_conjunct: {validation.failing_conjunct}\n\n{validation.rationale}",
                encoding="utf-8",
            )

    def _log_parent_validation_interaction(self, node: SorryNode, attempt: int, client: LLMClient) -> None:
        if self.logger:
            self.logger.llm_interaction(
                f"sorry_parent_witness_validation_{node.id}_{attempt}",
                {
                    "mode": getattr(client, "last_mode", "unknown"),
                    "error": getattr(client, "last_error", ""),
                    "request_messages": getattr(client, "last_request_messages", []),
                    "response_text": getattr(client, "last_response_text", ""),
                },
            )

    def _falsify_witness(
        self,
        node: SorryNode,
        skeleton: SorrySkeletonOut,
        client: LLMClient,
        attempt: int,
    ) -> str:
        """Ask the LLM for Lean falsification probes against each sublemma; verify in Lean.

        Returns an error string if the witness is provably wrong (Lean accepted a proof
        of False from a sublemma's hypothesis). Returns "" otherwise (proceed to accept).
        Failures of the validator itself (LLM error, malformed probe) are non-blocking.
        """
        preamble = node.preamble.strip()
        setup = _strip_imports_and_options(skeleton.setup_code).strip()
        # Most validations need a concrete witness in setup/preamble. Some bad
        # splits, however, put the concrete witness directly in the child lemma
        # statement, e.g. `∀ i j, ‖(1 : Matrix ...) i j‖ = 1`. Those should be
        # falsified before accepting the split and spawning doomed children.
        if not _should_validate_skeleton_witness(preamble, setup, skeleton):
            return ""
        sublemma_blocks = []
        for index, sublemma in enumerate(skeleton.sublemmas, start=1):
            statement = _strip_imports_and_options(sublemma.lean_statement).strip()
            sublemma_blocks.append(
                f"### Sublemma {index}: `{sublemma.name}`\n"
                f"Claim: {sublemma.claim}\n"
                f"```lean\n{statement}\n```"
            )
        self._phase(node, "llm_validating")
        validation = client.validate_skeleton_witness(
            parent_statement=node.statement,
            preamble=preamble,
            setup_code=setup,
            sublemma_blocks=sublemma_blocks,
        )
        self._log_validation_interaction(node, attempt, client)
        if not validation or not validation.falsified:
            return ""

        preamble_block = (preamble + "\n\n") if preamble else ""
        setup_block = (setup + "\n\n") if setup else ""
        for falsified in validation.falsified:
            probe = falsified.falsifier_lean_code.strip()
            if not probe or "sorry" in probe:
                continue
            probe_code = _header() + preamble_block + setup_block + probe + "\n"
            safe_name = _lean_safe_name(falsified.sublemma_name) or "sublemma"
            file_name = f"{node.id}_falsify_{attempt:02d}_{safe_name}.lean"
            result = self.runner.check(probe_code, file_name=file_name, allow_sorry=False)
            self._write_falsifier_attempt(node, attempt, falsified, probe_code, result)
            if result.ok:
                self._remember(
                    "invalid_witnesses",
                    {
                        "summary": f"Witness rejected for child sublemma `{falsified.sublemma_name}` at `{node.id}`.",
                        "reason": falsified.rationale or "(none)",
                        "evidence": result.output or file_name,
                    },
                )
                self._remember(
                    "sanity_checks",
                    f"Lean-confirmed child witness validation failed for `{falsified.sublemma_name}`.",
                )
                error = (
                    f"Witness validation: Lean confirmed sublemma `{falsified.sublemma_name}` "
                    f"is unsatisfiable under the chosen witness. "
                    f"Reason: {falsified.rationale or '(none)'}"
                )
                self._event(
                    "sorry_witness_falsified",
                    {
                        "node_id": node.id,
                        "attempt": attempt,
                        "sublemma": falsified.sublemma_name,
                        "rationale": falsified.rationale,
                        "probe_file": file_name,
                    },
                )
                return error
        return ""

    def _write_falsifier_attempt(
        self,
        node: SorryNode,
        attempt: int,
        falsified: FalsifiedSublemma,
        probe_code: str,
        result,
    ) -> None:
        with self._io_lock:
            falsify_dir = self._node_dir(node) / "splits" / f"falsify_{attempt:02d}"
            falsify_dir.mkdir(parents=True, exist_ok=True)
            stem = _lean_safe_name(falsified.sublemma_name) or "sublemma"
            falsify_dir.joinpath(f"{stem}.lean").write_text(probe_code, encoding="utf-8")
            falsify_dir.joinpath(f"{stem}.diagnostics.txt").write_text(
                result.output or "", encoding="utf-8"
            )
            falsify_dir.joinpath(f"{stem}.rationale.txt").write_text(
                falsified.rationale or "", encoding="utf-8"
            )

    def _log_validation_interaction(self, node: SorryNode, attempt: int, client: LLMClient) -> None:
        if self.logger:
            self.logger.llm_interaction(
                f"sorry_witness_validation_{node.id}_{attempt}",
                {
                    "mode": getattr(client, "last_mode", "unknown"),
                    "error": getattr(client, "last_error", ""),
                    "request_messages": getattr(client, "last_request_messages", []),
                    "response_text": getattr(client, "last_response_text", ""),
                },
            )

    def _accept_split(self, node: SorryNode, skeleton: SorrySkeletonOut, skeleton_code: str) -> None:
        with self._state_lock:
            if node.id in self._retired_node_ids:
                return
            node.status = "in_progress"
            node.skeleton_code = skeleton_code
            node.diagnostics.append(skeleton.rationale)
        self._write_split_files(node, skeleton, skeleton_code)
        # Build the preamble for children: inherit this node's preamble and
        # append the new setup_code so child nodes can reference all helper
        # definitions from ancestor splits.
        new_setup = _strip_imports_and_options(skeleton.setup_code).strip()
        child_preamble = "\n\n".join(p for p in [node.preamble, new_setup] if p)

        # Query LSP for goal states at each sorry in the skeleton.
        # The i-th sorry in skeleton_code corresponds to sublemma[i].
        lsp_goals = self._query_skeleton_goals(skeleton_code, child_preamble)
        if lsp_goals:
            self.reporter.info(
                f"{node.id}: LSP extracted {len(lsp_goals)} goal state(s) from skeleton"
            )
            self._event("lsp_goals_extracted", {
                "node_id": node.id,
                "count": len(lsp_goals),
                "goals": lsp_goals,
            })

        for index, sublemma in enumerate(skeleton.sublemmas, start=1):
            with self._state_lock:
                child_id = self._allocate_child_id(node, index)
            child = SorryNode(
                id=child_id,
                statement=sublemma.lean_statement,
                status="sorry",
                parent_id=node.id,
                depth=node.depth + 1,
                claim=sublemma.claim,
                preamble=child_preamble,
                # Attach the LSP goal if available (index is 1-based; goals list is 0-based)
                initial_goal=lsp_goals[index - 1] if index - 1 < len(lsp_goals) else "",
            )
            with self._state_lock:
                self.nodes.append(child)
                self.queue.append(child.id)
                node.children.append(child.id)
            self._save_node(child)
            # Announce each child immediately so the TUI can add it to the
            # tree without waiting for its sorry_iteration_started event.
            self._event("sorry_node_created", _node_json(child))
        self._save_node(node)
        self.reporter.success(f"{node.id} split into {len(skeleton.sublemmas)} child obligation(s)")
        self._event("sorry_node_split", {"node": _node_json(node), "children": node.children})

    def _allocate_child_id(self, node: SorryNode, index: int) -> str:
        """Return a stable, never-before-used child id for *node*.

        Backtracking keeps retired nodes in ``self.nodes`` for diagnostics. If a
        later replacement split reused ``sg1``/``sg2``, the new active children
        would collide with retired ids and be ignored by scheduling/status/merge
        checks. The first root split keeps the historical ``sg1`` naming used by
        tests and reports; retries get suffixed ids such as ``sg1_b2``.
        """
        existing = set(self._allocated_node_ids)
        existing.update(existing_node.id for existing_node in self.nodes)
        if node.id == "root":
            base = f"sg{index}"
        else:
            base = f"{node.id}_{index}"
        if base not in existing:
            self._allocated_node_ids.add(base)
            return base
        suffix = 2
        while True:
            candidate = f"{base}_b{suffix}"
            if candidate not in existing:
                self._allocated_node_ids.add(candidate)
                return candidate
            suffix += 1

    def _query_skeleton_goals(self, skeleton_code: str, preamble: str) -> List[str]:
        """Use the LSP server to get goal states at each sorry in *skeleton_code*.

        Returns an empty list if LSP is not available or the query fails.
        The returned list has one entry per sorry in source order, matching
        the order of ``skeleton.sublemmas``.
        """
        if not self._lsp:
            return []
        preamble_block = (preamble.strip() + "\n\n") if preamble.strip() else ""
        full_code = _header() + preamble_block + skeleton_code
        try:
            with self._lsp_lock:
                return extract_sorry_goals_lsp(self._lsp, full_code)
        except Exception as exc:
            self.reporter.info(f"LSP goal query failed: {exc}")
            return []

    def _prepare_state(self, node: SorryNode) -> ProofState:
        state = Retriever().run(Analyzer().run(node.statement))
        # Prepend the accumulated preamble (parent setup_code) to the header so
        # that Lean can resolve helper definitions when extracting goals, and so
        # the LLM receives the full context in its prompt.
        if node.preamble.strip():
            state.header = state.header.rstrip() + "\n\n" + node.preamble.strip() + "\n\n"
        # Use the LSP-extracted goal (set by _accept_split) when available;
        # fall back to the trace_state + regex extraction otherwise.
        if node.initial_goal:
            state.goals = [node.initial_goal]
            self.reporter.info(f"{node.id}: using LSP goal state ({len(node.initial_goal)} chars)")
        else:
            state.goals = extract_initial_goals(self.runner, state.header, state.theorem)
        state.diagnostics = list(node.diagnostics)
        state.rejected_designs = list(node.rejected_designs)
        memory_text = self._research_memory_text()
        if self.config.task_specific_guidance and memory_text:
            state.task_specific_guidance = (
                self.config.task_specific_guidance.strip()
                + "\n\nResearch memory from this run:\n"
                + memory_text
            )
        elif memory_text:
            state.task_specific_guidance = "Research memory from this run:\n" + memory_text
        else:
            state.task_specific_guidance = self.config.task_specific_guidance
        state.extra_retrieval_queries = list(self.config.extra_retrieval_queries)
        return retrieve_knowledge(
            state,
            runner=self.runner,
            workspace_dir=self.workspace_dir,
            mathlib_limit=self.config.mathlib_search_limit,
            enable_state_search=self.config.enable_state_search,
            state_search_limit=self.config.state_search_limit,
            state_search_revision=self.config.state_search_revision,
            reporter=self.reporter,
            logger=self.logger,
        )

    def _node(self, node_id: str) -> SorryNode:
        for node in self.nodes:
            if node.id == node_id:
                return node
        raise KeyError(node_id)

    def _status(self) -> str:
        with self._state_lock:
            nodes = [node for node in self.nodes if node.id not in self._retired_node_ids]
            queue = list(self.queue)
        if any(node.status == "failed" for node in nodes):
            blocked = self.research_memory.get("blocked_on") or []
            if blocked:
                return f"blocked_on_{_lean_safe_name(str(blocked[-1])) or 'unknown'}"
            return "failed"
        if queue:
            return "iteration_limit"
        if all(node.status == "proved" for node in nodes):
            return "proved"
        if all(node.status in {"proved", "in_progress"} for node in nodes):
            return "proved_conditionally"
        return "in_progress"

    def _node_dir(self, node: SorryNode) -> Path:
        path = self.workspace_dir / "sorry_nodes" / node.id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_node(self, node: SorryNode) -> None:
        with self._io_lock:
            node_dir = self._node_dir(node)
            node_dir.joinpath("node.json").write_text(json.dumps(_node_json(node), indent=2, ensure_ascii=False), encoding="utf-8")
            node_dir.joinpath("statement.lean").write_text(_lean_file(node.statement), encoding="utf-8")
            # prove.lean includes the preamble so a human (or child split) can work
            # with the file directly without missing parent helper definitions.
            if node.preamble.strip():
                prove_content = _header() + node.preamble.strip() + "\n\n" + node.statement.strip() + "\n"
            else:
                prove_content = _lean_file(node.statement)
            node_dir.joinpath("prove.lean").write_text(prove_content, encoding="utf-8")
            if node.proof_code:
                node_dir.joinpath("proof.lean").write_text(node.proof_code, encoding="utf-8")
            if node.skeleton_code:
                node_dir.joinpath("skeleton.lean").write_text(_header() + node.skeleton_code.strip() + "\n", encoding="utf-8")

    def _write_attempt(self, node: SorryNode, name: str, code: str, result) -> None:
        with self._io_lock:
            attempt_dir = self._node_dir(node) / "attempts"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            attempt_dir.joinpath(f"{name}.lean").write_text(_header() + code, encoding="utf-8")
            attempt_dir.joinpath(f"{name}.diagnostics.txt").write_text(result.output, encoding="utf-8")
        self._event(
            "sorry_attempt",
            {"node_id": node.id, "name": name, "ok": result.ok, "error_kind": "none" if result.ok else classify_lean_error(result.output)},
        )

    def _write_skeleton_attempt(self, node: SorryNode, attempt: int, skeleton: SorrySkeletonOut, skeleton_code: str, result, enriched_error: str = "") -> None:
        with self._io_lock:
            split_dir = self._node_dir(node) / "splits"
            split_dir.mkdir(parents=True, exist_ok=True)
            stem = f"split_{attempt:02d}"
            split_dir.joinpath(f"{stem}.lean").write_text(_header() + skeleton_code, encoding="utf-8")
            split_dir.joinpath(f"{stem}.json").write_text(skeleton.model_dump_json(indent=2), encoding="utf-8")
            split_dir.joinpath(f"{stem}.diagnostics.txt").write_text(result.output, encoding="utf-8")
        self._event("sorry_split_checked", {
            "node_id": node.id,
            "attempt": attempt,
            "ok": result.ok,
            "output": result.output,
            "error": enriched_error,
        })

    def _write_rejected_skeleton(self, node: SorryNode, attempt: int, skeleton: SorrySkeletonOut, error: str) -> None:
        with self._io_lock:
            split_dir = self._node_dir(node) / "splits"
            split_dir.mkdir(parents=True, exist_ok=True)
            stem = f"split_{attempt:02d}_rejected"
            split_dir.joinpath(f"{stem}.json").write_text(skeleton.model_dump_json(indent=2), encoding="utf-8")
            split_dir.joinpath(f"{stem}.diagnostics.txt").write_text(error, encoding="utf-8")
        self._event("sorry_split_rejected", {"node_id": node.id, "attempt": attempt, "error": error})

    def _write_split_files(self, node: SorryNode, skeleton: SorrySkeletonOut, skeleton_code: str) -> None:
        with self._io_lock:
            parts_dir = self.workspace_dir / "proof_parts" / node.id
            parts_dir.mkdir(parents=True, exist_ok=True)
            setup = _strip_imports_and_options(skeleton.setup_code).strip()
            parts_dir.joinpath("parent_assembly.lean").write_text(_header() + skeleton_code, encoding="utf-8")
            index_lines = [
                f"# Proof Parts for `{node.id}`",
                "",
                "Each child file is an independent Lean task. Edit the child file assigned to the subgoal.",
                "",
                "- `parent_assembly.lean`: checks that the child lemmas imply the parent theorem.",
            ]
            for index, sublemma in enumerate(skeleton.sublemmas, start=1):
                file_name = f"child_{index:02d}_{_lean_safe_name(sublemma.name) or 'subgoal'}.lean"
                path = parts_dir / file_name
                path.write_text(_child_part_file(setup, sublemma.lean_statement), encoding="utf-8")
                index_lines.append(f"- `{file_name}`: {sublemma.claim}")
            parts_dir.joinpath("README.md").write_text("\n".join(index_lines).rstrip() + "\n", encoding="utf-8")

    def _log_split_interaction(self, node: SorryNode, attempt: int, client: LLMClient) -> None:
        if self.logger:
            self.logger.llm_interaction(
                f"sorry_split_{node.id}_{attempt}",
                {
                    "mode": getattr(client, "last_mode", "unknown"),
                    "error": getattr(client, "last_error", ""),
                    "request_messages": getattr(client, "last_request_messages", []),
                    "response_text": getattr(client, "last_response_text", ""),
                },
            )

    def _log_design_interaction(self, node: SorryNode, client: LLMClient) -> None:
        if self.logger:
            self.logger.llm_interaction(
                f"sorry_design_{node.id}",
                {
                    "mode": getattr(client, "last_mode", "unknown"),
                    "error": getattr(client, "last_error", ""),
                    "request_messages": getattr(client, "last_request_messages", []),
                    "response_text": getattr(client, "last_response_text", ""),
                },
            )

    def _event(self, name: str, payload: dict) -> None:
        with self._io_lock:
            if self.logger:
                self.logger.event(name, payload)
            # TUI hook — lets TUIReporter update its node tree from structured events.
            # No-op for ConsoleReporter (which has no on_event method).
            on_event = getattr(self.reporter, "on_event", None)
            if on_event is not None:
                try:
                    on_event(name, payload)
                except Exception:
                    pass

    def _mark_node_failed(self, node_id: str, message: str) -> None:
        with self._state_lock:
            node = self._node(node_id)
            if node.id in self._retired_node_ids:
                return
            node.status = "failed"
            node.diagnostics.append(message)
        self._save_node(node)
        self._event("sorry_node_failed", _node_json(node))

    def _worker_client(self) -> LLMClient:
        existing = getattr(self._thread_local, "client", None)
        if existing is not None:
            return existing
        fork = self.client.fork()
        if fork is not None:
            self._thread_local.client = fork
            return fork
        return _LockedLLMClient(self.client, self._llm_lock)


class _LockedRunLogger:
    def __init__(self, logger: RunLogger, lock: threading.RLock) -> None:
        self._logger = logger
        self._lock = lock

    @property
    def run_dir(self):
        return self._logger.run_dir

    @property
    def trace_path(self):
        return self._logger.trace_path

    @property
    def llm_trace_path(self):
        return self._logger.llm_trace_path

    @property
    def llm_markdown_path(self):
        return self._logger.llm_markdown_path

    def event(self, name: str, payload: dict) -> None:
        with self._lock:
            self._logger.event(name, payload)

    def final(self, code: str) -> Path:
        with self._lock:
            return self._logger.final(code)

    def llm_interaction(self, phase: str, payload: dict) -> None:
        with self._lock:
            self._logger.llm_interaction(phase, payload)


class _LockedLLMClient(LLMClient):
    """Thread-safe per-worker view over a shared mutable LLM client.

    The underlying LangChain client stores diagnostics in ``last_*`` fields.
    This proxy serializes calls and copies those fields immediately, so
    concurrent workers can log their own interaction without another worker
    overwriting the shared state first.
    """

    def __init__(self, client: LLMClient, lock: threading.RLock) -> None:
        self._client = client
        self._lock = lock
        self._sync_last_fields()

    def propose(self, state: ProofState, beam_width: int):
        with self._lock:
            result = self._client.propose(state, beam_width)
            self._sync_last_fields()
            return result

    def design_proofs(self, state: ProofState, max_designs: int = 3):
        with self._lock:
            result = self._client.design_proofs(state, max_designs=max_designs)
            self._sync_last_fields()
            return result

    def split_with_sorries(self, state: ProofState, max_subgoals: int = 5):
        with self._lock:
            result = self._client.split_with_sorries(state, max_subgoals=max_subgoals)
            self._sync_last_fields()
            return result

    def review_failure(self, failure_context: str):
        with self._lock:
            result = self._client.review_failure(failure_context)
            self._sync_last_fields()
            return result

    def validate_skeleton_witness(
        self,
        parent_statement: str,
        preamble: str,
        setup_code: str,
        sublemma_blocks,
    ):
        with self._lock:
            result = self._client.validate_skeleton_witness(
                parent_statement=parent_statement,
                preamble=preamble,
                setup_code=setup_code,
                sublemma_blocks=sublemma_blocks,
            )
            self._sync_last_fields()
            return result

    def validate_parent_witness(
        self,
        parent_statement: str,
        preamble: str,
        setup_code: str,
    ):
        with self._lock:
            result = self._client.validate_parent_witness(
                parent_statement=parent_statement,
                preamble=preamble,
                setup_code=setup_code,
            )
            self._sync_last_fields()
            return result

    def triage_problem(self, theorem: str, retrieved_context: str = ""):
        with self._lock:
            result = self._client.triage_problem(theorem=theorem, retrieved_context=retrieved_context)
            self._sync_last_fields()
            return result

    def _sync_last_fields(self) -> None:
        self.last_response_text = getattr(self._client, "last_response_text", "")
        self.last_candidate_count = getattr(self._client, "last_candidate_count", 0)
        self.last_mode = getattr(self._client, "last_mode", "unknown")
        self.last_error = getattr(self._client, "last_error", "")
        self.last_request_messages = list(getattr(self._client, "last_request_messages", []))
        self.last_sorry_hints = list(getattr(self._client, "last_sorry_hints", []))


def _witness_hash(preamble: str, setup_code: str) -> str:
    """Stable identifier for the witness in scope; used to dedupe early-falsification calls."""
    blob = (preamble.strip() + "\n---\n" + setup_code.strip()).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def _normalize_triage_strategy(strategy: str) -> str:
    normalized = (strategy or "").strip().lower()
    if normalized == "abandon":
        return "research_plan"
    if normalized in {
        "direct_close",
        "split",
        "search_library",
        "construct_witness",
        "explore_small_cases",
        "research_plan",
    }:
        return normalized
    return "split"


def _failed_attempt_summaries(state: ProofState, max_attempts: int = 2) -> List[str]:
    if not state.attempts:
        return []
    summaries: List[str] = []
    recent_attempts = state.attempts[-max_attempts:]
    recent_diagnostics = state.diagnostics[-len(recent_attempts):]
    recent_errors = state.error_kinds[-len(recent_attempts):]
    start_number = max(1, len(state.attempts) - len(recent_attempts) + 1)
    for offset, candidate in enumerate(recent_attempts):
        diagnostic = recent_diagnostics[offset] if offset < len(recent_diagnostics) else ""
        error_kind = recent_errors[offset] if offset < len(recent_errors) else "unknown"
        summaries.append(
            "Rejected direct proof candidate. First sanity-check whether this construction is mathematically plausible; "
            "repair Lean details only if the witness still satisfies the goal.\n"
            f"Attempt: {start_number + offset}; score={candidate.score:.2f}; error_kind={error_kind}\n"
            f"Rationale: {candidate.rationale or '(none)'}\n"
            f"Lean code:\n{truncate(candidate.code, 1400)}\n"
            f"Lean diagnostics:\n{truncate(diagnostic or '(none)', 1400)}"
        )
    return summaries


_TOP_LEVEL_DECL_RE = re.compile(
    r"^(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev|instance)\s+\S+",
    re.MULTILINE,
)


def _extract_declaration_block(code: str, declaration_prefix: str, context_prefix: str = "") -> str:
    code = _drop_leading_context(code, context_prefix)
    start = code.find(declaration_prefix)
    if start < 0:
        name = _declaration_name(declaration_prefix)
        if name:
            match = re.search(
                rf"^(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev|instance)\s+{re.escape(name)}\b",
                code,
                flags=re.MULTILINE,
            )
            start = match.start() if match else -1
    if start < 0:
        raise ValueError(f"could not find proved declaration `{_declaration_name(declaration_prefix) or declaration_prefix[:80]}`")
    block_start = _declaration_block_start(code, start)
    end = len(code)
    for match in _TOP_LEVEL_DECL_RE.finditer(code, start + 1):
        if match.start() > start:
            end = match.start()
            break
    declaration = code[block_start:end].strip()
    if not declaration:
        raise ValueError(f"empty declaration for `{_declaration_name(declaration_prefix) or declaration_prefix[:80]}`")
    return declaration


def _extract_declaration(code: str, declaration_prefix: str) -> str:
    return _extract_declaration_block(code, declaration_prefix)


def _drop_leading_context(code: str, context_prefix: str) -> str:
    context = _strip_imports_and_options(context_prefix).strip()
    if not context:
        return code.strip()
    stripped = code.strip()
    if stripped.startswith(context):
        return stripped[len(context):].strip()
    return stripped


def _declaration_block_start(code: str, target_start: int) -> int:
    """Return the start of helpers immediately preceding the target declaration."""
    matches = [match for match in _TOP_LEVEL_DECL_RE.finditer(code) if match.start() <= target_start]
    if not matches:
        return target_start
    first = matches[0].start()
    # Include comments/whitespace immediately before the first helper declaration
    # in the child proof block, but not arbitrary imports/options (already stripped).
    prefix = code[:first]
    return 0 if not prefix.strip() else first


def _setup_introduces_substantive_declaration(setup: str) -> bool:
    return bool(_TOP_LEVEL_DECL_RE.search(_strip_imports_and_options(setup)))


def _should_validate_skeleton_witness(
    preamble: str,
    setup: str,
    skeleton: SorrySkeletonOut,
) -> bool:
    if preamble.strip() or setup.strip():
        return True
    statements = "\n".join(sublemma.lean_statement for sublemma in skeleton.sublemmas)
    return bool(re.search(r"\b(Matrix|Fin)\b|∀", statements))


def _top_level_decl_names(code: str) -> list[str]:
    names: list[str] = []
    for match in re.finditer(
        r"^(?:noncomputable\s+)?(?:theorem|lemma|def|abbrev|instance)\s+(\S+)",
        _strip_imports_and_options(code),
        flags=re.MULTILINE,
    ):
        raw = match.group(1)
        name = raw.split(".")[-1].strip()
        if name and name not in names:
            names.append(name)
    return names


def _preamble_witness_summary(preamble: str) -> str:
    code = _strip_imports_and_options(preamble).strip()
    if not code:
        return "(empty inherited preamble)"
    names = _top_level_decl_names(code)
    if names:
        return f"Inherited declarations: {', '.join(names[:8])}\n{truncate(code, 700)}"
    return truncate(code, 700)


def _replace_declaration(code: str, declaration_prefix: str, replacement: str) -> str:
    start = code.find(declaration_prefix)
    if start < 0:
        raise ValueError(f"could not find child placeholder `{_declaration_name(declaration_prefix) or declaration_prefix[:80]}`")
    end = len(code)
    for match in _TOP_LEVEL_DECL_RE.finditer(code, start + 1):
        if match.start() > start:
            end = match.start()
            break
    return code[:start].rstrip() + "\n\n" + replacement.strip() + "\n\n" + code[end:].lstrip()


def _declaration_name(declaration_prefix: str) -> str:
    match = re.search(r"\b(?:theorem|lemma|def|abbrev|instance)\s+(\S+)", declaration_prefix)
    return match.group(1) if match else ""
