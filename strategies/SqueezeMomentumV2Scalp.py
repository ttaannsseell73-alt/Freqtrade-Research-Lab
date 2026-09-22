from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class SqueezeMomentumV2Scalp(IStrategy):
    """KivancOzbilgic/LazyBear SQZMOM v2 signal-line crossover benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 50
    process_only_new_candles = True

    minimal_roi = {"0": 0.008, "30": -1.0}
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    length = 20
    signal_period = 5

    @staticmethod
    def _linreg_last(values: np.ndarray) -> float:
        y = np.asarray(values, dtype=float)
        if np.isnan(y).any():
            return np.nan
        n = len(y)
        x = np.arange(n, dtype=float)
        x_mean = (n - 1) / 2.0
        y_mean = float(y.mean())
        denominator = float(((x - x_mean) ** 2).sum())
        if denominator == 0:
            return y_mean
        slope = float(((x - x_mean) * (y - y_mean)).sum() / denominator)
        intercept = y_mean - slope * x_mean
        return intercept + slope * (n - 1)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        highest = dataframe["high"].rolling(
            self.length,
            min_periods=self.length,
        ).max()
        lowest = dataframe["low"].rolling(
            self.length,
            min_periods=self.length,
        ).min()
        close_ma = dataframe["close"].rolling(
            self.length,
            min_periods=self.length,
        ).mean()
        midpoint = (((highest + lowest) / 2.0) + close_ma) / 2.0
        raw = dataframe["close"] - midpoint

        dataframe["sqz_val"] = raw.rolling(
            self.length,
            min_periods=self.length,
        ).apply(self._linreg_last, raw=True)
        dataframe["sqz_signal"] = dataframe["sqz_val"].rolling(
            self.signal_period,
            min_periods=self.signal_period,
        ).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        val = dataframe["sqz_val"]
        signal = dataframe["sqz_signal"]
        liquid = dataframe["volume"] > 0

        long_signal = (
            (val > signal)
            & (val.shift(1) <= signal.shift(1))
            & liquid
        )
        short_signal = (
            (val < signal)
            & (val.shift(1) >= signal.shift(1))
            & liquid
        )
        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "sqzmom_v2_buy",
        )
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (
            1,
            "sqzmom_v2_sell",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0
