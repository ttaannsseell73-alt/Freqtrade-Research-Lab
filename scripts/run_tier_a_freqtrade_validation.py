from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pandas as pd

from freqtrade_research_lab.execution_metrics import write_backtest_summary


def _timerange(start: str, end: str) -> str:
    return f"{start.replace('-', '')}-{end.replace('-', '')}"


def _latest_zip(path: Path) -> Path:
    archives = sorted(
        path.glob("*.zip"),
        key=lambda p: p.stat().st_mtime_ns,
    )
    if not archives:
        raise FileNotFoundError(f"No Freqtrade backtest ZIP in {path}")
    return archives[-1]


def _run(command: list[str], log_path: Path) -> int:
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            log.write(line)
        return process.wait()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--strategy-path", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--fee-per-side", type=float, default=0.0005)
    parser.add_argument("--slippage-bps-round-trip", type=float, default=5.0)
    args = parser.parse_args()

    plan = pd.read_csv(args.plan)
    required = {
        "freqtrade_pair","timeframe","strategy_id","freqtrade_strategy_class",
        "validation_start","validation_end","confidence_score",
    }
    missing = required - set(plan.columns)
    if missing:
        raise ValueError(f"Execution plan missing columns: {sorted(missing)}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    failures: list[dict] = []

    grouped = plan.groupby(
        ["timeframe","strategy_id","freqtrade_strategy_class"],
        sort=True,
    )
    for group_idx, ((timeframe, strategy_id, strategy_class), group) in enumerate(grouped, 1):
        group = group.sort_values("confidence_score", ascending=False)
        pairs = group["freqtrade_pair"].tolist()
        start = str(group["validation_start"].iloc[0])
        end = str(group["validation_end"].iloc[0])
        group_name = f"{timeframe}-{strategy_id}".replace("/", "_")
        group_dir = args.output_dir / group_name
        backtest_dir = group_dir / "backtest"
        metrics_dir = group_dir / "metrics"
        group_dir.mkdir(parents=True, exist_ok=True)
        backtest_dir.mkdir(parents=True, exist_ok=True)

        command = [
            "freqtrade",
            "backtesting",
            "--config", str(args.config),
            "--data-dir", str(args.data_dir),
            "--strategy-path", str(args.strategy_path),
            "--strategy", str(strategy_class),
            "--timeframe", str(timeframe),
            "--timerange", _timerange(start, end),
            "--cache", "none",
            "--fee", str(args.fee_per_side),
            "--max-open-trades", str(max(1, len(pairs))),
            "--stake-amount", "1000",
            "--dry-run-wallet", "1000000000",
            "--export", "trades",
            "--backtest-directory", str(backtest_dir),
            "--pairs", *pairs,
        ]
        (group_dir / "command.json").write_text(
            json.dumps(command, indent=2),
            encoding="utf-8",
        )
        print(
            f"[group {group_idx}/{grouped.ngroups}] "
            f"{timeframe} {strategy_id} pairs={len(pairs)}",
            flush=True,
        )
        rc = _run(command, group_dir / "backtest.log")
        if rc != 0:
            failures.append(
                {
                    "timeframe": timeframe,
                    "strategy_id": strategy_id,
                    "strategy_class": strategy_class,
                    "pair_count": len(pairs),
                    "return_code": rc,
                    "log": str(group_dir / "backtest.log"),
                }
            )
            continue

        try:
            archive = _latest_zip(backtest_dir)
            overall = write_backtest_summary(
                archive,
                metrics_dir,
                strategy_name=str(strategy_class),
                slippage_bps_round_trip=args.slippage_bps_round_trip,
            )
        except Exception as exc:
            failures.append(
                {
                    "timeframe": timeframe,
                    "strategy_id": strategy_id,
                    "strategy_class": strategy_class,
                    "pair_count": len(pairs),
                    "return_code": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        rows.append(
            {
                "timeframe": timeframe,
                "strategy_id": strategy_id,
                "strategy_class": strategy_class,
                "pair_count": len(pairs),
                "trades": int(overall.get("trades", 0)),
                "win_rate": overall.get("win_rate"),
                "expectancy": overall.get("expectancy"),
                "profit_factor": overall.get("profit_factor"),
                "avg_duration_minutes": overall.get("avg_duration_minutes"),
                "long_trades": overall.get("long_trades"),
                "short_trades": overall.get("short_trades"),
                "funding_fees_sum": overall.get("funding_fees_sum"),
                "fee_round_trip_bps": args.fee_per_side * 2 * 10_000,
                "post_slippage_bps_round_trip": args.slippage_bps_round_trip,
                "status": (
                    "EXECUTION_POSITIVE"
                    if int(overall.get("trades", 0)) > 0
                    and float(overall.get("expectancy", 0.0)) > 0
                    and float(overall.get("profit_factor", 0.0)) > 1.0
                    else "EXECUTION_FAIL"
                ),
            }
        )

    summary = pd.DataFrame(rows)
    summary.to_csv(args.output_dir / "execution_summary.csv", index=False)
    (args.output_dir / "failures.json").write_text(
        json.dumps(failures, indent=2),
        encoding="utf-8",
    )

    manifest = {
        "schema_version": 1,
        "engine": "freqtrade",
        "fee_per_side": args.fee_per_side,
        "fee_round_trip_bps": args.fee_per_side * 2 * 10_000,
        "post_slippage_bps_round_trip": args.slippage_bps_round_trip,
        "group_count": int(grouped.ngroups),
        "completed_groups": int(len(summary)),
        "failed_groups": int(len(failures)),
        "positive_groups": int(
            (summary["status"] == "EXECUTION_POSITIVE").sum()
        ) if not summary.empty else 0,
    }
    (args.output_dir / "execution_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(manifest, indent=2), flush=True)
    if failures:
        raise SystemExit(
            f"{len(failures)} Freqtrade execution groups failed; inspect failures.json"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
