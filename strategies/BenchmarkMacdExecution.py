from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy
from freqtrade.vendor.qtpylib import indicators as qtpylib


class BenchmarkMacdExecution(IStrategy):
    """Pure MACD crossover execution control; not a production strategy."""

    INTERFACE_VERSION = 3
    timeframe = "15m"
    can_short = True
    startup_candle_count = 35
    process_only_new_candles = True

    # Disable ROI/stop exits for the control as far as practical. The benchmark
    # exits on the opposite MACD crossover so execution results measure the rule
    # itself rather than a separately tuned risk overlay.
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        fast = dataframe["close"].ewm(span=12, adjust=False, min_periods=12).mean()
        slow = dataframe["close"].ewm(span=26, adjust=False, min_periods=26).mean()
        dataframe["macd"] = fast - slow
        dataframe["macd_signal"] = dataframe["macd"].ewm(
            span=9, adjust=False, min_periods=9
        ).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        liquid = dataframe["volume"] > 0
        dataframe.loc[
            liquid & qtpylib.crossed_above(dataframe["macd"], dataframe["macd_signal"]),
            ["enter_long", "enter_tag"],
        ] = (1, "macd_cross_long")
        dataframe.loc[
            liquid & qtpylib.crossed_below(dataframe["macd"], dataframe["macd_signal"]),
            ["enter_short", "enter_tag"],
        ] = (1, "macd_cross_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            qtpylib.crossed_below(dataframe["macd"], dataframe["macd_signal"]),
            ["exit_long", "exit_tag"],
        ] = (1, "opposite_cross")
        dataframe.loc[
            qtpylib.crossed_above(dataframe["macd"], dataframe["macd_signal"]),
            ["exit_short", "exit_tag"],
        ] = (1, "opposite_cross")
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
