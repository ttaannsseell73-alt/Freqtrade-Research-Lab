from datetime import datetime, timezone
from io import StringIO

import numpy as np
import pandas as pd

from freqtrade_research_lab.event_study import (
    EventStudyConfig,
    _directional_performance,
    _forward_contiguous,
    _forward_extreme,
    analyze_pair,
    benjamini_hochberg,
    build_candidate_tables,
    build_discovery_table,
    split_boundaries,
)


def synthetic_frame(rows: int = 600, freq: str = "min") -> pd.DataFrame:
    index = np.arange(rows)
    close = 100 + 0.02 * index + 2.5 * np.sin(index / 8)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq=freq, tz="UTC"),
            "open": close - 0.01,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": np.ones(rows),
        }
    )


def test_event_study_outputs_both_directions_and_all_horizons() -> None:
    frame = synthetic_frame(freq="15min")
    train_end = frame["date"].iloc[360]
    validation_end = frame["date"].iloc[480]
    config = EventStudyConfig(horizons_bars=(1, 5), round_trip_cost_bps=0)
    result = analyze_pair(
        frame,
        "TEST/USDT:USDT",
        config,
        train_end,
        validation_end,
        bar_minutes=15,
    )
    assert set(result["direction"]) == {"long", "short"}
    assert set(result["horizon_bars"]) == {1, 5}
    assert set(result["holding_minutes"]) == {15, 75}
    assert set(result["period"]) == {"train", "validation", "holdout"}


def test_benjamini_hochberg_is_monotonic_in_rank() -> None:
    p_values = np.array([0.01, 0.04, 0.03, 0.50])
    adjusted = benjamini_hochberg(p_values)
    assert np.all((0 <= adjusted) & (adjusted <= 1))
    ranked = adjusted[np.argsort(p_values)]
    assert np.all(np.diff(ranked) >= -1e-12)


def test_split_boundaries_are_60_20_20() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 4, 11, tzinfo=timezone.utc)
    train_end, validation_end = split_boundaries(start, end)
    assert (train_end - pd.Timestamp(start)).days == 60
    assert (validation_end - train_end).days == 20


def test_forward_extreme_uses_only_candles_after_signal() -> None:
    values = np.array([10.0, 12.0, 11.0, 15.0, 14.0])
    result = _forward_extreme(values, 2, "max")
    np.testing.assert_allclose(result[:3], [12.0, 15.0, 15.0])
    assert np.isnan(result[3:]).all()


def test_short_return_uses_linear_futures_pnl_denominator() -> None:
    net, mfe, mae = _directional_performance(
        entry=np.array([100.0]),
        exit_price=np.array([90.0]),
        max_high=np.array([105.0]),
        min_low=np.array([85.0]),
        direction="short",
        cost_rate=0.001,
    )
    np.testing.assert_allclose(net, [0.099])
    np.testing.assert_allclose(mfe, [0.15])
    np.testing.assert_allclose(mae, [-0.05])


def test_period_boundaries_drop_cross_split_events() -> None:
    frame = synthetic_frame()
    train_end = frame["date"].iloc[360]
    validation_end = frame["date"].iloc[480]
    horizon = 60
    config = EventStudyConfig(horizons_bars=(horizon,), round_trip_cost_bps=0)
    result = analyze_pair(frame, "TEST/USDT:USDT", config, train_end, validation_end)

    close = frame["close"]
    fast = close.ewm(span=12, adjust=False, min_periods=12).mean()
    slow = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = fast - slow
    signal = macd.ewm(span=9, adjust=False, min_periods=9).mean()
    masks = {
        "long": (macd > signal) & (macd.shift(1) <= signal.shift(1)) & (frame["volume"] > 0),
        "short": (macd < signal) & (macd.shift(1) >= signal.shift(1)) & (frame["volume"] > 0),
    }

    def labels(dates: pd.Series) -> np.ndarray:
        return np.select(
            [dates < train_end, dates < validation_end],
            ["train", "validation"],
            default="holdout",
        )

    signal_period = labels(frame["date"])
    entry_dates = frame["date"].shift(-1)
    exit_dates = frame["date"].shift(-horizon)
    entry_period = labels(entry_dates)
    exit_period = labels(exit_dates)

    for direction, mask in masks.items():
        valid = (
            mask.to_numpy()
            & entry_dates.notna().to_numpy()
            & exit_dates.notna().to_numpy()
            & (signal_period == entry_period)
            & (signal_period == exit_period)
        )
        for period in ("train", "validation", "holdout"):
            expected = int(np.sum(valid & (signal_period == period)))
            rows = result.loc[
                (result["direction"] == direction)
                & (result["period"] == period)
                & (result["horizon_bars"] == horizon),
                "events",
            ]
            actual = int(rows.iloc[0]) if not rows.empty else 0
            assert actual == expected


def test_candidate_table_never_exposes_holdout_columns() -> None:
    rows = []
    for period, events, mean_return, p_value in (
        ("train", 200, 0.010, 0.20),
        ("validation", 50, 0.005, 0.001),
        ("holdout", 40, -0.020, 0.10),
    ):
        rows.append(
            {
                "pair": "TEST/USDT:USDT",
                "direction": "long",
                "period": period,
                "horizon_bars": 5,
                "holding_minutes": 5,
                "events": events,
                "mean_net_return": mean_return,
                "win_rate": 0.55,
                "p_value": p_value,
                "profit_factor": 1.2,
                "mean_mfe": 0.01,
                "mean_mae": -0.01,
            }
        )

    candidates, holdout = build_candidate_tables(
        pd.DataFrame(rows), EventStudyConfig(validation_fdr=0.10)
    )
    assert len(candidates) == 1
    assert not any(column.endswith("_holdout") for column in candidates.columns)
    assert "holdout_pass" not in candidates.columns
    assert "mean_net_return_holdout" in holdout.columns
    assert not bool(holdout["holdout_pass"].iloc[0])



def test_empty_candidate_holdout_csv_keeps_readable_schema() -> None:
    rows = []
    for period, events, mean_return, p_value in (
        ("train", 200, -0.010, 0.20),
        ("validation", 50, -0.005, 0.50),
        ("holdout", 40, 0.020, 0.10),
    ):
        rows.append(
            {
                "pair": "TEST/USDT:USDT",
                "direction": "long",
                "period": period,
                "horizon_bars": 5,
                "holding_minutes": 5,
                "events": events,
                "mean_net_return": mean_return,
                "win_rate": 0.45,
                "p_value": p_value,
                "profit_factor": 0.8,
                "mean_mfe": 0.01,
                "mean_mae": -0.01,
            }
        )

    candidates, holdout = build_candidate_tables(
        pd.DataFrame(rows), EventStudyConfig(validation_fdr=0.10)
    )
    assert candidates.empty
    assert holdout.empty
    assert "holdout_pass" in holdout.columns
    assert "mean_net_return_holdout" in holdout.columns

    csv_text = holdout.to_csv(index=False)
    reread = pd.read_csv(StringIO(csv_text))
    assert reread.empty
    assert "holdout_pass" in reread.columns



def test_forward_contiguous_rejects_windows_crossing_missing_candle() -> None:
    dates = pd.Series(
        pd.to_datetime(
            [
                "2026-01-01 00:00:00+00:00",
                "2026-01-01 00:01:00+00:00",
                "2026-01-01 00:02:00+00:00",
                "2026-01-01 00:04:00+00:00",
                "2026-01-01 00:05:00+00:00",
            ],
            utc=True,
        )
    )

    one_bar = _forward_contiguous(dates, horizon_bars=1, bar_minutes=1)
    two_bars = _forward_contiguous(dates, horizon_bars=2, bar_minutes=1)

    assert one_bar.tolist() == [True, True, False, True, False]
    assert two_bars.tolist() == [True, False, False, False, False]



def test_validation_fdr_covers_entire_experiment_family() -> None:
    rows = []
    for pair, direction, horizon, validation_p in (
        ("A/USDT:USDT", "long", 5, 0.04),
        ("B/USDT:USDT", "short", 10, 0.20),
    ):
        for period, p_value in (("train", 0.50), ("validation", validation_p)):
            rows.append(
                {
                    "pair": pair,
                    "direction": direction,
                    "period": period,
                    "horizon_bars": horizon,
                    "holding_minutes": horizon,
                    "events": 200 if period == "train" else 50,
                    "mean_net_return": 0.01,
                    "win_rate": 0.55,
                    "p_value": p_value,
                    "profit_factor": 1.2,
                    "mean_mfe": 0.01,
                    "mean_mae": -0.01,
                }
            )

    discovery = build_discovery_table(
        pd.DataFrame(rows),
        EventStudyConfig(validation_fdr=0.05),
    )

    first = discovery.loc[discovery["pair"] == "A/USDT:USDT"].iloc[0]
    assert abs(first["validation_q_value"] - 0.08) < 1e-12
    assert first["fdr_family_size"] == 2
    assert first["fdr_scope"] == "experiment_all_pair_direction_horizon"
    assert first["minimum_train_events"] == 100
    assert first["minimum_validation_events"] == 30
    assert first["experiment_validation_fdr"] == 0.05
    assert not bool(first["discovery_pass"])
