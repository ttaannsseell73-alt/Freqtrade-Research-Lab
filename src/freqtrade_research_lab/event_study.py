from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import erfc, sqrt
from typing import Iterable

import numpy as np
import pandas as pd

from .signals import SignalSet, macd_crossover_signals, validate_signal_set


@dataclass(frozen=True)
class EventStudyConfig:
    fast_period: int = 12
    slow_period: int = 26
    signal_period: int = 9
    horizons_bars: tuple[int, ...] = (1, 3, 5, 10, 20, 60)
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
        if not self.horizons_bars or any(horizon <= 0 for horizon in self.horizons_bars):
            raise ValueError("horizons_bars must contain positive integers")
        if self.round_trip_cost_bps < 0:
            raise ValueError("round_trip_cost_bps cannot be negative")
        if self.minimum_train_events <= 0:
            raise ValueError("minimum_train_events must be positive")
        if self.minimum_validation_events <= 0:
            raise ValueError("minimum_validation_events must be positive")
        if self.minimum_holdout_events <= 0:
            raise ValueError("minimum_holdout_events must be positive")
        if not 0 < self.validation_fdr <= 1:
            raise ValueError("validation_fdr must be in (0, 1]")


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


def _forward_extreme(values: np.ndarray, horizon_bars: int, reducer: str) -> np.ndarray:
    future = pd.Series(values, dtype=float).shift(-1)
    rolling = future.rolling(horizon_bars, min_periods=horizon_bars)
    reduced = rolling.max() if reducer == "max" else rolling.min()
    return reduced.shift(-(horizon_bars - 1)).to_numpy()


def _forward_contiguous(
    dates: pd.Series,
    horizon_bars: int,
    bar_minutes: int,
) -> np.ndarray:
    if horizon_bars <= 0:
        raise ValueError("horizon_bars must be positive")
    if bar_minutes <= 0:
        raise ValueError("bar_minutes must be positive")

    interval = pd.Timedelta(minutes=bar_minutes)
    edge_ok = dates.diff().eq(interval).astype(float)
    future_edges = edge_ok.shift(-1)
    rolling = future_edges.rolling(horizon_bars, min_periods=horizon_bars).min()
    forward = rolling.shift(-(horizon_bars - 1))
    return forward.fillna(0.0).astype(bool).to_numpy()


def _directional_performance(
    entry: np.ndarray,
    exit_price: np.ndarray,
    max_high: np.ndarray,
    min_low: np.ndarray,
    direction: str,
    cost_rate: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if direction == "long":
        net_return = (exit_price - entry) / entry - cost_rate
        mfe = (max_high - entry) / entry
        mae = (min_low - entry) / entry
    elif direction == "short":
        net_return = (entry - exit_price) / entry - cost_rate
        mfe = (entry - min_low) / entry
        mae = (entry - max_high) / entry
    else:
        raise ValueError(f"Unsupported direction: {direction}")
    return net_return, mfe, mae


def _normal_p_value(t_stat: float) -> float:
    if not np.isfinite(t_stat):
        return 1.0
    return erfc(abs(t_stat) / sqrt(2.0))


def _summarize_events(
    events: pd.DataFrame,
    pair: str,
    horizon_bars: int,
    bar_minutes: int,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    holding_minutes = horizon_bars * bar_minutes
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
                "horizon_bars": horizon_bars,
                "holding_minutes": holding_minutes,
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


def analyze_signals(
    frame: pd.DataFrame,
    pair: str,
    signals: SignalSet,
    config: EventStudyConfig,
    train_end: pd.Timestamp,
    validation_end: pd.Timestamp,
    *,
    bar_minutes: int = 1,
) -> pd.DataFrame:
    if bar_minutes <= 0:
        raise ValueError("bar_minutes must be positive")

    data = frame.copy()
    checked = validate_signal_set(data, signals)
    long_signal = checked.long
    short_signal = checked.short

    entry = data["open"].shift(-1).to_numpy(dtype=float)
    highs = data["high"].to_numpy(dtype=float)
    lows = data["low"].to_numpy(dtype=float)
    closes = data["close"].to_numpy(dtype=float)
    dates = data["date"]
    signal_periods = _period_labels(dates, train_end, validation_end)
    entry_dates = dates.shift(-1)
    entry_periods = _period_labels(entry_dates, train_end, validation_end)
    cost_rate = config.round_trip_cost_bps / 10_000.0
    summary_rows: list[dict[str, object]] = []

    for horizon_bars in config.horizons_bars:
        exit_price = pd.Series(closes).shift(-horizon_bars).to_numpy(dtype=float)
        exit_dates = dates.shift(-horizon_bars)
        exit_periods = _period_labels(exit_dates, train_end, validation_end)
        max_high = _forward_extreme(highs, horizon_bars, "max")
        min_low = _forward_extreme(lows, horizon_bars, "min")
        contiguous = _forward_contiguous(dates, horizon_bars, bar_minutes)

        for direction, mask in (("long", long_signal), ("short", short_signal)):
            valid = mask.to_numpy() & np.isfinite(entry) & np.isfinite(exit_price)
            valid &= np.isfinite(max_high) & np.isfinite(min_low)
            valid &= contiguous
            valid &= entry_dates.notna().to_numpy() & exit_dates.notna().to_numpy()
            valid &= signal_periods == entry_periods
            valid &= signal_periods == exit_periods
            if not valid.any():
                continue

            net_return, mfe, mae = _directional_performance(
                entry[valid],
                exit_price[valid],
                max_high[valid],
                min_low[valid],
                direction,
                cost_rate,
            )
            events = pd.DataFrame(
                {
                    "direction": direction,
                    "period": signal_periods[valid],
                    "net_return": net_return,
                    "mfe": mfe,
                    "mae": mae,
                }
            )
            summary_rows.extend(
                _summarize_events(events, pair, horizon_bars, bar_minutes)
            )

    return pd.DataFrame(summary_rows)


def analyze_pair(
    frame: pd.DataFrame,
    pair: str,
    config: EventStudyConfig,
    train_end: pd.Timestamp,
    validation_end: pd.Timestamp,
    *,
    bar_minutes: int = 1,
) -> pd.DataFrame:
    signals = macd_crossover_signals(
        frame,
        fast_period=config.fast_period,
        slow_period=config.slow_period,
        signal_period=config.signal_period,
    )
    return analyze_signals(
        frame,
        pair,
        signals,
        config,
        train_end,
        validation_end,
        bar_minutes=bar_minutes,
    )


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


def build_discovery_table(
    summary: pd.DataFrame, config: EventStudyConfig
) -> pd.DataFrame:
    if summary.empty:
        return pd.DataFrame()

    keys = ["pair", "direction", "horizon_bars", "holding_minutes"]
    metric_columns = [
        "events",
        "mean_net_return",
        "win_rate",
        "p_value",
        "profit_factor",
        "mean_mfe",
        "mean_mae",
    ]

    discovery_summary = summary.loc[
        summary["period"].isin(["train", "validation"])
    ].copy()
    discovery_wide = discovery_summary.pivot(
        index=keys, columns="period", values=metric_columns
    )
    discovery_wide.columns = [
        f"{metric}_{period}" for metric, period in discovery_wide.columns
    ]
    discovery_wide = discovery_wide.reset_index()

    required = [
        "events_train",
        "events_validation",
        "mean_net_return_train",
        "mean_net_return_validation",
        "p_value_validation",
    ]
    for column in required:
        if column not in discovery_wide:
            discovery_wide[column] = np.nan

    discovery_wide["validation_q_value"] = benjamini_hochberg(
        discovery_wide["p_value_validation"].fillna(1.0)
    )
    discovery_wide["fdr_family_size"] = int(len(discovery_wide))
    discovery_wide["fdr_scope"] = "experiment_all_pair_direction_horizon"
    discovery_wide["minimum_train_events"] = config.minimum_train_events
    discovery_wide["minimum_validation_events"] = config.minimum_validation_events
    discovery_wide["experiment_validation_fdr"] = config.validation_fdr

    discovery_wide["discovery_pass"] = (
        (discovery_wide["events_train"] >= config.minimum_train_events)
        & (discovery_wide["events_validation"] >= config.minimum_validation_events)
        & (discovery_wide["mean_net_return_train"] > 0)
        & (discovery_wide["mean_net_return_validation"] > 0)
        & (discovery_wide["validation_q_value"] <= config.validation_fdr)
    )
    discovery_wide["discovery_score"] = np.minimum(
        discovery_wide["mean_net_return_train"],
        discovery_wide["mean_net_return_validation"],
    )
    return discovery_wide


def build_candidate_tables(
    summary: pd.DataFrame, config: EventStudyConfig
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if summary.empty:
        return pd.DataFrame(), pd.DataFrame()

    keys = ["pair", "direction", "horizon_bars", "holding_minutes"]
    metric_columns = [
        "events",
        "mean_net_return",
        "win_rate",
        "p_value",
        "profit_factor",
        "mean_mfe",
        "mean_mae",
    ]

    discovery_wide = build_discovery_table(summary, config)
    candidates = discovery_wide.loc[discovery_wide["discovery_pass"]].sort_values(
        ["discovery_score", "validation_q_value"], ascending=[False, True]
    )
    candidates = candidates.reset_index(drop=True)

    if candidates.empty:
        holdout = candidates.copy()
        for metric in metric_columns:
            holdout[f"{metric}_holdout"] = pd.Series(dtype=float)
        holdout["holdout_pass"] = pd.Series(dtype=bool)
        return candidates, holdout

    holdout_summary = summary.loc[summary["period"] == "holdout"].copy()
    holdout_wide = holdout_summary.pivot(
        index=keys, columns="period", values=metric_columns
    )
    holdout_wide.columns = [
        f"{metric}_{period}" for metric, period in holdout_wide.columns
    ]
    holdout_wide = holdout_wide.reset_index()

    holdout = candidates.merge(holdout_wide, on=keys, how="left")
    if "events_holdout" not in holdout:
        holdout["events_holdout"] = np.nan
    if "mean_net_return_holdout" not in holdout:
        holdout["mean_net_return_holdout"] = np.nan

    holdout["holdout_pass"] = (
        (holdout["events_holdout"] >= config.minimum_holdout_events)
        & (holdout["mean_net_return_holdout"] > 0)
    )
    holdout = holdout.sort_values(
        ["holdout_pass", "mean_net_return_holdout"], ascending=[False, False]
    )
    return candidates, holdout.reset_index(drop=True)
