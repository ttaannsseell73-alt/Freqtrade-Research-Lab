from pathlib import Path

import pytest

from freqtrade_research_lab.run_io import prepare_fresh_output_dir


def test_prepare_fresh_output_dir_creates_missing_directory(tmp_path: Path) -> None:
    target = tmp_path / "run"
    prepare_fresh_output_dir(target)
    assert target.is_dir()


def test_prepare_fresh_output_dir_allows_existing_empty_directory(tmp_path: Path) -> None:
    target = tmp_path / "run"
    target.mkdir()
    prepare_fresh_output_dir(target)
    assert target.is_dir()


def test_prepare_fresh_output_dir_refuses_nonempty_directory(tmp_path: Path) -> None:
    target = tmp_path / "run"
    target.mkdir()
    (target / "old.csv").write_text("old", encoding="utf-8")
    with pytest.raises(FileExistsError, match="fresh run directory"):
        prepare_fresh_output_dir(target)
    assert (target / "old.csv").read_text(encoding="utf-8") == "old"
