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
