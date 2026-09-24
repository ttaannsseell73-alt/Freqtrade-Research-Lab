from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

from ..contracts import StrategyPlugin, StrategySpec, Timeframe


def _wma(series: pd.Series, length: int) -> pd.Series:
    weights = np.arange(1, length + 1, dtype=float)
    denom = weights.sum()
    return series.rolling(length, min_periods=length).apply(
        lambda values: float(np.dot(values, weights) / denom),
        raw=True,
    )


class MavilimW(StrategyPlugin):
    spec = StrategySpec(
        strategy_id="mavilimw",
        name="MavilimW",
        version="1.0.0",
        supported_timeframes=tuple(Timeframe),
        warmup_bars=90,
        default_parameters={"fmal": 3, "smal": 5, "signal_mode": "color_flip"},
        source_url="https://www.tradingview.com/script/IAssyObN-MavilimW/",
    )

    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        self.validate_input(candles)
        p = dict(self.spec.default_parameters)
        if parameters:
            p.update(parameters)
        fmal = int(p["fmal"])
        smal = int(p["smal"])
        tmal = fmal + smal
        f_plus_t = smal + tmal
        ft_plus_t = tmal + f_plus_t
        final_len = f_plus_t + ft_plus_t

        out = candles.copy()
        line = _wma(out["close"].astype(float), fmal)
        line = _wma(line, smal)
        line = _wma(line, tmal)
        line = _wma(line, f_plus_t)
        line = _wma(line, ft_plus_t)
        out["mavilimw"] = _wma(line, final_len)
        out.attrs["signal_mode"] = p["signal_mode"]
        return out

    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        m = prepared["mavilimw"]
        mode = prepared.attrs.get("signal_mode", "color_flip")
        if mode == "price_cross":
            c = prepared["close"]
            return ((c > m) & (c.shift(1) <= m.shift(1))).fillna(False)
        return ((m > m.shift(1)) & (m.shift(1) <= m.shift(2))).fillna(False)

    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        m = prepared["mavilimw"]
        mode = prepared.attrs.get("signal_mode", "color_flip")
        if mode == "price_cross":
            c = prepared["close"]
            return ((c < m) & (c.shift(1) >= m.shift(1))).fillna(False)
        return ((m < m.shift(1)) & (m.shift(1) >= m.shift(2))).fillna(False)
