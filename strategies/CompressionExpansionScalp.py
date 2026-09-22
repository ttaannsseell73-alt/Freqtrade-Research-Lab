from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class CompressionExpansionScalp(IStrategy):
    """Pre-registered price-only compression -> expansion scalping benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 70
    process_only_new_candles = True

    minimal_roi = {
        "0": 0.008,
        "30": -1.0,
    }
    stoploss = -0.005
    trailing_stop = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    compression_window = 10
    baseline_window = 50
    compression_ratio = 0.60
    expansion_ratio = 1.50
    breakout_buffer_bps = 5.0
    close_strength_fraction = 0.65

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        rolling_high = dataframe["high"].rolling(
            self.compression_window,
            min_periods=self.compression_window,
        ).max()
        rolling_low = dataframe["low"].rolling(
            self.compression_window,
            min_periods=self.compression_window,
        ).min()

        range_width = (rolling_high - rolling_low) / dataframe["close"]
        dataframe["prior_compression_width"] = range_width.shift(1)
        dataframe["historical_width_baseline"] = (
            range_width
            .shift(self.compression_window + 1)
            .rolling(
                self.baseline_window,
                min_periods=self.baseline_window,
            )
            .median()
        )

        dataframe["prior_range_high"] = rolling_high.shift(1)
        dataframe["prior_range_low"] = rolling_low.shift(1)

        previous_close = dataframe["close"].shift(1)
        dataframe["normalized_candle_range"] = (
            dataframe["high"] - dataframe["low"]
        ) / previous_close
        dataframe["prior_candle_range_baseline"] = (
            dataframe["normalized_candle_range"]
            .rolling(
                self.compression_window,
                min_periods=self.compression_window,
            )
            .median()
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
        compressed = (
            dataframe["prior_compression_width"].notna()
            & dataframe["historical_width_baseline"].notna()
            & (
                dataframe["prior_compression_width"]
                <= dataframe["historical_width_baseline"] * self.compression_ratio
            )
        )
        expanding = (
            dataframe["prior_candle_range_baseline"].notna()
            & (
                dataframe["normalized_candle_range"]
                >= dataframe["prior_candle_range_baseline"] * self.expansion_ratio
            )
        )
        breakout_buffer = self.breakout_buffer_bps / 10_000.0
        liquid = dataframe["volume"] > 0

        long_signal = (
            compressed
            & expanding
            & dataframe["prior_range_high"].notna()
            & (
                dataframe["close"]
                >= dataframe["prior_range_high"] * (1.0 + breakout_buffer)
            )
            & (dataframe["close_location"] >= self.close_strength_fraction)
            & liquid
        )
        short_signal = (
            compressed
            & expanding
            & dataframe["prior_range_low"].notna()
            & (
                dataframe["close"]
                <= dataframe["prior_range_low"] * (1.0 - breakout_buffer)
            )
            & (
                dataframe["close_location"]
                <= 1.0 - self.close_strength_fraction
            )
            & liquid
        )

        dataframe.loc[
            long_signal,
            ["enter_long", "enter_tag"],
        ] = (1, "compression_expansion_long")
        dataframe.loc[
            short_signal,
            ["enter_short", "enter_tag"],
        ] = (1, "compression_expansion_short")
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
