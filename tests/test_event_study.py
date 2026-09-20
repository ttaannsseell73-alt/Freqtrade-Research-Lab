from datetime import datetime, timezone

import numpy as np
import pandas as pd

from freqtrade_research_lab.event_study import (
    EventStudyConfig,
    _directional_performance,
    _forward_extreme,
    analyze_pair,
    benjamini_hochberg,
    split_boundaries,
)


def synthetic_frame(rows: int = 600) -> pd.DataFrame:
    index = np.arange(rows)
    close = 100 + 0.02 * index + 2.5 * np.sin(index / 8)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="min", tz="UTC"),
            "open": close - 0.01,
            "high": close + 0.10,
            "low": close - 0.10,
            "close": close,
            "volume": np.ones(rows),
        }
    )


def test_event_study_outputs_both_directions_and_all_horizons() -> None:
    frame = synthetic_frame()
    train_end = frame["date"].iloc[360]
    validation_end = frame["date"].iloc[480]
    config = EventStudyConfig(horizons=(1, 5), round_trip_cost_bps=0)
    result = analyze_pair(frame, "TEST/USDT:USDT", config, train_end, validation_end)
    assert set(result["direction"]) == {"long", "short"}
    assert set(result["horizon_minutes"]) == {1, 5}
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
