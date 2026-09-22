from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class VolumeBasedColouredBarsScalp(IStrategy):
    """Strong-volume directional bars derived from Kivanc VBCB."""

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

    volume_length = 30

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["volume_average"] = dataframe["volume"].rolling(
            self.volume_length,
            min_periods=self.volume_length,
        ).mean()
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        strong_volume = (
            dataframe["volume_average"].notna()
            & (dataframe["volume"] > dataframe["volume_average"] * 1.5)
        )
        long_signal = strong_volume & (dataframe["close"] > dataframe["open"])
        short_signal = strong_volume & (dataframe["close"] < dataframe["open"])

        dataframe.loc[long_signal, ["enter_long", "enter_tag"]] = (
            1,
            "vbcb_strong_bull",
        )
        dataframe.loc[short_signal, ["enter_short", "enter_tag"]] = (
            1,
            "vbcb_strong_bear",
        )
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def leverage(self, pair: str, current_time, current_rate: float,
                 proposed_leverage: float, max_leverage: float,
                 entry_tag: str | None, side: str, **kwargs) -> float:
        return 1.0
