from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class BreakoutRetestScalp(IStrategy):
    """Pre-registered immediate-next-candle breakout/retest scalping benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 22
    process_only_new_candles = True

    # Same fixed execution overlay as the first scalp benchmark so signal
    # quality is compared under the same trade-management assumptions.
    minimal_roi = {
        "0": 0.008,
        "30": -1.0,
    }
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    lookback = 20
    breakout_buffer_bps = 5.0
    retest_tolerance_bps = 10.0
    close_strength_fraction = 0.55

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["prior_range_high"] = (
            dataframe["high"]
            .rolling(self.lookback, min_periods=self.lookback)
            .max()
            .shift(1)
        )
        dataframe["prior_range_low"] = (
            dataframe["low"]
            .rolling(self.lookback, min_periods=self.lookback)
            .min()
            .shift(1)
        )
        candle_range = (dataframe["high"] - dataframe["low"]).where(
            dataframe["high"] > dataframe["low"]
        )
        dataframe["close_location"] = (
            (dataframe["close"] - dataframe["low"]) / candle_range
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        breakout_buffer = self.breakout_buffer_bps / 10_000.0
        retest_tolerance = self.retest_tolerance_bps / 10_000.0

        previous_long_breakout = (
            dataframe["close"].shift(1)
            >= dataframe["prior_range_high"].shift(1) * (1.0 + breakout_buffer)
        )
        previous_short_breakout = (
            dataframe["close"].shift(1)
            <= dataframe["prior_range_low"].shift(1) * (1.0 - breakout_buffer)
        )
        long_level = dataframe["prior_range_high"].shift(1)
        short_level = dataframe["prior_range_low"].shift(1)
        liquid = dataframe["volume"] > 0

        long_signal = (
            previous_long_breakout
            & long_level.notna()
            & (dataframe["low"] <= long_level * (1.0 + retest_tolerance))
            & (dataframe["low"] >= long_level * (1.0 - retest_tolerance))
            & (dataframe["close"] > long_level)
            & (dataframe["close_location"] >= self.close_strength_fraction)
            & liquid
        )
        short_signal = (
            previous_short_breakout
            & short_level.notna()
            & (dataframe["high"] >= short_level * (1.0 - retest_tolerance))
            & (dataframe["high"] <= short_level * (1.0 + retest_tolerance))
            & (dataframe["close"] < short_level)
            & (
                dataframe["close_location"]
                <= 1.0 - self.close_strength_fraction
            )
            & liquid
        )

        dataframe.loc[
            long_signal,
            ["enter_long", "enter_tag"],
        ] = (1, "breakout_retest_long")
        dataframe.loc[
            short_signal,
            ["enter_short", "enter_tag"],
        ] = (1, "breakout_retest_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return 1.0
