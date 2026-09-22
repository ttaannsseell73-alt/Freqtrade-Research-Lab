from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class TurtleTradeChannelsScalp(IStrategy):
    """KivancOzbilgic TuTCI entry-channel benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 25
    process_only_new_candles = True

    minimal_roi = {"0": 0.008, "30": -1.0}
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    entry_length = 20

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["turtle_upper_prior"] = (
            dataframe["high"]
            .rolling(self.entry_length, min_periods=self.entry_length)
            .max()
            .shift(1)
        )
        dataframe["turtle_lower_prior"] = (
            dataframe["low"]
            .rolling(self.entry_length, min_periods=self.entry_length)
            .min()
            .shift(1)
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        liquid = dataframe["volume"] > 0
        long_signal = (
            dataframe["turtle_upper_prior"].notna()
            & (dataframe["high"] >= dataframe["turtle_upper_prior"])
            & liquid
        )
        short_signal = (
            dataframe["turtle_lower_prior"].notna()
            & (dataframe["low"] <= dataframe["turtle_lower_prior"])
            & liquid
        )
        both = long_signal & short_signal
        long_signal &= ~both
        short_signal &= ~both

        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "tutci_breakout_long",
        )
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (
            1,
            "tutci_breakout_short",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0
