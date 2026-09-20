import numpy as np
import pandas as pd

from freqtrade_research_lab.event_study import (
    EventStudyConfig,
    analyze_signals,
    build_discovery_table,
)
from freqtrade_research_lab.signals import SignalSet


def _frame(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> pd.DataFrame:
    rows = len(opens)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=rows, freq="15min", tz="UTC"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": np.ones(rows),
        }
    )


def _single_signal(frame: pd.DataFrame, *, direction: str, index: int = 1) -> SignalSet:
    long_signal = pd.Series(False, index=frame.index)
    short_signal = pd.Series(False, index=frame.index)
    if direction == "long":
        long_signal.iloc[index] = True
    else:
        short_signal.iloc[index] = True
    return SignalSet(long=long_signal, short=short_signal)


def _all_train_boundaries(frame: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
    train_end = frame["date"].iloc[-1] + pd.Timedelta(days=1)
    validation_end = train_end + pd.Timedelta(days=1)
    return train_end, validation_end


def test_golden_long_two_bar_pnl_cost_mfe_mae() -> None:
    frame = _frame(
        opens=[99, 99, 100, 108, 111, 111],
        highs=[100, 101, 105, 112, 113, 113],
        lows=[98, 98, 97, 104, 109, 109],
        closes=[99, 100, 103, 110, 112, 112],
    )
    train_end, validation_end = _all_train_boundaries(frame)
    result = analyze_signals(
        frame,
        "TEST/USDT:USDT",
        _single_signal(frame, direction="long"),
        EventStudyConfig(horizons_bars=(2,), round_trip_cost_bps=14),
        train_end,
        validation_end,
        bar_minutes=15,
    )
    row = result.iloc[0]

    # Signal at t=1 -> entry open[t=2] = 100.
    # Two-bar hold covers t=2 and t=3 -> exit close[t=3] = 110.
    # Gross return 10%; round-trip cost 14 bps -> 9.86% net.
    assert row["direction"] == "long"
    assert int(row["events"]) == 1
    assert abs(row["mean_net_return"] - 0.0986) < 1e-12
    assert abs(row["mean_mfe"] - 0.12) < 1e-12
    assert abs(row["mean_mae"] - (-0.03)) < 1e-12


def test_golden_short_two_bar_pnl_cost_mfe_mae() -> None:
    frame = _frame(
        opens=[101, 101, 100, 94, 89, 89],
        highs=[102, 103, 104, 102, 91, 91],
        lows=[100, 99, 90, 85, 87, 87],
        closes=[101, 100, 95, 90, 88, 88],
    )
    train_end, validation_end = _all_train_boundaries(frame)
    result = analyze_signals(
        frame,
        "TEST/USDT:USDT",
        _single_signal(frame, direction="short"),
        EventStudyConfig(horizons_bars=(2,), round_trip_cost_bps=14),
        train_end,
        validation_end,
        bar_minutes=15,
    )
    row = result.iloc[0]

    # Signal at t=1 -> entry open[t=2] = 100.
    # Two-bar hold covers t=2 and t=3 -> exit close[t=3] = 90.
    # Gross short return 10%; round-trip cost 14 bps -> 9.86% net.
    assert row["direction"] == "short"
    assert int(row["events"]) == 1
    assert abs(row["mean_net_return"] - 0.0986) < 1e-12
    assert abs(row["mean_mfe"] - 0.15) < 1e-12
    assert abs(row["mean_mae"] - (-0.04)) < 1e-12


def test_validation_fdr_family_is_train_screened() -> None:
    rows: list[dict[str, object]] = []
    for pair, train_test_mean, validation_p in (
        ("A/USDT:USDT", 0.01, 0.04),
        ("B/USDT:USDT", -0.01, 0.20),
    ):
        for period in ("train", "validation"):
            rows.append(
                {
                    "pair": pair,
                    "direction": "long",
                    "period": period,
                    "horizon_bars": 5,
                    "holding_minutes": 75,
                    "events": 200 if period == "train" else 50,
                    "non_overlapping_events": 120 if period == "train" else 40,
                    "mean_net_return": 0.01 if period == "train" else 0.005,
                    "test_mean_net_return": (
                        train_test_mean if period == "train" else 0.005
                    ),
                    "win_rate": 0.55,
                    "p_value": 0.50 if period == "train" else validation_p,
                    "profit_factor": 1.2,
                    "mean_mfe": 0.02,
                    "mean_mae": -0.01,
                    "test_std_net_return": 0.01,
                }
            )

    discovery = build_discovery_table(
        pd.DataFrame(rows),
        EventStudyConfig(
            minimum_train_events=100,
            minimum_validation_events=30,
            validation_fdr=0.05,
        ),
    )
    a = discovery.loc[discovery["pair"] == "A/USDT:USDT"].iloc[0]
    b = discovery.loc[discovery["pair"] == "B/USDT:USDT"].iloc[0]

    assert bool(a["train_eligible"])
    assert not bool(b["train_eligible"])
    assert abs(a["validation_q_value"] - 0.04) < 1e-12
    assert pd.isna(b["validation_q_value"])
    assert int(a["fdr_family_size"]) == 1
    assert a["fdr_scope"] == "train_screened_validation_pair_direction_horizon"
