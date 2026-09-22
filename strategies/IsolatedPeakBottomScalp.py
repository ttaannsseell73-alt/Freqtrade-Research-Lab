from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class IsolatedPeakBottomScalp(IStrategy):
    """Tuncer Sengoz / KivancOzbilgic isolated peak-bottom benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 8
    process_only_new_candles = True

    minimal_roi = {"0": 0.008, "30": -1.0}
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        high = dataframe["high"]
        low = dataframe["low"]

        dataframe["iso_peak_2"] = (
            (high.shift(2) > high)
            & (high.shift(2) >= high.shift(1))
            & (high.shift(2) > high.shift(3))
            & (high.shift(2) > high.shift(4))
            & (low.shift(1) > low.shift(3).combine(low.shift(2), min))
            & (low < low.shift(3).combine(low.shift(2), min))
        )
        dataframe["iso_peak_1"] = (
            (high.shift(1) > high)
            & (high.shift(1) > high.shift(2))
            & (high.shift(1) > high.shift(3))
            & (low < low.shift(2).combine(low.shift(1), min))
        )
        dataframe["iso_bottom_2"] = (
            (low.shift(2) < low)
            & (low.shift(2) < low.shift(1))
            & (low.shift(2) < low.shift(3))
            & (low.shift(2) < low.shift(4))
            & (high.shift(1) < high.shift(3).combine(high.shift(2), max))
            & (high > high.shift(3).combine(high.shift(2), max))
        )
        dataframe["iso_bottom_1"] = (
            (low.shift(1) < low)
            & (low.shift(1) < low.shift(2))
            & (low.shift(1) < low.shift(3))
            & (high > high.shift(2).combine(high.shift(1), max))
        )
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        liquid = dataframe["volume"] > 0
        long_signal = (
            dataframe["iso_bottom_1"].fillna(False)
            | dataframe["iso_bottom_2"].fillna(False)
        ) & liquid
        short_signal = (
            dataframe["iso_peak_1"].fillna(False)
            | dataframe["iso_peak_2"].fillna(False)
        ) & liquid
        both = long_signal & short_signal
        long_signal &= ~both
        short_signal &= ~both

        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "isolated_bottom",
        )
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (
            1,
            "isolated_peak",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0
