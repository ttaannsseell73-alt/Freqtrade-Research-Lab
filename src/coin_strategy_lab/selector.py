from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd


@dataclass(frozen=True)
class RobustnessPolicy:
    min_windows: int = 2
    min_oos_floor_bps: float = 0.0
    min_stress_expectancy_bps: float = 0.0
    min_holdout_profit_factor: float = 1.0


def build_robust_assignments(
    matrices: Iterable[tuple[str, pd.DataFrame]],
    policy: RobustnessPolicy = RobustnessPolicy(),
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for window_id, matrix in matrices:
        required = {
            "symbol",
            "timeframe",
            "strategy_id",
            "candidate",
            "oos_floor_bps",
            "expectancy_full_15bps",
            "profit_factor_holdout",
        }
        missing = required - set(matrix.columns)
        if missing:
            raise ValueError(f"{window_id}: missing columns {sorted(missing)}")
        cur = matrix.copy()
        cur["window_id"] = window_id
        cur["candidate"] = cur["candidate"].astype(bool)
        frames.append(cur)

    if not frames:
        return pd.DataFrame()

    all_rows = pd.concat(frames, ignore_index=True)
    qualified = all_rows[
        all_rows["candidate"]
        & (all_rows["oos_floor_bps"] > policy.min_oos_floor_bps)
        & (all_rows["expectancy_full_15bps"] > policy.min_stress_expectancy_bps)
        & (all_rows["profit_factor_holdout"] > policy.min_holdout_profit_factor)
    ].copy()

    if qualified.empty:
        return pd.DataFrame(
            columns=[
                "symbol","timeframe","strategy_id","windows_passed","window_ids",
                "worst_oos_floor_bps","worst_15bps_expectancy",
                "worst_holdout_profit_factor","robust",
            ]
        )

    grouped = (
        qualified.groupby(["symbol","timeframe","strategy_id"], as_index=False)
        .agg(
            windows_passed=("window_id","nunique"),
            window_ids=("window_id", lambda s: ",".join(sorted(set(map(str, s))))),
            worst_oos_floor_bps=("oos_floor_bps","min"),
            median_oos_floor_bps=("oos_floor_bps","median"),
            worst_15bps_expectancy=("expectancy_full_15bps","min"),
            worst_holdout_profit_factor=("profit_factor_holdout","min"),
            total_trades=("trades_full","sum"),
        )
    )
    grouped["robust"] = grouped["windows_passed"] >= policy.min_windows
    grouped = grouped.sort_values(
        [
            "robust",
            "windows_passed",
            "worst_oos_floor_bps",
            "worst_15bps_expectancy",
            "worst_holdout_profit_factor",
        ],
        ascending=False,
    ).reset_index(drop=True)
    return grouped


def select_best_per_coin_timeframe(robust: pd.DataFrame) -> pd.DataFrame:
    if robust.empty:
        return robust.copy()
    eligible = robust[robust["robust"]].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        [
            "symbol",
            "timeframe",
            "windows_passed",
            "worst_oos_floor_bps",
            "worst_15bps_expectancy",
            "worst_holdout_profit_factor",
        ],
        ascending=[True, True, False, False, False, False],
    )
    return eligible.groupby(["symbol","timeframe"], as_index=False).head(1).reset_index(drop=True)


def select_best_setup_per_coin(robust: pd.DataFrame) -> pd.DataFrame:
    if robust.empty:
        return robust.copy()
    eligible = robust[robust["robust"]].copy()
    if eligible.empty:
        return eligible
    eligible = eligible.sort_values(
        [
            "symbol",
            "windows_passed",
            "worst_oos_floor_bps",
            "worst_15bps_expectancy",
            "worst_holdout_profit_factor",
        ],
        ascending=[True, False, False, False, False],
    )
    return eligible.groupby("symbol", as_index=False).head(1).reset_index(drop=True)


# Backward-compatible name; semantically this returns one strategy per coin+timeframe.
select_best_per_coin = select_best_per_coin_timeframe
