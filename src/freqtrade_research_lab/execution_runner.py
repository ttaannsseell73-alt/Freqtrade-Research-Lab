from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence

import pandas as pd

from .dataset import load_catalog_selection
from .execution_metrics import write_backtest_summary
from .run_io import prepare_fresh_output_dir


@dataclass(frozen=True)
class ExecutionRunConfig:
    config_path: Path
    data_dir: Path
    catalog_path: Path
    strategy_path: Path
    output_dir: Path
    strategy_name: str
    timeframe: str
    start: str
    end: str
    timeframe_detail: str | None = None
    fee_per_side: float = 0.0005
    slippage_bps_round_trip: float = 4.0
    stake_amount: float = 1000.0
    run_lookahead: bool = True
    minimum_trade_amount: int = 20
    targeted_trade_amount: int = 100
    max_pairs: int | None = None

    def __post_init__(self) -> None:
        if self.fee_per_side < 0:
            raise ValueError("fee_per_side cannot be negative")
        if self.slippage_bps_round_trip < 0:
            raise ValueError("slippage_bps_round_trip cannot be negative")
        if self.stake_amount <= 0:
            raise ValueError("stake_amount must be positive")
        if self.minimum_trade_amount <= 0 or self.targeted_trade_amount <= 0:
            raise ValueError("lookahead trade amounts must be positive")
        if self.max_pairs is not None and self.max_pairs <= 0:
            raise ValueError("max_pairs must be positive")


def _timerange(start: str, end: str) -> str:
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    if end_ts <= start_ts:
        raise ValueError("end must be later than start")
    return f"{start_ts.strftime('%Y%m%d')}-{end_ts.strftime('%Y%m%d')}"


def select_pairs(config: ExecutionRunConfig) -> list[str]:
    start = pd.Timestamp(config.start, tz="UTC")
    end = pd.Timestamp(config.end, tz="UTC")
    selection = load_catalog_selection(
        config.catalog_path,
        config.timeframe,
        start=start,
        end=end,
    )
    pairs = sorted(selection.pairs)
    if config.max_pairs is not None:
        pairs = pairs[: config.max_pairs]
    if not pairs:
        raise ValueError("Catalog selected no research-ready pairs")
    return pairs


def build_backtest_command(
    config: ExecutionRunConfig,
    pairs: Sequence[str],
    backtest_dir: Path,
) -> list[str]:
    command = [
        "freqtrade",
        "backtesting",
        "--config",
        str(config.config_path),
        "--data-dir",
        str(config.data_dir),
        "--strategy-path",
        str(config.strategy_path),
        "--strategy",
        config.strategy_name,
        "--timeframe",
        config.timeframe,
        "--timerange",
        _timerange(config.start, config.end),
        "--cache",
        "none",
        "--fee",
        str(config.fee_per_side),
        "--max-open-trades",
        str(len(pairs)),
        "--stake-amount",
        str(config.stake_amount),
        "--dry-run-wallet",
        "1000000000",
        "--export",
        "trades",
        "--backtest-directory",
        str(backtest_dir),
    ]
    if config.timeframe_detail:
        command.extend(["--timeframe-detail", config.timeframe_detail])
    command.extend(["--pairs", *pairs])
    return command


def build_lookahead_command(
    config: ExecutionRunConfig,
    pairs: Sequence[str],
    export_csv: Path,
) -> list[str]:
    command = [
        "freqtrade",
        "lookahead-analysis",
        "--config",
        str(config.config_path),
        "--data-dir",
        str(config.data_dir),
        "--strategy-path",
        str(config.strategy_path),
        "--strategy",
        config.strategy_name,
        "--timeframe",
        config.timeframe,
        "--timerange",
        _timerange(config.start, config.end),
        "--fee",
        str(config.fee_per_side),
        "--minimum-trade-amount",
        str(config.minimum_trade_amount),
        "--targeted-trade-amount",
        str(config.targeted_trade_amount),
        "--lookahead-analysis-exportfilename",
        str(export_csv),
    ]
    if config.timeframe_detail:
        command.extend(["--timeframe-detail", config.timeframe_detail])
    command.extend(["--pairs", *pairs])
    return command


def _run_and_tee(command: Sequence[str], log_path: Path) -> None:
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(
            f"Command failed with exit code {return_code}. See {log_path}"
        )


def _latest_backtest_zip(backtest_dir: Path) -> Path:
    archives = sorted(
        backtest_dir.glob("*.zip"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not archives:
        raise FileNotFoundError(
            f"Freqtrade produced no backtest ZIP in {backtest_dir}"
        )
    return archives[-1]


def run_execution_benchmark(config: ExecutionRunConfig) -> dict[str, object]:
    prepare_fresh_output_dir(config.output_dir)
    backtest_dir = config.output_dir / "backtest"
    metrics_dir = config.output_dir / "metrics"
    backtest_dir.mkdir()
    metrics_dir.mkdir()

    pairs = select_pairs(config)
    pairs_text = "\n".join(pairs) + "\n"
    (config.output_dir / "pairs.txt").write_text(pairs_text, encoding="utf-8")
    pair_fingerprint = hashlib.sha256(pairs_text.encode("utf-8")).hexdigest()

    backtest_command = build_backtest_command(config, pairs, backtest_dir)
    _run_and_tee(backtest_command, config.output_dir / "backtest.log")
    archive = _latest_backtest_zip(backtest_dir)

    overall = write_backtest_summary(
        archive,
        metrics_dir,
        strategy_name=config.strategy_name,
        slippage_bps_round_trip=config.slippage_bps_round_trip,
    )

    lookahead_csv: str | None = None
    if config.run_lookahead:
        target = config.output_dir / "lookahead.csv"
        lookahead_command = build_lookahead_command(config, pairs, target)
        _run_and_tee(lookahead_command, config.output_dir / "lookahead.log")
        lookahead_csv = str(target)

    manifest = {
        "schema_version": 1,
        "study": "freqtrade_execution_benchmark",
        "strategy": config.strategy_name,
        "timeframe": config.timeframe,
        "timeframe_detail": config.timeframe_detail,
        "start_inclusive": pd.Timestamp(config.start, tz="UTC").isoformat(),
        "end_exclusive": pd.Timestamp(config.end, tz="UTC").isoformat(),
        "catalog": str(config.catalog_path),
        "pair_count": len(pairs),
        "pair_fingerprint": pair_fingerprint,
        "fee_per_side": config.fee_per_side,
        "fee_round_trip_bps": config.fee_per_side * 2 * 10_000,
        "post_backtest_slippage_bps_round_trip": config.slippage_bps_round_trip,
        "stake_amount": config.stake_amount,
        "max_open_trades": len(pairs),
        "dry_run_wallet": 1_000_000_000,
        "backtest_result_zip": str(archive),
        "lookahead_csv": lookahead_csv,
        "overall_metrics": overall,
        "notes": [
            "Freqtrade fee is applied on entry and exit.",
            "Additional slippage is applied post-backtest to exported trade returns.",
            "Historical futures funding is handled by Freqtrade when funding data is available.",
            "Portfolio slot and wallet competition are neutralized with max_open_trades=pair_count and a large static wallet.",
            "Per-pair drawdown is computed from each pair's sequential trades; Freqtrade remains the source for portfolio-level drawdown.",
        ],
    }
    (config.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    return manifest
