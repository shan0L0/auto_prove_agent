from pathlib import Path

from agent.models import ProofState
from agent.retrieval import MathlibSearcher, parse_state_search_output, retrieval_queries_for_state, write_retrieval_report


def test_mathlib_searcher_returns_source_snippet(tmp_path):
    root = tmp_path / "Mathlib"
    path = root / "Data" / "Nat" / "Prime.lean"
    path.parent.mkdir(parents=True)
    path.write_text(
        "namespace Nat\n\n"
        "theorem Prime.two_le {p : Nat} (hp : Nat.Prime p) : 2 <= p := by\n"
        "  exact hp.two_le\n\n"
        "end Nat\n",
        encoding="utf-8",
    )

    results = MathlibSearcher([root]).search("Nat.Prime", limit=1)

    assert len(results) == 1
    assert results[0].source == "mathlib"
    assert results[0].file_path == str(path)
    assert "Prime.two_le" in results[0].snippet


def test_retrieval_queries_include_constants_from_theorem():
    state = ProofState(theorem="example (p : Nat) : Nat.Prime p -> Int.ModEq 24 (p : Int) 5 -> True")

    queries = retrieval_queries_for_state(state)

    assert "Nat.Prime" in queries
    assert "Int.ModEq" in queries


def test_mathlib_search_many_keeps_later_precise_queries(tmp_path):
    root = tmp_path / "Mathlib"
    generic_dir = root / "Data" / "Fin"
    generic_dir.mkdir(parents=True)
    for index in range(10):
        (generic_dir / f"Generic{index}.lean").write_text(
            f"theorem generic_{index} : True := by\n"
            f"  have h : True := by trivial\n"
            f"  -- Fin.val noisy hit {index}\n"
            f"  trivial\n",
            encoding="utf-8",
        )
    precise = root / "Data" / "Nat" / "Mod.lean"
    precise.parent.mkdir(parents=True)
    precise.write_text("theorem Nat.mod_lt_demo : True := by\n  -- Nat.mod_lt\n  trivial\n", encoding="utf-8")

    results = MathlibSearcher([root]).search_many(["Fin.val", "Nat.mod_lt"], limit=3)

    assert any(result.query == "Nat.mod_lt" for result in results)


def test_parse_state_search_output_extracts_check_suggestions():
    output = """
info: Try these:
  • #check zero_lt_one -- ∀ {α : Type u_1}, (0 : α) < (1 : α)
    ⏎
  • #check one_pos -- ∀ {α : Type u_1}, (0 : α) < (1 : α)
    ⏎
"""

    results = parse_state_search_output(output)

    assert [result.name for result in results] == ["zero_lt_one", "one_pos"]
    assert results[0].content.startswith("#check zero_lt_one")


def test_write_retrieval_report(tmp_path):
    root = tmp_path / "Mathlib"
    path = root / "Demo.lean"
    path.parent.mkdir(parents=True)
    path.write_text("theorem demo : True := by trivial\n", encoding="utf-8")
    results = MathlibSearcher([root]).search("theorem demo", limit=1)

    write_retrieval_report(results, tmp_path)

    assert (tmp_path / "retrieval_results.json").exists()
    markdown = (tmp_path / "retrieval_results.md").read_text(encoding="utf-8")
    assert "theorem demo" in markdown
