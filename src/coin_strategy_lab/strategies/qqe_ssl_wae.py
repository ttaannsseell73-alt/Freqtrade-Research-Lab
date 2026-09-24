from __future__ import annotations

from typing import Any, Mapping
import math
import numpy as np
import pandas as pd

from ..contracts import StrategyPlugin, StrategySpec, Timeframe
from ..indicators import ema, rsi, sma, true_range


def _wma(s: pd.Series, length: int) -> pd.Series:
    w = np.arange(1, length + 1, dtype=float)
    return s.rolling(length, min_periods=length).apply(
        lambda x: float(np.dot(x, w) / w.sum()), raw=True
    )


def _hma(s: pd.Series, length: int) -> pd.Series:
    half = max(1, int(length / 2))
    root = max(1, int(round(math.sqrt(length))))
    return _wma(2.0 * _wma(s, half) - _wma(s, length), root)


def _qqe_line(close: pd.Series, rsi_length: int, smoothing: int, factor: float) -> tuple[pd.Series, pd.Series]:
    rsi_ma = ema(rsi(close, rsi_length), smoothing)
    wilders = rsi_length * 2 - 1
    atr_rsi = (rsi_ma.shift(1) - rsi_ma).abs()
    ma_atr = ema(atr_rsi, wilders)
    dar = ema(ma_atr, wilders) * factor

    rs = rsi_ma.to_numpy(float)
    dv = dar.to_numpy(float)
    longband = np.full(len(close), np.nan)
    shortband = np.full(len(close), np.nan)

    prev_long = prev_short = np.nan
    prev_rs = np.nan
    for i in range(len(close)):
        if np.isnan(rs[i]) or np.isnan(dv[i]):
            continue
        new_short = rs[i] + dv[i]
        new_long = rs[i] - dv[i]
        if np.isnan(prev_long):
            cur_long, cur_short = new_long, new_short
        else:
            cur_long = max(prev_long, new_long) if prev_rs > prev_long and rs[i] > prev_long else new_long
            cur_short = min(prev_short, new_short) if prev_rs < prev_short and rs[i] < prev_short else new_short
        longband[i], shortband[i] = cur_long, cur_short
        prev_long, prev_short, prev_rs = cur_long, cur_short, rs[i]

    rs_s = pd.Series(rs, index=close.index)
    long_s = pd.Series(longband, index=close.index)
    short_s = pd.Series(shortband, index=close.index)

    # Pine:
    # cross_1 = ta.cross(longband[1], RSIndex)
    # trend := ta.cross(RSIndex, shortband[1]) ? 1 : cross_1 ? -1 : nz(trend[1], 1)
    cross_short = (
        ((rs_s > short_s.shift(1)) & (rs_s.shift(1) <= short_s.shift(2)))
        | ((rs_s < short_s.shift(1)) & (rs_s.shift(1) >= short_s.shift(2)))
    ).fillna(False)
    cross_long = (
        ((long_s.shift(1) > rs_s) & (long_s.shift(2) <= rs_s.shift(1)))
        | ((long_s.shift(1) < rs_s) & (long_s.shift(2) >= rs_s.shift(1)))
    ).fillna(False)

    trend = np.full(len(close), np.nan)
    prev_trend = 1.0
    for i in range(len(close)):
        if bool(cross_short.iloc[i]):
            prev_trend = 1.0
        elif bool(cross_long.iloc[i]):
            prev_trend = -1.0
        trend[i] = prev_trend

    fast_tl = np.where(trend == 1.0, longband, shortband)
    return rsi_ma, pd.Series(fast_tl, index=close.index)


class QQESSLWAE(StrategyPlugin):
    spec = StrategySpec(
        strategy_id="qqe_ssl_wae",
        name="QQE MOD + SSL Hybrid + Waddah Attar Explosion",
        version="1.0.0",
        supported_timeframes=tuple(Timeframe),
        warmup_bars=180,
        default_parameters={
            "rsi_length": 6,
            "rsi_smoothing": 6,
            "qqe_factor": 3.0,
            "qqe_bb_length": 50,
            "qqe_bb_mult": 0.35,
            "rsi2_length": 6,
            "rsi2_smoothing": 5,
            "rsi2_threshold": 3.0,
            "ssl_length": 60,
            "ssl_channel_mult": 0.2,
            "wae_sensitivity": 180.0,
            "wae_fast": 20,
            "wae_slow": 40,
            "wae_channel_length": 20,
            "wae_bb_mult": 2.0,
        },
        source_url="https://www.tradingview.com/script/YCob5r03-QQE-MOD-SSL-Hybrid-Waddah-Attar-Explosion/",
    )

    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        self.validate_input(candles)
        p = dict(self.spec.default_parameters)
        if parameters:
            p.update(parameters)
        out = candles.copy()
        close = out["close"].astype(float)

        # QQE MOD
        rsi_ma, fast_tl = _qqe_line(
            close, int(p["rsi_length"]), int(p["rsi_smoothing"]), float(p["qqe_factor"])
        )
        q = fast_tl - 50.0
        basis = sma(q, int(p["qqe_bb_length"]))
        dev = q.rolling(int(p["qqe_bb_length"]), min_periods=int(p["qqe_bb_length"])).std(ddof=0) * float(p["qqe_bb_mult"])
        upper, lower = basis + dev, basis - dev

        rsi2_ma = ema(rsi(close, int(p["rsi2_length"])), int(p["rsi2_smoothing"]))
        green1 = (rsi2_ma - 50.0) > float(p["rsi2_threshold"])
        green2 = (rsi_ma - 50.0) > upper
        red1 = (rsi2_ma - 50.0) < -float(p["rsi2_threshold"])
        red2 = (rsi_ma - 50.0) < lower
        qqe_green = (green1 & green2).fillna(False)
        qqe_red = (red1 & red2).fillna(False)
        out["qqe_buy"] = qqe_green & ~qqe_green.shift(1, fill_value=False)
        out["qqe_sell"] = qqe_red & ~qqe_red.shift(1, fill_value=False)

        # SSL Hybrid default baseline: HMA(60), Keltner channel around that HMA.
        ssl_len = int(p["ssl_length"])
        bbmc = _hma(close, ssl_len)
        keltma = _hma(close, ssl_len)
        range_ma = ema(true_range(out), ssl_len)
        upperk = keltma + range_ma * float(p["ssl_channel_mult"])
        lowerk = keltma - range_ma * float(p["ssl_channel_mult"])
        out["ssl_buy"] = ((close > upperk) & (close > bbmc)).fillna(False)
        out["ssl_sell"] = ((close < lowerk) & (close < bbmc)).fillna(False)

        # Waddah Attar Explosion
        macd = ema(close, int(p["wae_fast"])) - ema(close, int(p["wae_slow"]))
        t1 = (macd - macd.shift(1)) * float(p["wae_sensitivity"])
        trend_up = t1.clip(lower=0.0)
        trend_down = (-t1).clip(lower=0.0)
        ch = int(p["wae_channel_length"])
        explosion = close.rolling(ch, min_periods=ch).std(ddof=0) * 2.0 * float(p["wae_bb_mult"])
        out["wae_buy"] = ((trend_up > 0.0) & (trend_up > explosion)).fillna(False)
        out["wae_sell"] = ((trend_down > 0.0) & (trend_down > explosion)).fillna(False)
        return out

    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        return (prepared["qqe_buy"] & prepared["ssl_buy"] & prepared["wae_buy"]).fillna(False).astype(bool)

    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        return (prepared["qqe_sell"] & prepared["ssl_sell"] & prepared["wae_sell"]).fillna(False).astype(bool)
