from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
import math

import pandas as pd


@dataclass(frozen=True)
class RobustnessPolicy:
    min_windows: int = 2
    min_oos_floor_bps: float = 0.0
    min_stress_expectancy_bps: float = 0.0
    min_holdout_profit_factor: float = 1.0


def _cap_log(value: float, cap: float) -> float:
    value = max(float(value), 0.0)
    cap = max(float(cap), 1e-9)
    return min(math.log1p(value) / math.log1p(cap), 1.0)


EXPECTED_WINDOWS_BY_TIMEFRAME = {
    "1m": 2,
    "5m": 2,
    "15m": 2,
    "1h": 3,
    "4h": 2,
    "1d": 2,
}


def _confidence_score(row: pd.Series) -> float:
    """Timeframe-aware routing score.

    Raw bps-per-trade cannot be compared directly across 1m..1d. This score
    deliberately caps large edge values and also rewards stress survival,
    holdout PF, repeatability across windows, and sample size.
    """
    edge = _cap_log(row.get("worst_oos_floor_bps", 0.0), 100.0)
    stress = _cap_log(row.get("worst_15bps_expectancy", 0.0), 100.0)
    pf = min(max((float(row.get("worst_holdout_profit_factor", 1.0)) - 1.0) / 1.0, 0.0), 1.0)
    sample = _cap_log(row.get("total_trades", 0.0), 300.0)
    timeframe = str(row.get("timeframe", ""))
    expected_windows = float(EXPECTED_WINDOWS_BY_TIMEFRAME.get(timeframe, 2))
    windows = min(max(float(row.get("windows_passed", 0.0)) / expected_windows, 0.0), 1.0)
    return 100.0 * (
        0.35 * edge
        + 0.25 * stress
        + 0.15 * pf
        + 0.15 * sample
        + 0.10 * windows
    )


def add_confidence_score(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        out["confidence_score"] = pd.Series(dtype=float)
        return out
    out["confidence_score"] = out.apply(_confidence_score, axis=1)
    return out


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
                "worst_holdout_profit_factor","total_trades",
                "expected_windows","window_coverage","robust_tier",
                "confidence_score","robust",
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
    grouped["expected_windows"] = grouped["timeframe"].map(EXPECTED_WINDOWS_BY_TIMEFRAME).fillna(2).astype(int)
    grouped["window_coverage"] = grouped["windows_passed"] / grouped["expected_windows"]
    grouped["robust"] = grouped["windows_passed"] >= policy.min_windows
    grouped["robust_tier"] = "RESEARCH"
    grouped.loc[grouped["robust"], "robust_tier"] = "B"
    grouped.loc[grouped["robust"] & (grouped["window_coverage"] >= 1.0), "robust_tier"] = "A"
    grouped = add_confidence_score(grouped)
    grouped = grouped.sort_values(
        [
            "robust",
            "windows_passed",
            "confidence_score",
            "worst_oos_floor_bps",
            "worst_15bps_expectancy",
        ],
        ascending=False,
    ).reset_index(drop=True)
    return grouped


def _ensure_routing_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize legacy/direct selector inputs to the current routing schema."""
    out = frame.copy()
    if "expected_windows" not in out.columns:
        out["expected_windows"] = (
            out["timeframe"].map(EXPECTED_WINDOWS_BY_TIMEFRAME).fillna(2).astype(int)
        )
    if "window_coverage" not in out.columns:
        out["window_coverage"] = (
            out["windows_passed"].astype(float)
            / out["expected_windows"].replace(0, 1).astype(float)
        )
    if "robust_tier" not in out.columns:
        out["robust_tier"] = "RESEARCH"
        robust_mask = out["robust"].astype(bool)
        out.loc[robust_mask, "robust_tier"] = "B"
        out.loc[robust_mask & (out["window_coverage"] >= 1.0), "robust_tier"] = "A"
    if "confidence_score" not in out.columns:
        out = add_confidence_score(out)
    return out


def select_best_per_coin_timeframe(robust: pd.DataFrame) -> pd.DataFrame:
    if robust.empty:
        return robust.copy()
    eligible = _ensure_routing_columns(robust)
    eligible = eligible[eligible["robust"]].copy()
    if eligible.empty:
        return eligible
    eligible["_tier_rank"] = eligible["robust_tier"].map({"A": 2, "B": 1}).fillna(0)
    eligible = eligible.sort_values(
        [
            "symbol",
            "timeframe",
            "_tier_rank",
            "confidence_score",
            "windows_passed",
            "worst_oos_floor_bps",
            "worst_15bps_expectancy",
        ],
        ascending=[True, True, False, False, False, False, False],
    )
    return (
        eligible.groupby(["symbol","timeframe"], as_index=False)
        .head(1)
        .drop(columns=["_tier_rank"])
        .reset_index(drop=True)
    )


def select_best_setup_per_coin(robust: pd.DataFrame) -> pd.DataFrame:
    if robust.empty:
        return robust.copy()
    eligible = _ensure_score(robust)
    eligible = eligible[eligible["robust"]].copy()
    if eligible.empty:
        return eligible
    eligible["_tier_rank"] = eligible["robust_tier"].map({"A": 2, "B": 1}).fillna(0)
    eligible = eligible.sort_values(
        [
            "symbol",
            "_tier_rank",
            "confidence_score",
            "windows_passed",
            "worst_oos_floor_bps",
            "worst_15bps_expectancy",
        ],
        ascending=[True, False, False, False, False, False],
    )
    return (
        eligible.groupby("symbol", as_index=False)
        .head(1)
        .drop(columns=["_tier_rank"])
        .reset_index(drop=True)
    )


# Backward-compatible name; semantically this returns one strategy per coin+timeframe.
select_best_per_coin = select_best_per_coin_timeframe
