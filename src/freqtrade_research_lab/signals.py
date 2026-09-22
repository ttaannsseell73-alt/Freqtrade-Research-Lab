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


def breakout_retest_signals(
    frame: pd.DataFrame,
    *,
    lookback: int = 20,
    breakout_buffer_bps: float = 5.0,
    retest_tolerance_bps: float = 10.0,
    close_strength_fraction: float = 0.55,
) -> SignalSet:
    """Immediate-next-candle breakout/retest signals without future leakage.

    Long setup:
    - previous candle closed above its prior `lookback`-bar high by at least
      `breakout_buffer_bps`,
    - current candle retests that broken level within
      `retest_tolerance_bps`,
    - current close holds back above the broken level,
    - current close finishes in the upper `close_strength_fraction` of its
      candle range.

    Short is the mirrored rule around the prior rolling low.
    """
    if lookback < 2:
        raise ValueError("lookback must be at least 2")
    if breakout_buffer_bps < 0:
        raise ValueError("breakout_buffer_bps cannot be negative")
    if retest_tolerance_bps < 0:
        raise ValueError("retest_tolerance_bps cannot be negative")
    if not 0.5 <= close_strength_fraction < 1.0:
        raise ValueError("close_strength_fraction must be in [0.5, 1.0)")

    required = {"high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("OHLCV frame missing columns: " + ", ".join(missing))

    prior_high = (
        frame["high"]
        .rolling(lookback, min_periods=lookback)
        .max()
        .shift(1)
    )
    prior_low = (
        frame["low"]
        .rolling(lookback, min_periods=lookback)
        .min()
        .shift(1)
    )

    breakout_buffer = breakout_buffer_bps / 10_000.0
    retest_tolerance = retest_tolerance_bps / 10_000.0

    previous_long_breakout = (
        frame["close"].shift(1)
        >= prior_high.shift(1) * (1.0 + breakout_buffer)
    )
    previous_short_breakout = (
        frame["close"].shift(1)
        <= prior_low.shift(1) * (1.0 - breakout_buffer)
    )
    long_level = prior_high.shift(1)
    short_level = prior_low.shift(1)

    candle_range = (frame["high"] - frame["low"]).where(
        frame["high"] > frame["low"]
    )
    close_location = (frame["close"] - frame["low"]) / candle_range
    tradable = frame["volume"] > 0

    long_signal = (
        previous_long_breakout
        & long_level.notna()
        & (frame["low"] <= long_level * (1.0 + retest_tolerance))
        & (frame["low"] >= long_level * (1.0 - retest_tolerance))
        & (frame["close"] > long_level)
        & (close_location >= close_strength_fraction)
        & tradable
    )
    short_signal = (
        previous_short_breakout
        & short_level.notna()
        & (frame["high"] >= short_level * (1.0 - retest_tolerance))
        & (frame["high"] <= short_level * (1.0 + retest_tolerance))
        & (frame["close"] < short_level)
        & (close_location <= 1.0 - close_strength_fraction)
        & tradable
    )

    return SignalSet(long=long_signal, short=short_signal)


def compression_expansion_signals(
    frame: pd.DataFrame,
    *,
    compression_window: int = 10,
    baseline_window: int = 50,
    compression_ratio: float = 0.60,
    expansion_ratio: float = 1.50,
    breakout_buffer_bps: float = 5.0,
    close_strength_fraction: float = 0.65,
) -> SignalSet:
    """Price-only compression -> expansion breakout signal without lookahead.

    Compression is measured from the completed prior `compression_window`
    range relative to its own historical median range-width baseline.
    Expansion requires the current candle's normalized range to exceed the
    median normalized candle range of the prior compression window.
    """
    if compression_window < 3:
        raise ValueError("compression_window must be at least 3")
    if baseline_window < compression_window:
        raise ValueError("baseline_window must be >= compression_window")
    if not 0 < compression_ratio < 1:
        raise ValueError("compression_ratio must be between 0 and 1")
    if expansion_ratio <= 1:
        raise ValueError("expansion_ratio must be greater than 1")
    if breakout_buffer_bps < 0:
        raise ValueError("breakout_buffer_bps cannot be negative")
    if not 0.5 < close_strength_fraction < 1.0:
        raise ValueError("close_strength_fraction must be between 0.5 and 1.0")

    required = {"high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("OHLCV frame missing columns: " + ", ".join(missing))

    rolling_high = frame["high"].rolling(
        compression_window, min_periods=compression_window
    ).max()
    rolling_low = frame["low"].rolling(
        compression_window, min_periods=compression_window
    ).min()
    range_width = (rolling_high - rolling_low) / frame["close"]

    prior_range_width = range_width.shift(1)
    historical_width_baseline = (
        range_width
        .shift(compression_window + 1)
        .rolling(baseline_window, min_periods=baseline_window)
        .median()
    )
    compressed = (
        prior_range_width.notna()
        & historical_width_baseline.notna()
        & (prior_range_width <= historical_width_baseline * compression_ratio)
    )

    prior_high = rolling_high.shift(1)
    prior_low = rolling_low.shift(1)

    previous_close = frame["close"].shift(1)
    normalized_candle_range = (frame["high"] - frame["low"]) / previous_close
    prior_range_baseline = (
        normalized_candle_range
        .rolling(compression_window, min_periods=compression_window)
        .median()
        .shift(1)
    )
    expanding = (
        prior_range_baseline.notna()
        & (normalized_candle_range >= prior_range_baseline * expansion_ratio)
    )

    candle_range = (frame["high"] - frame["low"]).where(
        frame["high"] > frame["low"]
    )
    close_location = (frame["close"] - frame["low"]) / candle_range
    breakout_buffer = breakout_buffer_bps / 10_000.0
    tradable = frame["volume"] > 0

    long_signal = (
        compressed
        & expanding
        & prior_high.notna()
        & (frame["close"] >= prior_high * (1.0 + breakout_buffer))
        & (close_location >= close_strength_fraction)
        & tradable
    )
    short_signal = (
        compressed
        & expanding
        & prior_low.notna()
        & (frame["close"] <= prior_low * (1.0 - breakout_buffer))
        & (close_location <= 1.0 - close_strength_fraction)
        & tradable
    )
    return SignalSet(long=long_signal, short=short_signal)
