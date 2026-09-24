from __future__ import annotations

from typing import Any, Mapping
import pandas as pd

from ..contracts import StrategyPlugin, StrategySpec, Timeframe
from ..indicators import rolling_linreg_last, sma


class SqueezeMomentum(StrategyPlugin):
    spec = StrategySpec(
        strategy_id="squeeze_momentum",
        name="Squeeze Momentum",
        version="1.0.0",
        supported_timeframes=tuple(Timeframe),
        warmup_bars=80,
        default_parameters={
            "bb_length": 20,
            "bb_mult": 2.0,
            "kc_length": 20,
            "kc_mult": 1.5,
            "require_release": True,
            "logic": "type2",
        },
        source_url="https://www.tradingview.com/script/nqQ1DT5a-Squeeze-Momentum-Indicator-LazyBear/",
    )

    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        self.validate_input(candles)
        p = dict(self.spec.default_parameters)
        if parameters:
            p.update(parameters)
        out = candles.copy()
        src = (out["open"] + out["high"] + out["low"] + out["close"]) / 4.0
        bb_len = int(p["bb_length"])
        kc_len = int(p["kc_length"])
        basis = sma(src, bb_len)
        dev = src.rolling(bb_len, min_periods=bb_len).std(ddof=0) * float(p["bb_mult"])
        upper_bb, lower_bb = basis + dev, basis - dev
        kc_mid = sma(src, kc_len)
        range_ma = sma(out["high"] - out["low"], kc_len)
        upper_kc = kc_mid + range_ma * float(p["kc_mult"])
        lower_kc = kc_mid - range_ma * float(p["kc_mult"])
        sqz_on = (lower_bb > lower_kc) & (upper_bb < upper_kc)
        sqz_off = (lower_bb < lower_kc) & (upper_bb > upper_kc)

        hl2 = (out["high"] + out["low"]) / 2.0
        center = (
            (
                out["high"].rolling(kc_len, min_periods=kc_len).max()
                + out["low"].rolling(kc_len, min_periods=kc_len).min()
            ) / 2.0
            + sma(hl2, kc_len)
        ) / 2.0
        out["sqz_val"] = rolling_linreg_last(src - center, kc_len)
        out["sqz_on"] = sqz_on.fillna(False)
        out["sqz_off"] = sqz_off.fillna(False)
        out.attrs["require_release"] = bool(p["require_release"])
        out.attrs["logic"] = str(p["logic"])
        return out

    def _release(self, prepared: pd.DataFrame) -> pd.Series:
        return prepared["sqz_off"] if prepared.attrs.get("require_release", True) else pd.Series(True, index=prepared.index)

    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        v = prepared["sqz_val"]
        rising_turn = (v > v.shift(1)) & (v.shift(1) <= v.shift(2))
        if prepared.attrs.get("logic") == "type2":
            rising_turn &= v < 0
        return (rising_turn & self._release(prepared)).fillna(False)

    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        v = prepared["sqz_val"]
        falling_turn = (v < v.shift(1)) & (v.shift(1) >= v.shift(2))
        if prepared.attrs.get("logic") == "type2":
            falling_turn &= v > 0
        return (falling_turn & self._release(prepared)).fillna(False)
