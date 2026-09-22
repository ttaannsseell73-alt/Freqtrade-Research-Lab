from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class ProgressiveTrendTrackerScalp(IStrategy):
    """PTT price-cross benchmark using the published default periods."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 40
    process_only_new_candles = True

    minimal_roi = {"0": 0.008, "30": -1.0}
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    faster_period = 5
    period = 5
    ma_period = 2
    slower_period = 10
    stddev_multiplier = 2.0

    @staticmethod
    def _vidya(source: pd.Series, length: int) -> pd.Series:
        alpha = 2.0 / (length + 1.0)
        delta = source.diff()
        up = delta.clip(lower=0.0).rolling(9, min_periods=9).sum()
        down = (-delta.clip(upper=0.0)).rolling(9, min_periods=9).sum()
        denom = up + down
        cmo = ((up - down) / denom.replace(0.0, np.nan)).abs().fillna(0.0)

        result = np.full(len(source), np.nan, dtype=float)
        for pos in range(len(source)):
            value = source.iloc[pos]
            if pd.isna(value):
                continue
            previous = 0.0
            if pos > 0 and np.isfinite(result[pos - 1]):
                previous = float(result[pos - 1])
            weight = alpha * float(cmo.iloc[pos])
            result[pos] = weight * float(value) + (1.0 - weight) * previous
        return pd.Series(result, index=source.index, dtype=float)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        hhv = dataframe["high"].rolling(
            self.faster_period,
            min_periods=self.faster_period,
        ).max()
        llv = dataframe["low"].rolling(
            self.faster_period,
            min_periods=self.faster_period,
        ).min()
        hhv_period = hhv.rolling(
            self.period,
            min_periods=self.period,
        ).max()
        llv_period = llv.rolling(
            self.period,
            min_periods=self.period,
        ).min()

        upper_ma = self._vidya(hhv_period, self.ma_period)
        lower_ma = self._vidya(llv_period, self.ma_period)
        upper_std = hhv_period.rolling(
            self.ma_period,
            min_periods=self.ma_period,
        ).std(ddof=0)
        lower_std = llv_period.rolling(
            self.ma_period,
            min_periods=self.ma_period,
        ).std(ddof=0)

        upper_band = upper_ma + self.stddev_multiplier * upper_std
        lower_band = lower_ma - self.stddev_multiplier * lower_std

        dataframe["ptt_upper"] = upper_band.rolling(
            self.slower_period,
            min_periods=self.slower_period,
        ).min()
        dataframe["ptt_lower"] = lower_band.rolling(
            self.slower_period,
            min_periods=self.slower_period,
        ).max()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        close = dataframe["close"]
        lower = dataframe["ptt_lower"]
        upper = dataframe["ptt_upper"]
        liquid = dataframe["volume"] > 0

        long_signal = (
            lower.notna()
            & (close > lower)
            & (close.shift(1) <= lower.shift(1))
            & liquid
        )
        short_signal = (
            upper.notna()
            & (close < upper)
            & (close.shift(1) >= upper.shift(1))
            & liquid
        )
        both = long_signal & short_signal
        long_signal &= ~both
        short_signal &= ~both

        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "ptt_buy",
        )
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (
            1,
            "ptt_sell",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0
