from pathlib import Path

import pytest

from freqtrade_research_lab.execution_runner import (
    ExecutionRunConfig,
    _timerange,
    build_backtest_command,
    build_lookahead_command,
)


def config(tmp_path: Path, **overrides) -> ExecutionRunConfig:
    values = {
        "config_path": tmp_path / "config.json",
        "data_dir": tmp_path / "data",
        "catalog_path": tmp_path / "dataset_catalog.csv",
        "strategy_path": tmp_path / "strategies",
        "output_dir": tmp_path / "out",
        "strategy_name": "BenchmarkMacdExecution",
        "timeframe": "15m",
        "start": "2025-09-20",
        "end": "2026-09-20",
        "timeframe_detail": "1m",
    }
    values.update(overrides)
    return ExecutionRunConfig(**values)


def test_timerange_is_explicit_and_end_exclusive() -> None:
    assert _timerange("2025-09-20", "2026-09-20") == "20250920-20260920"
    with pytest.raises(ValueError, match="end must be later"):
        _timerange("2026-09-20", "2026-09-20")


def test_backtest_command_neutralizes_pair_slot_competition(tmp_path: Path) -> None:
    cfg = config(tmp_path)
    pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    command = build_backtest_command(cfg, pairs, tmp_path / "backtest")

    assert command[0:2] == ["freqtrade", "backtesting"]
    assert command[command.index("--max-open-trades") + 1] == "2"
    assert command[command.index("--dry-run-wallet") + 1] == "1000000000"
    assert command[command.index("--fee") + 1] == "0.0005"
    assert command[command.index("--timeframe-detail") + 1] == "1m"
    pair_index = command.index("--pairs")
    assert command[pair_index + 1 :] == pairs


def test_lookahead_command_is_catalog_pair_scoped(tmp_path: Path) -> None:
    cfg = config(tmp_path, timeframe="4h", timeframe_detail="15m")
    pairs = ["BTC/USDT:USDT"]
    export_csv = tmp_path / "lookahead.csv"
    command = build_lookahead_command(cfg, pairs, export_csv)

    assert command[0:2] == ["freqtrade", "lookahead-analysis"]
    assert command[command.index("--minimum-trade-amount") + 1] == "20"
    assert command[command.index("--targeted-trade-amount") + 1] == "100"
    assert command[command.index("--lookahead-analysis-exportfilename") + 1] == str(
        export_csv
    )
    assert command[command.index("--timeframe-detail") + 1] == "15m"


def test_execution_config_rejects_invalid_costs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="fee_per_side"):
        config(tmp_path, fee_per_side=-0.1)
    with pytest.raises(ValueError, match="slippage"):
        config(tmp_path, slippage_bps_round_trip=-1)
