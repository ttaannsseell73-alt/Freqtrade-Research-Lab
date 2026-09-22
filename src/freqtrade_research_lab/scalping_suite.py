from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import pandas as pd

from .execution_metrics import load_backtest_report, summarize_directions
from .execution_runner import ExecutionRunConfig, run_execution_benchmark, select_pairs
from .run_io import prepare_fresh_output_dir


DEFAULT_STRATEGIES = (
    "LiquiditySweepReclaimScalp",
    "BreakoutRetestScalp",
    "CompressionExpansionScalp",
    "BosChochScalp",
    "TurtleTradeChannelsScalp",
    "IsolatedPeakBottomScalp",
    "VolumeBasedColouredBarsScalp",
    "FollowLineScalp",
    "SqueezeMomentumV2Scalp",
    "ProgressiveTrendTrackerScalp",
    "TurtleVhfFilteredScalp",
)


@dataclass(frozen=True)
class ScalpingSuiteConfig:
    config_path: Path
    data_dir: Path
    catalog_1m: Path
    catalog_5m: Path
    strategy_path: Path
    output_dir: Path
    start: str
    end: str
    reuse_root: Path | None = None
    strategies: tuple[str, ...] = DEFAULT_STRATEGIES
    fee_per_side: float = 0.0005
    slippage_bps_round_trip: float = 4.0
    stake_amount: float = 1000.0
    max_pairs: int = 5
    pair_offset: int = 0
    run_lookahead: bool = False

    def __post_init__(self) -> None:
        if not self.strategies:
            raise ValueError("strategies cannot be empty")
        if self.max_pairs <= 0:
            raise ValueError("max_pairs must be positive")
        if self.pair_offset < 0:
            raise ValueError("pair_offset cannot be negative")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _catalog_for(config: ScalpingSuiteConfig, timeframe: str) -> Path:
    if timeframe == "1m":
        return config.catalog_1m
    if timeframe == "5m":
        return config.catalog_5m
    raise ValueError(f"Unsupported suite timeframe: {timeframe}")


def _execution_config(
    suite: ScalpingSuiteConfig,
    *,
    strategy: str,
    timeframe: str,
    output_dir: Path,
) -> ExecutionRunConfig:
    return ExecutionRunConfig(
        config_path=suite.config_path,
        data_dir=suite.data_dir,
        catalog_path=_catalog_for(suite, timeframe),
        strategy_path=suite.strategy_path,
        output_dir=output_dir,
        strategy_name=strategy,
        timeframe=timeframe,
        start=suite.start,
        end=suite.end,
        fee_per_side=suite.fee_per_side,
        slippage_bps_round_trip=suite.slippage_bps_round_trip,
        stake_amount=suite.stake_amount,
        run_lookahead=suite.run_lookahead,
        max_pairs=suite.max_pairs,
        pair_offset=suite.pair_offset,
    )


def _manifest_matches(
    manifest: dict[str, object],
    *,
    run_config: ExecutionRunConfig,
    expected_pairs: list[str],
    strategy_sha256: str,
) -> bool:
    expected_start = pd.Timestamp(run_config.start, tz="UTC").isoformat()
    expected_end = pd.Timestamp(run_config.end, tz="UTC").isoformat()
    checks = (
        manifest.get("strategy") == run_config.strategy_name,
        manifest.get("strategy_sha256") == strategy_sha256,
        manifest.get("timeframe") == run_config.timeframe,
        manifest.get("start_inclusive") == expected_start,
        manifest.get("end_exclusive") == expected_end,
        int(manifest.get("pair_count", -1)) == len(expected_pairs),
        int(manifest.get("pair_offset", 0)) == run_config.pair_offset,
        float(manifest.get("fee_per_side", -1.0)) == run_config.fee_per_side,
        float(manifest.get("post_backtest_slippage_bps_round_trip", -1.0))
        == run_config.slippage_bps_round_trip,
    )
    return all(checks)


def _find_reusable_run(
    reuse_root: Path | None,
    *,
    run_config: ExecutionRunConfig,
    expected_pairs: list[str],
) -> tuple[Path, dict[str, object]] | None:
    if reuse_root is None or not reuse_root.is_dir():
        return None

    strategy_file = run_config.strategy_path / f"{run_config.strategy_name}.py"
    strategy_sha256 = _sha256(strategy_file)
    for manifest_path in sorted(reuse_root.rglob("run_manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        if not _manifest_matches(
            manifest,
            run_config=run_config,
            expected_pairs=expected_pairs,
            strategy_sha256=strategy_sha256,
        ):
            continue
        pairs_path = manifest_path.parent / "pairs.txt"
        if not pairs_path.is_file():
            continue
        pairs = [
            line.strip()
            for line in pairs_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if pairs != expected_pairs:
            continue
        backtest_dir = manifest_path.parent / "backtest"
        if not any(backtest_dir.glob("*.zip")):
            continue
        return manifest_path.parent, manifest
    return None


def _direction_aggregate(
    run_dir: Path,
    *,
    strategy: str,
    slippage_bps_round_trip: float,
) -> dict[str, dict[str, object]]:
    direction_path = run_dir / "metrics" / "direction_metrics.csv"
    if direction_path.is_file():
        directions = pd.read_csv(direction_path)
    else:
        archives = sorted(
            (run_dir / "backtest").glob("*.zip"),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if not archives:
            return {}
        report = load_backtest_report(archives[-1], strategy_name=strategy)
        directions = summarize_directions(
            report,
            slippage_bps_round_trip=slippage_bps_round_trip,
        )

    if directions.empty:
        return {}

    aggregate = directions.loc[directions["pair"] == "__ALL__"]
    result: dict[str, dict[str, object]] = {}
    for _, row in aggregate.iterrows():
        result[str(row["direction"])] = row.to_dict()
    return result


def _status(overall: dict[str, object]) -> str:
    trades = int(overall.get("trades", 0))
    if trades <= 0:
        return "NO_TRADES"
    expectancy = float(overall.get("expectancy", float("nan")))
    profit_factor = float(overall.get("profit_factor", float("nan")))
    if expectancy > 0 and profit_factor > 1:
        return "POSITIVE_SMOKE"
    return "NEGATIVE_SMOKE"


def _summary_row(
    *,
    strategy: str,
    timeframe: str,
    source: str,
    run_dir: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    overall_raw = manifest.get("overall_metrics", {})
    overall = overall_raw if isinstance(overall_raw, dict) else {}
    directions = _direction_aggregate(
        run_dir,
        strategy=strategy,
        slippage_bps_round_trip=float(
            manifest.get("post_backtest_slippage_bps_round_trip", 4.0)
        ),
    )
    long = directions.get("long", {})
    short = directions.get("short", {})
    return {
        "strategy": strategy,
        "timeframe": timeframe,
        "source": source,
        "trades": int(overall.get("trades", 0)),
        "win_rate": overall.get("win_rate"),
        "expectancy": overall.get("expectancy"),
        "profit_factor": overall.get("profit_factor"),
        "avg_duration_minutes": overall.get("avg_duration_minutes"),
        "long_trades": int(long.get("trades", 0)) if long else 0,
        "long_expectancy": long.get("expectancy"),
        "long_profit_factor": long.get("profit_factor"),
        "short_trades": int(short.get("trades", 0)) if short else 0,
        "short_expectancy": short.get("expectancy"),
        "short_profit_factor": short.get("profit_factor"),
        "status": _status(overall),
        "run_dir": str(run_dir),
    }


def _fmt_pct(value: object) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(numeric):
        return "-"
    return f"{numeric * 100:.4f}%"


def _fmt_num(value: object, digits: int = 3) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(numeric):
        return "-"
    return f"{numeric:.{digits}f}"


def _write_markdown_report(
    summary: pd.DataFrame,
    output_path: Path,
    *,
    config: ScalpingSuiteConfig,
    errors: list[dict[str, str]],
) -> None:
    lines = [
        "# Scalping Benchmark Suite Report",
        "",
        f"Window: {config.start} -> {config.end}",
        f"Pairs per run: {config.max_pairs} (offset {config.pair_offset})",
        (
            f"Costs: {config.fee_per_side * 2 * 10_000:.1f} bps round-trip fee "
            f"+ {config.slippage_bps_round_trip:.1f} bps post-backtest slippage"
        ),
        "",
        "Smoke status is diagnostic only. POSITIVE_SMOKE is not a production promotion.",
        "",
        "| Strategy | TF | Source | Trades | PF | Exp/trade | Win | Long PF / Exp | Short PF / Exp | Status |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for _, row in summary.iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["strategy"]),
                    str(row["timeframe"]),
                    str(row["source"]),
                    str(int(row["trades"])),
                    _fmt_num(row["profit_factor"]),
                    _fmt_pct(row["expectancy"]),
                    _fmt_pct(row["win_rate"]),
                    f"{_fmt_num(row['long_profit_factor'])} / {_fmt_pct(row['long_expectancy'])}",
                    f"{_fmt_num(row['short_profit_factor'])} / {_fmt_pct(row['short_expectancy'])}",
                    str(row["status"]),
                ]
            )
            + " |"
        )

    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors:
            lines.append(
                f"- {error['strategy']} {error['timeframe']}: {error['error']}"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "NEGATIVE_SMOKE means fee + configured slippage adjusted expectancy is not positive or PF is not above 1.",
            "A POSITIVE_SMOKE result must still pass lookahead checks, broader-universe testing, walk-forward validation, and paper trading before any promotion.",
            "",
        ]
    )
    output_path.write_text("\n".join(lines), encoding="utf-8")


def run_scalping_suite(config: ScalpingSuiteConfig) -> dict[str, object]:
    prepare_fresh_output_dir(config.output_dir)
    runs_dir = config.output_dir / "runs"
    runs_dir.mkdir()

    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    for strategy in config.strategies:
        for timeframe in ("1m", "5m"):
            target = runs_dir / f"{strategy}_{timeframe}"
            run_config = _execution_config(
                config,
                strategy=strategy,
                timeframe=timeframe,
                output_dir=target,
            )
            try:
                expected_pairs = select_pairs(run_config)
                reusable = _find_reusable_run(
                    config.reuse_root,
                    run_config=run_config,
                    expected_pairs=expected_pairs,
                )
                if reusable is not None:
                    run_dir, manifest = reusable
                    source = "reused"
                else:
                    manifest = run_execution_benchmark(run_config)
                    run_dir = target
                    source = "executed"

                rows.append(
                    _summary_row(
                        strategy=strategy,
                        timeframe=timeframe,
                        source=source,
                        run_dir=run_dir,
                        manifest=manifest,
                    )
                )
            except Exception as exc:
                errors.append(
                    {
                        "strategy": strategy,
                        "timeframe": timeframe,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )

    summary = pd.DataFrame(rows)
    summary.to_csv(config.output_dir / "suite_summary.csv", index=False)
    _write_markdown_report(
        summary,
        config.output_dir / "SCALPING_SUITE_REPORT.md",
        config=config,
        errors=errors,
    )

    suite_manifest = {
        "schema_version": 1,
        "study": "scalping_benchmark_suite",
        "strategies": list(config.strategies),
        "timeframes": ["1m", "5m"],
        "start": config.start,
        "end": config.end,
        "max_pairs": config.max_pairs,
        "pair_offset": config.pair_offset,
        "fee_per_side": config.fee_per_side,
        "slippage_bps_round_trip": config.slippage_bps_round_trip,
        "lookahead_enabled": config.run_lookahead,
        "completed_runs": int(len(rows)),
        "errors": errors,
    }
    (config.output_dir / "suite_manifest.json").write_text(
        json.dumps(suite_manifest, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    return suite_manifest
