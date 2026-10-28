from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .logging_utils import RunLogger
from .ui import ConsoleReporter


@dataclass(frozen=True)
class DatasetProblem:
    problem_idx: int
    problem: str
    formal_statement: str
    source: str = ""
    title: str = ""
    authors: str = ""

    @property
    def directory_name(self) -> str:
        return f"{self.problem_idx:04d}"


def prepare_dataset(
    parquet_path: Path,
    dataset_dir: Path,
    reporter: Optional[ConsoleReporter] = None,
    logger: Optional[RunLogger] = None,
) -> List[Path]:
    """Convert the ArXivLean parquet file into per-problem directories."""

    reporter = reporter or ConsoleReporter(enabled=False)
    reporter.title("Prepare Dataset")
    reporter.path("Parquet", parquet_path)
    reporter.path("Dataset directory", dataset_dir)
    if logger:
        logger.event("prepare_dataset_started", {"parquet_path": str(parquet_path), "dataset_dir": str(dataset_dir)})
    rows = _read_parquet_rows(parquet_path)
    dataset_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []
    for row in rows:
        problem = DatasetProblem(
            problem_idx=int(row["problem_idx"]),
            problem=str(row["problem"]),
            formal_statement=str(row["formal_statement"]),
            source=str(row.get("source", "")),
            title=str(row.get("title", "")),
            authors=str(row.get("authors", "")),
        )
        problem_dir = dataset_dir / problem.directory_name
        problem_dir.mkdir(parents=True, exist_ok=True)
        (problem_dir / "problem.md").write_text(_problem_markdown(problem), encoding="utf-8")
        (problem_dir / "prove.lean").write_text(_lean_file(problem.formal_statement), encoding="utf-8")
        written.append(problem_dir)
        reporter.detail(f"Wrote {problem_dir}")
    reporter.success(f"Wrote {len(written)} problem directorie(s)")
    if logger:
        logger.event("prepare_dataset_finished", {"count": len(written)})
    return written


def _read_parquet_rows(parquet_path: Path) -> Iterable[Dict[str, object]]:
    os.environ.setdefault("ARROW_USER_SIMD_LEVEL", "NONE")
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("Install pyarrow to read parquet datasets.") from exc

    table = pq.read_table(parquet_path)
    return table.to_pylist()


def _problem_markdown(problem: DatasetProblem) -> str:
    sections = [
        f"# Problem {problem.problem_idx}",
        problem.problem.strip(),
    ]
    metadata = []
    if problem.title:
        metadata.append(f"- Title: {problem.title}")
    if problem.source:
        metadata.append(f"- Source: {problem.source}")
    if problem.authors:
        metadata.append(f"- Authors: {problem.authors}")
    if metadata:
        sections.append("## Metadata\n\n" + "\n".join(metadata))
    return "\n\n".join(sections).rstrip() + "\n"


def _lean_file(formal_statement: str) -> str:
    return "import Mathlib\n\nset_option autoImplicit false\n\n" + formal_statement.strip() + "\n"


def _strip_imports_and_options(code: str) -> str:
    lines = []
    for line in code.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("set_option "):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _fresh_workspace_dir(work_root: Path, name: str) -> Path:
    work_root.mkdir(parents=True, exist_ok=True)
    base = work_root / name
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = work_root / f"{name}_{index}"
        if not candidate.exists():
            return candidate
        index += 1


def _export_workspace(workspace_dir: Path, output_dir: Path, name: str, reporter: ConsoleReporter) -> Path:
    destination = Path(output_dir) / name
    if destination.exists():
        reporter.warning(f"Replacing existing output directory: {destination}")
        shutil.rmtree(destination)
    reporter.step("Copying workspace to output")
    shutil.copytree(workspace_dir, destination)
    return destination
