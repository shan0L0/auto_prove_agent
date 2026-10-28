from pathlib import Path

from agent.dataset import prepare_dataset


def test_prepare_dataset_from_parquet(tmp_path):
    parquet = Path("train-00000-of-00001.parquet")
    if not parquet.exists():
        return

    written = prepare_dataset(parquet, tmp_path / "dataset")

    assert len(written) == 41
    assert (tmp_path / "dataset" / "0001" / "problem.md").exists()
    prove = (tmp_path / "dataset" / "0001" / "prove.lean").read_text(encoding="utf-8")
    assert prove.startswith("import Mathlib")
    assert "sorry" in prove
