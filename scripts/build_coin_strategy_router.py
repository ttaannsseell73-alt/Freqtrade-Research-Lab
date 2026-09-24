from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coin_strategy_lab.selector import (
    RobustnessPolicy,
    build_robust_assignments,
    select_best_per_coin_timeframe,
    select_best_setup_per_coin,
)


def _parse_matrix(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("matrix must be LABEL=PATH")
    label, raw_path = value.split("=", 1)
    label = label.strip()
    if not label:
        raise argparse.ArgumentTypeError("matrix label may not be empty")
    return label, Path(raw_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--matrix",
        action="append",
        type=_parse_matrix,
        required=True,
        help="Repeatable LABEL=MASTER_COIN_STRATEGY_MATRIX.csv",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-windows", type=int, default=2)
    args = parser.parse_args()

    matrices: list[tuple[str, pd.DataFrame]] = []
    for label, path in args.matrix:
        matrices.append((label, pd.read_csv(path)))

    robust = build_robust_assignments(
        matrices,
        RobustnessPolicy(min_windows=args.min_windows),
    )
    best_tf = select_best_per_coin_timeframe(robust)
    best_primary = select_best_setup_per_coin(robust)

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    robust.to_csv(out / "ROBUST_ASSIGNMENTS.csv", index=False)
    best_tf.to_csv(out / "BEST_ROBUST_STRATEGY_BY_COIN_TIMEFRAME.csv", index=False)
    best_primary.to_csv(out / "BEST_ROBUST_SETUP_BY_COIN.csv", index=False)

    assignments = {}
    for row in best_tf.itertuples():
        symbol_entry = assignments.setdefault(row.symbol, {"timeframes": {}})
        symbol_entry["timeframes"][row.timeframe] = {
            "strategy_id": row.strategy_id,
            "windows_passed": int(row.windows_passed),
            "window_ids": str(row.window_ids).split(","),
            "worst_oos_floor_bps": float(row.worst_oos_floor_bps),
            "worst_15bps_expectancy": float(row.worst_15bps_expectancy),
            "worst_holdout_profit_factor": float(row.worst_holdout_profit_factor),
            "confidence_score": float(row.confidence_score),
            "status": "ROBUST",
        }

    for row in best_primary.itertuples():
        symbol_entry = assignments.setdefault(row.symbol, {"timeframes": {}})
        symbol_entry["primary"] = {
            "timeframe": row.timeframe,
            "strategy_id": row.strategy_id,
            "windows_passed": int(row.windows_passed),
            "worst_oos_floor_bps": float(row.worst_oos_floor_bps),
            "worst_15bps_expectancy": float(row.worst_15bps_expectancy),
            "worst_holdout_profit_factor": float(row.worst_holdout_profit_factor),
            "confidence_score": float(row.confidence_score),
        }

    router = {
        "version": 2,
        "default": "NO_TRADE",
        "assignments": assignments,
        "rule": "Assignments are keyed by coin and timeframe. Each coin may also have one primary setup. Missing coin/timeframe => NO_TRADE.",
    }
    (out / "ROUTER_CONFIG.json").write_text(
        json.dumps(router, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = {
        "input_windows": [label for label, _ in args.matrix],
        "robust_pairs": int(robust["robust"].sum()) if not robust.empty else 0,
        "coins_with_assignment": int(best_primary["symbol"].nunique()) if not best_primary.empty else 0,
        "coin_timeframe_assignments": int(len(best_tf)),
        "default": "NO_TRADE",
        "selection_metric": "confidence_score",
    }
    (out / "ROUTER_SUMMARY.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
