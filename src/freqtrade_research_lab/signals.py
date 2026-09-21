from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class SignalSet:
    long: pd.Series
    short: pd.Series


def validate_signal_set(frame: pd.DataFrame, signals: SignalSet) -> SignalSet:
    if len(signals.long) != len(frame) or len(signals.short) != len(frame):
        raise ValueError("Signal length must match the OHLCV frame")
    if not signals.long.index.equals(frame.index) or not signals.short.index.equals(frame.index):
        raise ValueError("Signal indexes must exactly match the OHLCV frame index")

    long_signal = signals.long.fillna(False).astype(bool)
    short_signal = signals.short.fillna(False).astype(bool)
    if (long_signal & short_signal).any():
        raise ValueError("Long and short signals cannot overlap on the same candle")
    return SignalSet(long=long_signal, short=short_signal)


def macd_crossover_signals(
    frame: pd.DataFrame,
    *,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> SignalSet:
    if not 0 < fast_period < slow_period:
        raise ValueError("MACD periods must satisfy 0 < fast < slow")
    if signal_period <= 0:
        raise ValueError("signal_period must be positive")

    close = frame["close"]
    fast = close.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
    slow = close.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
    macd = fast - slow
    signal = macd.ewm(
        span=signal_period,
        adjust=False,
        min_periods=signal_period,
    ).mean()
    tradable = frame["volume"] > 0
    return SignalSet(
        long=(macd > signal) & (macd.shift(1) <= signal.shift(1)) & tradable,
        short=(macd < signal) & (macd.shift(1) >= signal.shift(1)) & tradable,
    )


def liquidity_sweep_reclaim_signals(
    frame: pd.DataFrame,
    *,
    lookback: int = 20,
    min_sweep_bps: float = 5.0,
    rejection_close_fraction: float = 0.60,
) -> SignalSet:
    """Closed-candle liquidity sweep/reclaim signals with no future leakage.

    Long:
    - current low sweeps below the prior `lookback`-bar low by at least
      `min_sweep_bps`,
    - current close reclaims that prior low,
    - close finishes in the upper `rejection_close_fraction` of the candle.

    Short is the exact mirror image around the prior rolling high.
    """
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if min_sweep_bps < 0:
        raise ValueError("min_sweep_bps cannot be negative")
    if not 0.5 < rejection_close_fraction < 1.0:
        raise ValueError("rejection_close_fraction must be between 0.5 and 1.0")

    required = {"high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("OHLCV frame missing columns: " + ", ".join(missing))

    previous_low = (
        frame["low"]
        .rolling(lookback, min_periods=lookback)
        .min()
        .shift(1)
    )
    previous_high = (
        frame["high"]
        .rolling(lookback, min_periods=lookback)
        .max()
        .shift(1)
    )

    sweep_fraction = min_sweep_bps / 10_000.0
    candle_range = (frame["high"] - frame["low"]).where(
        frame["high"] > frame["low"]
    )
    close_location = (frame["close"] - frame["low"]) / candle_range
    tradable = frame["volume"] > 0

    long_signal = (
        previous_low.notna()
        & (frame["low"] <= previous_low * (1.0 - sweep_fraction))
        & (frame["close"] > previous_low)
        & (close_location >= rejection_close_fraction)
        & tradable
    )
    short_signal = (
        previous_high.notna()
        & (frame["high"] >= previous_high * (1.0 + sweep_fraction))
        & (frame["close"] < previous_high)
        & (close_location <= 1.0 - rejection_close_fraction)
        & tradable
    )
    return SignalSet(long=long_signal, short=short_signal)
