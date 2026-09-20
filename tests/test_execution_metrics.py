import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np

from freqtrade_research_lab.execution_metrics import (
    load_backtest_report,
    summarize_backtest,
    write_backtest_summary,
)


def _write_backtest_zip(path: Path) -> None:
    payload = {
        "strategy": {
            "BenchmarkMacdExecution": {
                "trades": [
                    {
                        "pair": "BTC/USDT:USDT",
                        "profit_ratio": 0.0100,
                        "is_short": False,
                        "open_date": "2026-01-01T00:00:00Z",
                        "close_date": "2026-01-01T01:00:00Z",
                        "exit_reason": "exit_signal",
                        "funding_fees": -0.10,
                    },
                    {
                        "pair": "BTC/USDT:USDT",
                        "profit_ratio": -0.0050,
                        "is_short": True,
                        "open_date": "2026-01-02T00:00:00Z",
                        "close_date": "2026-01-02T02:00:00Z",
                        "exit_reason": "exit_signal",
                        "funding_fees": 0.05,
                    },
                    {
                        "pair": "ETH/USDT:USDT",
                        "profit_ratio": 0.0200,
                        "is_short": False,
                        "open_date": "2026-01-01T00:00:00Z",
                        "close_date": "2026-01-01T03:00:00Z",
                        "exit_reason": "exit_signal",
                        "funding_fees": 0.00,
                    },
                ]
            }
        }
    }
    with ZipFile(path, "w") as archive:
        archive.writestr("backtest-report.json", json.dumps(payload))
        archive.writestr("config.json", json.dumps({"dry_run": True}))


def test_load_backtest_report_finds_strategy_json(tmp_path: Path) -> None:
    archive = tmp_path / "result.zip"
    _write_backtest_zip(archive)

    report = load_backtest_report(
        archive,
        strategy_name="BenchmarkMacdExecution",
    )

    assert report.strategy_name == "BenchmarkMacdExecution"
    assert len(report.trades) == 3


def test_summary_applies_round_trip_slippage_and_pair_metrics(tmp_path: Path) -> None:
    archive = tmp_path / "result.zip"
    _write_backtest_zip(archive)
    report = load_backtest_report(archive, strategy_name="BenchmarkMacdExecution")

    pair_metrics, overall, exits = summarize_backtest(
        report,
        slippage_bps_round_trip=4.0,
    )

    btc = pair_metrics.loc[pair_metrics["pair"] == "BTC/USDT:USDT"].iloc[0]
    # 1.00% and -0.50% become +0.96% and -0.54% after 4 bps RT slippage.
    assert abs(btc["expectancy"] - 0.0021) < 1e-12
    assert abs(btc["profit_factor"] - (0.0096 / 0.0054)) < 1e-12
    assert btc["long_trades"] == 1
    assert btc["short_trades"] == 1
    assert abs(btc["funding_fees_sum"] - (-0.05)) < 1e-12
    assert btc["max_drawdown_compounded"] < 0

    assert overall["trades"] == 3
    assert overall["slippage_bps_round_trip"] == 4.0
    assert "max_drawdown_compounded" not in overall
    assert len(exits) == 1


def test_write_backtest_summary_outputs_canonical_files(tmp_path: Path) -> None:
    archive = tmp_path / "result.zip"
    _write_backtest_zip(archive)
    output = tmp_path / "summary"

    overall = write_backtest_summary(
        archive,
        output,
        strategy_name="BenchmarkMacdExecution",
        slippage_bps_round_trip=4.0,
    )

    assert overall["trades"] == 3
    assert (output / "pair_metrics.csv").is_file()
    assert (output / "exit_reason_metrics.csv").is_file()
    assert (output / "overall_metrics.json").is_file()


def test_empty_report_is_supported(tmp_path: Path) -> None:
    archive = tmp_path / "empty.zip"
    with ZipFile(archive, "w") as z:
        z.writestr(
            "report.json",
            json.dumps({"strategy": {"BenchmarkMacdExecution": {"trades": []}}}),
        )

    report = load_backtest_report(archive, strategy_name="BenchmarkMacdExecution")
    pair_metrics, overall, exits = summarize_backtest(report)

    assert pair_metrics.empty
    assert exits.empty
    assert overall["trades"] == 0
