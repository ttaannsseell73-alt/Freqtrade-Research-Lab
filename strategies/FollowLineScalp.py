from __future__ import annotations

import numpy as np
import pandas as pd
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class FollowLineScalp(IStrategy):
    """KivancOzbilgic Follow Line benchmark using the published defaults."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 35
    process_only_new_candles = True

    minimal_roi = {"0": 0.008, "30": -1.0}
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    atr_period = 5
    bb_period = 21
    bb_deviation = 1.0

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        basis = dataframe["close"].rolling(
            self.bb_period,
            min_periods=self.bb_period,
        ).mean()
        deviation = dataframe["close"].rolling(
            self.bb_period,
            min_periods=self.bb_period,
        ).std(ddof=0)
        upper = basis + deviation * self.bb_deviation
        lower = basis - deviation * self.bb_deviation

        previous_close = dataframe["close"].shift(1)
        true_range = pd.concat(
            [
                dataframe["high"] - dataframe["low"],
                (dataframe["high"] - previous_close).abs(),
                (dataframe["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = true_range.ewm(
            alpha=1.0 / self.atr_period,
            adjust=False,
            min_periods=self.atr_period,
        ).mean()

        follow = np.full(len(dataframe), np.nan, dtype=float)
        trend = np.zeros(len(dataframe), dtype=int)
        bb_signal = 0

        for pos in range(len(dataframe)):
            close = float(dataframe["close"].iloc[pos])
            if pd.notna(upper.iloc[pos]) and close > float(upper.iloc[pos]):
                bb_signal = 1
            elif pd.notna(lower.iloc[pos]) and close < float(lower.iloc[pos]):
                bb_signal = -1

            prev_follow = 0.0
            if pos > 0 and np.isfinite(follow[pos - 1]):
                prev_follow = float(follow[pos - 1])

            if bb_signal == 1 and pd.notna(atr.iloc[pos]):
                candidate = float(dataframe["low"].iloc[pos] - atr.iloc[pos])
                follow[pos] = max(candidate, prev_follow)
            elif bb_signal == -1 and pd.notna(atr.iloc[pos]):
                candidate = float(dataframe["high"].iloc[pos] + atr.iloc[pos])
                follow[pos] = min(candidate, prev_follow)
            elif pos > 0:
                follow[pos] = follow[pos - 1]

            current = 0.0 if not np.isfinite(follow[pos]) else float(follow[pos])
            previous = (
                0.0
                if pos == 0 or not np.isfinite(follow[pos - 1])
                else float(follow[pos - 1])
            )
            if current > previous:
                trend[pos] = 1
            elif current < previous:
                trend[pos] = -1
            elif pos > 0:
                trend[pos] = trend[pos - 1]

        dataframe["follow_line"] = follow
        dataframe["follow_trend"] = trend
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        previous = dataframe["follow_trend"].shift(1)
        long_signal = (
            (previous == -1)
            & (dataframe["follow_trend"] == 1)
            & (dataframe["volume"] > 0)
        )
        short_signal = (
            (previous == 1)
            & (dataframe["follow_trend"] == -1)
            & (dataframe["volume"] > 0)
        )
        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "follow_line_buy",
        )
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (
            1,
            "follow_line_sell",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0
