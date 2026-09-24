from __future__ import annotations

from typing import Any, Mapping
import numpy as np
import pandas as pd

from ..contracts import StrategyPlugin, StrategySpec, Timeframe
from ..indicators import atr, crossover, crossunder, ema


class PMax(StrategyPlugin):
    spec = StrategySpec(
        strategy_id="pmax",
        name="PMax Explorer",
        version="1.0.0",
        supported_timeframes=tuple(Timeframe),
        warmup_bars=80,
        default_parameters={
            "atr_length": 10,
            "atr_multiplier": 3.0,
            "ma_length": 10,
            "signal_mode": "ma_cross",
        },
        source_url="https://www.tradingview.com/script/nHGK4Qtp/",
    )

    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        self.validate_input(candles)
        p = dict(self.spec.default_parameters)
        if parameters:
            p.update(parameters)
        out = candles.copy()
        src = (out["high"].astype(float) + out["low"].astype(float)) / 2.0
        ma = ema(src, int(p["ma_length"]))
        atr_s = atr(out, int(p["atr_length"]), method="rma")
        mult = float(p["atr_multiplier"])

        long_stop = np.full(len(out), np.nan)
        short_stop = np.full(len(out), np.nan)
        pmax_line = np.full(len(out), np.nan)
        direction = np.full(len(out), np.nan)
        prev_long = prev_short = np.nan
        prev_dir = 1.0

        for i in range(len(out)):
            if np.isnan(ma.iloc[i]) or np.isnan(atr_s.iloc[i]):
                continue
            raw_long = float(ma.iloc[i] - mult * atr_s.iloc[i])
            raw_short = float(ma.iloc[i] + mult * atr_s.iloc[i])
            if np.isnan(prev_long):
                cur_long, cur_short = raw_long, raw_short
            else:
                cur_long = max(raw_long, prev_long) if float(ma.iloc[i]) > prev_long else raw_long
                cur_short = min(raw_short, prev_short) if float(ma.iloc[i]) < prev_short else raw_short
            cur_dir = prev_dir
            if not np.isnan(prev_short) and prev_dir == -1 and float(ma.iloc[i]) > prev_short:
                cur_dir = 1.0
            elif not np.isnan(prev_long) and prev_dir == 1 and float(ma.iloc[i]) < prev_long:
                cur_dir = -1.0
            long_stop[i], short_stop[i], direction[i] = cur_long, cur_short, cur_dir
            pmax_line[i] = cur_long if cur_dir == 1 else cur_short
            prev_long, prev_short, prev_dir = cur_long, cur_short, cur_dir

        out["pmax_ma"] = ma
        out["pmax"] = pmax_line
        out.attrs["signal_mode"] = str(p["signal_mode"])
        return out

    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        if prepared.attrs.get("signal_mode") == "price_cross":
            return crossover(prepared["close"], prepared["pmax"])
        return crossover(prepared["pmax_ma"], prepared["pmax"])

    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        if prepared.attrs.get("signal_mode") == "price_cross":
            return crossunder(prepared["close"], prepared["pmax"])
        return crossunder(prepared["pmax_ma"], prepared["pmax"])
