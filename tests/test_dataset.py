from pathlib import Path

import pandas as pd

from freqtrade_research_lab.dataset import (
    assess_market_file,
    discover_market_files,
    load_ready_pairs_from_catalog,
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
