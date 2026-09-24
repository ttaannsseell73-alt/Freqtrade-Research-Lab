from __future__ import annotations

from typing import Any, Mapping
import numpy as np
import pandas as pd

from ..contracts import StrategyPlugin, StrategySpec, Timeframe
from ..indicators import atr, crossover, crossunder, mfi, rsi


class AlphaTrend(StrategyPlugin):
    spec = StrategySpec(
        strategy_id="alphatrend",
        name="AlphaTrend",
        version="1.0.0",
        supported_timeframes=tuple(Timeframe),
        warmup_bars=80,
        default_parameters={"period": 14, "multiplier": 1.0, "no_volume_data": False},
        source_url="https://www.tradingview.com/script/uvkI3QJC/",
    )

    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        self.validate_input(candles)
        p = dict(self.spec.default_parameters)
        if parameters:
            p.update(parameters)
        period = int(p["period"])
        mult = float(p["multiplier"])
        out = candles.copy()
        atr_s = atr(out, period, method="sma")
        up_t = out["low"].astype(float) - atr_s * mult
        down_t = out["high"].astype(float) + atr_s * mult
        gate = rsi(out["close"].astype(float), period) >= 50.0 if bool(p["no_volume_data"]) else mfi(out, period) >= 50.0

        result = np.full(len(out), np.nan, dtype=float)
        prev = np.nan
        for i in range(len(out)):
            if np.isnan(up_t.iloc[i]) or np.isnan(down_t.iloc[i]) or pd.isna(gate.iloc[i]):
                continue
            if np.isnan(prev):
                current = float(up_t.iloc[i] if bool(gate.iloc[i]) else down_t.iloc[i])
            elif bool(gate.iloc[i]):
                current = prev if float(up_t.iloc[i]) < prev else float(up_t.iloc[i])
            else:
                current = prev if float(down_t.iloc[i]) > prev else float(down_t.iloc[i])
            result[i] = current
            prev = current
        out["alphatrend"] = result
        return out

    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        a = prepared["alphatrend"]
        return crossover(a, a.shift(2))

    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        a = prepared["alphatrend"]
        return crossunder(a, a.shift(2))
