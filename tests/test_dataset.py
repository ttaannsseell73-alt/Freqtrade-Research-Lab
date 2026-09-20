from pathlib import Path

from freqtrade_research_lab.dataset import discover_market_files, parse_market_file


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

