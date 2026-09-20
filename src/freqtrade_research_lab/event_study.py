from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import erfc, sqrt
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class EventStudyConfig:
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    horizons: tuple[int, ...] = (1, 3, 5, 10, 20, 60)
    round_trip_cost_bps: float = 14.0
    minimum_train_events: int = 100
    minimum_validation_events: int = 30
    minimum_holdout_events: int = 30
    validation_fdr: float = 0.10

    def __post_init__(self) -> None:
        if not 0 < self.fast_period < self.slow_period:
            raise ValueError("MACD periods must satisfy 0 < fast < slow")
        if self.signal_period <= 0:
            raise ValueError("signal_period must be positive")
        if not self.horizons or any(horizon <= 0 for horizon in self.horizons):
            raise ValueError("horizons must contain positive integers")
        if self.round_trip_cost_bps < 0:
            raise ValueError("round_trip_cost_bps cannot be negative")


def split_boundaries(start: datetime, end: datetime) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if start_ts.tzinfo is None:
        start_ts = start_ts.tz_localize("UTC")
    else:
        start_ts = start_ts.tz_convert("UTC")
    if end_ts.tzinfo is None:
        end_ts = end_ts.tz_localize("UTC")
    else:
        end_ts = end_ts.tz_convert("UTC")
    if end_ts <= start_ts:
        raise ValueError("end must be later than start")
    span = end_ts - start_ts
    return start_ts + span * 0.60, start_ts + span * 0.80


def _period_labels(
    dates: pd.Series, train_end: pd.Timestamp, validation_end: pd.Timestamp
) -> np.ndarray:
    return np.select(
        [dates < train_end, dates < validation_end],
        ["train", "validation"],
        default="holdout",
    )


def _forward_extreme(values: np.ndarray, horizon: int, reducer: str) -> np.ndarray:
    shifted = np.empty(values.shape, dtype=float)
    shifted[:-1] = values[1:]
    shifted[-1] = np.nan
    reversed_series = pd.Series(shifted[::-1])
    rolling = reversed_series.rolling(horizon, min_periods=horizon)
    reduced = rolling.max() if reducer == "max" else rolling.min()
    return reduced.to_numpy()[::-1]


def _normal_p_value(t_stat: float) -> float:
    if not np.isfinite(t_stat):
        return 1.0
    return erfc(abs(t_stat) / sqrt(2.0))


def _summarize_events(events: pd.DataFrame, pair: str, horizon: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for (direction, period), group in events.groupby(["direction", "period"], observed=True):
        returns = group["net_return"].to_numpy(dtype=float)
        count = int(returns.size)
        mean = float(np.mean(returns)) if count else np.nan
        std = float(np.std(returns, ddof=1)) if count > 1 else np.nan
        t_stat = mean / (std / sqrt(count)) if count > 1 and std > 0 else np.nan
        gross_wins = returns[returns > 0].sum()
        gross_losses = -returns[returns < 0].sum()
        profit_factor = float(gross_wins / gross_losses) if gross_losses > 0 else np.inf
        rows.append(
            {
                "pair": pair,
                "direction": str(direction),
                "period": str(period),
                "horizon_minutes": horizon,
                "events": count,
                "mean_net_return": mean,
                "median_net_return": float(np.median(returns)) if count else np.nan,
                "win_rate": float(np.mean(returns > 0)) if count else np.nan,
                "std_net_return": std,
                "t_stat": t_stat,
                "p_value": _normal_p_value(t_stat),
                "profit_factor": profit_factor,
                "mean_mfe": float(group["mfe"].mean()),
                "mean_mae": float(group["mae"].mean()),
            }
        )
    return rows


def analyze_pair(
    frame: pd.DataFrame,
    pair: str,
    config: EventStudyConfig,
    train_end: pd.Timestamp,
    validation_end: pd.Timestamp,
) -> pd.DataFrame:
    data = frame.copy()
    close = data["close"]
    fast = close.ewm(span=config.fast_period, adjust=False, min_periods=config.fast_period).mean()
    slow = close.ewm(span=config.slow_period, adjust=False, min_periods=config.slow_period).mean()
    macd = fast - slow
    signal = macd.ewm(
        span=config.signal_period,
        adjust=False,
        min_periods=config.signal_period,
    ).mean()

    long_signal = (macd > signal) & (macd.shift(1) <= signal.shift(1)) & (data["volume"] > 0)
    short_signal = (macd < signal) & (macd.shift(1) >= signal.shift(1)) & (data["volume"] > 0)
    entry = data["open"].shift(-1).to_numpy(dtype=float)
    highs = data["high"].to_numpy(dtype=float)
    lows = data["low"].to_numpy(dtype=float)
    closes = data["close"].to_numpy(dtype=float)
    dates = data["date"]
    periods = _period_labels(dates, train_end, validation_end)
    cost_rate = config.round_trip_cost_bps / 10_000.0
    summary_rows: list[dict[str, object]] = []

    for horizon in config.horizons:
        exit_price = pd.Series(closes).shift(-horizon).to_numpy(dtype=float)
        max_high = _forward_extreme(highs, horizon, "max")
        min_low = _forward_extreme(lows, horizon, "min")

        for direction, mask in (("long", long_signal), ("short", short_signal)):
            valid = mask.to_numpy() & np.isfinite(entry) & np.isfinite(exit_price)
            valid &= np.isfinite(max_high) & np.isfinite(min_low)
            if not valid.any():
                continue

            if direction == "long":
                net_return = exit_price[valid] / entry[valid] - 1.0 - cost_rate
                mfe = max_high[valid] / entry[valid] - 1.0
                mae = min_low[valid] / entry[valid] - 1.0
            else:
                net_return = entry[valid] / exit_price[valid] - 1.0 - cost_rate
                mfe = entry[valid] / min_low[valid] - 1.0
                mae = entry[valid] / max_high[valid] - 1.0

            events = pd.DataFrame(
                {
                    "direction": direction,
                    "period": periods[valid],
                    "net_return": net_return,
                    "mfe": mfe,
                    "mae": mae,
                }
            )
            summary_rows.extend(_summarize_events(events, pair, horizon))

    return pd.DataFrame(summary_rows)


def benjamini_hochberg(p_values: Iterable[float]) -> np.ndarray:
    values = np.asarray(list(p_values), dtype=float)
    count = values.size
    if count == 0:
        return np.array([], dtype=float)
    order = np.argsort(values)
    ranked = values[order]
    adjusted = ranked * count / np.arange(1, count + 1)
    adjusted = np.minimum.accumulate(adjusted[::-1])[::-1]
    adjusted = np.clip(adjusted, 0.0, 1.0)
    result = np.empty(count, dtype=float)
    result[order] = adjusted
    return result


def build_candidate_tables(
    summary: pd.DataFrame, config: EventStudyConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if summary.empty:
        return pd.DataFrame(), pd.DataFrame()

    keys = ["pair", "direction", "horizon_minutes"]
    metric_columns = [
        "events",
        "mean_net_return",
        "win_rate",
        "p_value",
        "profit_factor",
        "mean_mfe",
        "mean_mae",
    ]
    wide = summary.pivot(index=keys, columns="period", values=metric_columns)
    wide.columns = [f"{metric}_{period}" for metric, period in wide.columns]
    wide = wide.reset_index()

    required = [
        "events_train",
        "events_validation",
        "events_holdout",
        "mean_net_return_train",
        "mean_net_return_validation",
        "mean_net_return_holdout",
        "p_value_validation",
    ]
    for column in required:
        if column not in wide:
            wide[column] = np.nan

    wide["validation_q_value"] = 1.0
    for (_, _), indexes in wide.groupby(["direction", "horizon_minutes"]).groups.items():
        index_list = list(indexes)
        wide.loc[index_list, "validation_q_value"] = benjamini_hochberg(
            wide.loc[index_list, "p_value_validation"].fillna(1.0)
        )

    wide["discovery_pass"] = (
        (wide["events_train"] >= config.minimum_train_events)
        & (wide["events_validation"] >= config.minimum_validation_events)
        & (wide["mean_net_return_train"] > 0)
        & (wide["mean_net_return_validation"] > 0)
        & (wide["validation_q_value"] <= config.validation_fdr)
    )
    wide["holdout_pass"] = (
        wide["discovery_pass"]
        & (wide["events_holdout"] >= config.minimum_holdout_events)
        & (wide["mean_net_return_holdout"] > 0)
    )
    wide["discovery_score"] = np.minimum(
        wide["mean_net_return_train"], wide["mean_net_return_validation"]
    )

    candidates = wide.loc[wide["discovery_pass"]].sort_values(
        ["discovery_score", "validation_q_value"], ascending=[False, True]
    )
    holdout = candidates.sort_values(
        ["holdout_pass", "mean_net_return_holdout"], ascending=[False, False]
    )
    return candidates.reset_index(drop=True), holdout.reset_index(drop=True)

