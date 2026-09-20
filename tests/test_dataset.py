import json
from pathlib import Path

import pandas as pd

from freqtrade_research_lab.dataset import (
    assess_market_file,
    discover_market_files,
    load_catalog_selection,
    load_ready_pairs_from_catalog,
    sha256_file,
    parse_market_file,
    timeframe_delta,
)


def test_parse_freqtrade_futures_filename() -> None:
    item = parse_market_file(Path("BTC_USDT_USDT-1m-futures.feather"))
    assert item is not None
    assert item.pair == "BTC/USDT:USDT"
    assert item.timeframe == "1m"


def test_discovery_ignores_mark_and_other_timeframes(tmp_path: Path) -> None:
    for name in (
        "BTC_USDT_USDT-1m-futures.feather",
        "ETH_USDT_USDT-1m-futures.feather",
        "BTC_USDT_USDT-5m-futures.feather",
        "BTC_USDT_USDT-1h-mark.feather",
    ):
        (tmp_path / name).touch()
    found = discover_market_files(tmp_path, "1m")
    assert [item.pair for item in found] == ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def test_timeframe_delta_supports_research_timeframes() -> None:
    assert timeframe_delta("1m").total_seconds() == 60
    assert timeframe_delta("4h").total_seconds() == 14_400
    assert timeframe_delta("1d").total_seconds() == 86_400


def test_assess_market_file_marks_complete_data_ready(tmp_path: Path) -> None:
    path = tmp_path / "BTC_USDT_USDT-1m-futures.feather"
    dates = pd.date_range("2026-01-01", periods=10, freq="min", tz="UTC")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1.0,
        }
    )
    frame.to_feather(path)
    item = parse_market_file(path)
    assert item is not None
    result = assess_market_file(
        item,
        pd.Timestamp("2026-01-01", tz="UTC"),
        pd.Timestamp("2026-01-01 00:10", tz="UTC"),
        minimum_coverage=0.9,
        minimum_candles=10,
    )
    assert result["research_ready"] is True
    assert result["coverage_ratio"] == 1.0
    assert result["gap_count"] == 0
    assert result["status"] == "ready"
    assert result["source_sha256"] == sha256_file(path)
    assert len(str(result["source_sha256"])) == 64


def test_load_ready_pairs_from_catalog_filters_timeframe_and_readiness(tmp_path: Path) -> None:
    path = tmp_path / "dataset_catalog.csv"
    pd.DataFrame(
        [
            {"pair": "BTC/USDT:USDT", "timeframe": "1m", "research_ready": True},
            {"pair": "ETH/USDT:USDT", "timeframe": "1m", "research_ready": False},
            {"pair": "BTC/USDT:USDT", "timeframe": "15m", "research_ready": True},
        ]
    ).to_csv(path, index=False)

    ready = load_ready_pairs_from_catalog(path, "1m")
    assert ready == {"BTC/USDT:USDT"}



def test_catalog_selection_validates_range_and_uses_source_hashes(tmp_path: Path) -> None:
    catalog_path = tmp_path / "dataset_catalog.csv"
    source_hash = "1" * 64
    pd.DataFrame(
        [
            {
                "pair": "BTC/USDT:USDT",
                "timeframe": "1m",
                "research_ready": True,
                "source_sha256": source_hash,
            },
            {
                "pair": "ETH/USDT:USDT",
                "timeframe": "1m",
                "research_ready": False,
                "source_sha256": "2" * 64,
            },
        ]
    ).to_csv(catalog_path, index=False)
    (tmp_path / "dataset_catalog_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "start_inclusive": "2025-09-20T00:00:00+00:00",
                "end_exclusive": "2026-09-20T00:00:00+00:00",
                "timeframes": ["1m"],
            }
        ),
        encoding="utf-8",
    )

    selection = load_catalog_selection(
        catalog_path,
        "1m",
        start=pd.Timestamp("2025-09-20", tz="UTC"),
        end=pd.Timestamp("2026-09-20", tz="UTC"),
    )

    assert selection.pairs == frozenset({"BTC/USDT:USDT"})
    assert selection.fingerprint_basis == "source_sha256_v1"
    assert len(selection.fingerprint_for_pairs()) == 64


def test_catalog_selection_rejects_wrong_study_range(tmp_path: Path) -> None:
    catalog_path = tmp_path / "dataset_catalog.csv"
    pd.DataFrame(
        [
            {
                "pair": "BTC/USDT:USDT",
                "timeframe": "1m",
                "research_ready": True,
            }
        ]
    ).to_csv(catalog_path, index=False)
    (tmp_path / "dataset_catalog_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "start_inclusive": "2025-09-20T00:00:00+00:00",
                "end_exclusive": "2026-09-20T00:00:00+00:00",
                "timeframes": ["1m"],
            }
        ),
        encoding="utf-8",
    )

    import pytest

    with pytest.raises(ValueError, match="date range"):
        load_catalog_selection(
            catalog_path,
            "1m",
            start=pd.Timestamp("2025-09-21", tz="UTC"),
            end=pd.Timestamp("2026-09-20", tz="UTC"),
        )


def test_legacy_catalog_uses_metadata_fingerprint_fallback(tmp_path: Path) -> None:
    catalog_path = tmp_path / "dataset_catalog.csv"
    pd.DataFrame(
        [
            {
                "pair": "BTC/USDT:USDT",
                "timeframe": "1m",
                "research_ready": True,
                "file": "BTC_USDT_USDT-1m-futures.feather",
                "size_bytes": 1234,
                "candles": 1000,
            }
        ]
    ).to_csv(catalog_path, index=False)
    (tmp_path / "dataset_catalog_summary.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "start_inclusive": "2025-09-20T00:00:00+00:00",
                "end_exclusive": "2026-09-20T00:00:00+00:00",
                "timeframes": ["1m"],
            }
        ),
        encoding="utf-8",
    )

    selection = load_catalog_selection(
        catalog_path,
        "1m",
        start=pd.Timestamp("2025-09-20", tz="UTC"),
        end=pd.Timestamp("2026-09-20", tz="UTC"),
    )
    assert selection.fingerprint_basis == "catalog_metadata_fallback_v1"
    assert len(selection.fingerprint_for_pairs()) == 64
