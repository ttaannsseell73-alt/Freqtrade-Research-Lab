from pathlib import Path

import pandas as pd
import pytest

from freqtrade_research_lab.catalog_io import write_catalog_bundle


def sample_catalog() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "pair": "BTC/USDT:USDT",
                "timeframe": "1m",
                "research_ready": True,
                "status": "ready",
            }
        ]
    )


def test_catalog_writer_refuses_silent_overwrite(tmp_path: Path) -> None:
    existing = tmp_path / "dataset_catalog.csv"
    existing.write_text("sentinel\n", encoding="utf-8")

    with pytest.raises(FileExistsError, match="--replace"):
        write_catalog_bundle(
            sample_catalog(),
            {"schema_version": 2},
            tmp_path,
        )

    assert existing.read_text(encoding="utf-8") == "sentinel\n"


def test_catalog_writer_replaces_only_when_explicit(tmp_path: Path) -> None:
    (tmp_path / "dataset_catalog.csv").write_text("old\n", encoding="utf-8")
    (tmp_path / "dataset_catalog_summary.json").write_text(
        '{"old": true}',
        encoding="utf-8",
    )

    write_catalog_bundle(
        sample_catalog(),
        {"schema_version": 2, "timeframes": ["1m"]},
        tmp_path,
        replace=True,
    )

    reread = pd.read_csv(tmp_path / "dataset_catalog.csv")
    assert reread["pair"].tolist() == ["BTC/USDT:USDT"]
    assert '"schema_version": 2' in (
        tmp_path / "dataset_catalog_summary.json"
    ).read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".*.tmp"))
