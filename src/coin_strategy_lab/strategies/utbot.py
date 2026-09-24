from __future__ import annotations

from typing import Any, Mapping
import numpy as np
import pandas as pd

from ..contracts import StrategyPlugin, StrategySpec, Timeframe
from ..indicators import atr, crossover, crossunder


class UTBot(StrategyPlugin):
    spec = StrategySpec(
        strategy_id="utbot",
        name="UT Bot Alerts",
        version="1.0.0",
        supported_timeframes=tuple(Timeframe),
        warmup_bars=60,
        default_parameters={"key_value": 1.0, "atr_period": 10},
        source_url="https://www.tradingview.com/script/n8ss8BID-UT-Bot-Alerts/",
    )

    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        self.validate_input(candles)
        p = dict(self.spec.default_parameters)
        if parameters:
            p.update(parameters)
        out = candles.copy()
        src = out["close"].astype(float)
        loss = atr(out, int(p["atr_period"]), method="rma") * float(p["key_value"])
        stop = np.full(len(out), np.nan)
        prev_stop = np.nan

        for i in range(len(out)):
            if np.isnan(loss.iloc[i]):
                continue
            s = float(src.iloc[i])
            prev_s = float(src.iloc[i - 1]) if i > 0 else s
            if np.isnan(prev_stop):
                cur = s - float(loss.iloc[i])
            elif s > prev_stop and prev_s > prev_stop:
                cur = max(prev_stop, s - float(loss.iloc[i]))
            elif s < prev_stop and prev_s < prev_stop:
                cur = min(prev_stop, s + float(loss.iloc[i]))
            elif s > prev_stop:
                cur = s - float(loss.iloc[i])
            else:
                cur = s + float(loss.iloc[i])
            stop[i] = cur
            prev_stop = cur

        out["utbot_stop"] = stop
        return out

    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        c = prepared["close"]
        s = prepared["utbot_stop"]
        return (c > s) & crossover(c, s)

    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        c = prepared["close"]
        s = prepared["utbot_stop"]
        return (c < s) & crossunder(c, s)
