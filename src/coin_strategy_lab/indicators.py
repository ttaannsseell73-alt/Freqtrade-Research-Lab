from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).mean()


def ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def rma(s: pd.Series, length: int) -> pd.Series:
    values = s.astype(float).to_numpy()
    out = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return pd.Series(out, index=s.index)
    seed = np.nanmean(values[:length])
    if np.isnan(seed):
        return pd.Series(out, index=s.index)
    out[length - 1] = seed
    alpha = 1.0 / length
    for i in range(length, len(values)):
        v = values[i]
        prev = out[i - 1]
        if np.isnan(v) or np.isnan(prev):
            out[i] = prev
        else:
            out[i] = alpha * v + (1.0 - alpha) * prev
    return pd.Series(out, index=s.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    a = df["high"] - df["low"]
    b = (df["high"] - prev_close).abs()
    c = (df["low"] - prev_close).abs()
    return pd.concat([a, b, c], axis=1).max(axis=1)


def atr(df: pd.DataFrame, length: int, *, method: str = "rma") -> pd.Series:
    tr = true_range(df)
    return rma(tr, length) if method == "rma" else sma(tr, length)


def rsi(s: pd.Series, length: int) -> pd.Series:
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = rma(gain.fillna(0.0), length)
    avg_loss = rma(loss.fillna(0.0), length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain != 0.0, 0.0)
    both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
    return out.where(~both_zero, 50.0)


def mfi(df: pd.DataFrame, length: int) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    raw = tp * df["volume"]
    delta = tp.diff()
    pos = raw.where(delta > 0.0, 0.0)
    neg = raw.where(delta < 0.0, 0.0)
    pos_sum = pos.rolling(length, min_periods=length).sum()
    neg_sum = neg.rolling(length, min_periods=length).sum()
    ratio = pos_sum / neg_sum.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + ratio))
    out = out.where(neg_sum != 0.0, 100.0)
    return out


def rolling_linreg_last(s: pd.Series, length: int) -> pd.Series:
    x = np.arange(length, dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def calc(values: np.ndarray) -> float:
        y = values.astype(float)
        y_mean = y.mean()
        slope = ((x - x_mean) * (y - y_mean)).sum() / denom
        intercept = y_mean - slope * x_mean
        return float(intercept + slope * (length - 1))

    return s.rolling(length, min_periods=length).apply(calc, raw=True)


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a > b) & (a.shift(1) <= b.shift(1))).fillna(False)


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a < b) & (a.shift(1) >= b.shift(1))).fillna(False)
