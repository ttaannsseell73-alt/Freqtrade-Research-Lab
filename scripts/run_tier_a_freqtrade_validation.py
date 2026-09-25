from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pandas as pd

from freqtrade_research_lab.execution_metrics import write_backtest_summary


MIN_TRADES_BY_TIMEFRAME = {
    "5m": 100,
    "15m": 50,
    "1h": 30,
    "4h": 20,
    "1d": 15,
}


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
    pair_rows: list[dict] = []
    direction_rows: list[dict] = []
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
            sys.executable,
            "scripts/freqtrade_offline_backtest.py",
            "--config", str(args.config),
            "--data-dir", str(args.data_dir),
            "--strategy-path", str(args.strategy_path),
            "--strategy", str(strategy_class),
            "--timeframe", str(timeframe),
            "--timerange", _timerange(start, end),
            "--output-dir", str(backtest_dir),
            "--fee", str(args.fee_per_side),
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

        pair_metrics = pd.read_csv(metrics_dir / "pair_metrics.csv")
        directions = pd.read_csv(metrics_dir / "direction_metrics.csv")
        plan_lookup = group.set_index("freqtrade_pair")

        direction_pair = directions[directions["pair"] != "__ALL__"].copy()
        direction_pivot: dict[str, dict[str, float]] = {}
        for _, drow in direction_pair.iterrows():
            pair_key = str(drow["pair"])
            direction = str(drow["direction"])
            direction_pivot.setdefault(pair_key, {})
            direction_pivot[pair_key][f"{direction}_trades"] = int(drow["trades"])
            direction_pivot[pair_key][f"{direction}_expectancy"] = float(drow["expectancy"])
            direction_pivot[pair_key][f"{direction}_profit_factor"] = float(drow["profit_factor"])
            direction_rows.append(
                {
                    "pair": pair_key,
                    "timeframe": timeframe,
                    "strategy_id": strategy_id,
                    "direction": direction,
                    "trades": int(drow["trades"]),
                    "expectancy": float(drow["expectancy"]),
                    "profit_factor": float(drow["profit_factor"]),
                    "win_rate": float(drow["win_rate"]),
                    "max_drawdown_compounded": float(drow["max_drawdown_compounded"]),
                }
            )

        for _, prow in pair_metrics.iterrows():
            pair_key = str(prow["pair"])
            if pair_key not in plan_lookup.index:
                continue
            plan_row = plan_lookup.loc[pair_key]
            min_trades = MIN_TRADES_BY_TIMEFRAME[str(timeframe)]
            trades = int(prow["trades"])
            expectancy = float(prow["expectancy"])
            profit_factor = float(prow["profit_factor"])
            max_dd = float(prow["max_drawdown_compounded"])
            dvals = direction_pivot.get(pair_key, {})
            long_exp = dvals.get("long_expectancy")
            short_exp = dvals.get("short_expectancy")

            sample_ok = trades >= min_trades
            edge_ok = expectancy > 0.0 and profit_factor > 1.0
            both_directions_positive = (
                long_exp is not None
                and short_exp is not None
                and long_exp > 0.0
                and short_exp > 0.0
            )
            drawdown_warning = max_dd < -0.35
            pair_status = (
                "EXECUTION_PASS"
                if sample_ok and edge_ok
                else "EXECUTION_REJECT"
            )

            pair_rows.append(
                {
                    "symbol": str(plan_row["symbol"]),
                    "pair": pair_key,
                    "timeframe": timeframe,
                    "strategy_id": strategy_id,
                    "strategy_class": strategy_class,
                    "robust_tier": str(plan_row["robust_tier"]),
                    "research_confidence_score": float(plan_row["confidence_score"]),
                    "research_worst_oos_floor_bps": float(plan_row["worst_oos_floor_bps"]),
                    "research_worst_15bps_expectancy": float(plan_row["worst_15bps_expectancy"]),
                    "research_worst_holdout_profit_factor": float(
                        plan_row["worst_holdout_profit_factor"]
                    ),
                    "trades": trades,
                    "min_trades_required": min_trades,
                    "sample_ok": sample_ok,
                    "win_rate": float(prow["win_rate"]),
                    "expectancy": expectancy,
                    "expectancy_bps": expectancy * 10_000.0,
                    "profit_factor": profit_factor,
                    "sum_adjusted_returns": float(prow["sum_adjusted_returns"]),
                    "max_drawdown_compounded": max_dd,
                    "drawdown_warning_gt_35pct": drawdown_warning,
                    "avg_duration_minutes": float(prow["avg_duration_minutes"]),
                    "long_trades": int(prow["long_trades"]),
                    "short_trades": int(prow["short_trades"]),
                    "long_expectancy": long_exp,
                    "short_expectancy": short_exp,
                    "both_directions_positive": both_directions_positive,
                    "funding_fees_sum": float(prow["funding_fees_sum"]),
                    "fee_round_trip_bps": args.fee_per_side * 2 * 10_000,
                    "post_slippage_bps_round_trip": args.slippage_bps_round_trip,
                    "status": pair_status,
                }
            )

    summary = pd.DataFrame(rows)
    pair_summary = pd.DataFrame(pair_rows)
    direction_summary = pd.DataFrame(direction_rows)
    summary.to_csv(args.output_dir / "execution_summary.csv", index=False)
    pair_summary.to_csv(args.output_dir / "execution_pair_results.csv", index=False)
    direction_summary.to_csv(args.output_dir / "execution_direction_results.csv", index=False)
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
        "pair_count": int(len(pair_summary)),
        "execution_pass_pairs": int(
            (pair_summary["status"] == "EXECUTION_PASS").sum()
        ) if not pair_summary.empty else 0,
        "execution_reject_pairs": int(
            (pair_summary["status"] == "EXECUTION_REJECT").sum()
        ) if not pair_summary.empty else 0,
        "both_directions_positive_pairs": int(
            pair_summary["both_directions_positive"].sum()
        ) if not pair_summary.empty else 0,
        "drawdown_warning_pairs": int(
            pair_summary["drawdown_warning_gt_35pct"].sum()
        ) if not pair_summary.empty else 0,
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
