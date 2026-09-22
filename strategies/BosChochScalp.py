from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class BosChochScalp(IStrategy):
    """Pre-registered BOS/CHOCH market-structure scalping benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 45
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

    lookback = 20
    break_buffer_bps = 5.0
    displacement_ratio = 1.20
    close_strength_fraction = 0.65

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["prior_structure_high"] = (
            dataframe["high"]
            .rolling(self.lookback, min_periods=self.lookback)
            .max()
            .shift(1)
        )
        dataframe["prior_structure_low"] = (
            dataframe["low"]
            .rolling(self.lookback, min_periods=self.lookback)
            .min()
            .shift(1)
        )

        true_range = (
            (dataframe["high"] - dataframe["low"])
            / dataframe["close"].shift(1)
        )
        dataframe["normalized_range"] = true_range
        dataframe["prior_range_median"] = (
            true_range
            .rolling(self.lookback, min_periods=self.lookback)
            .median()
            .shift(1)
        )

        candle_range = (dataframe["high"] - dataframe["low"]).where(
            dataframe["high"] > dataframe["low"]
        )
        dataframe["close_location"] = (
            (dataframe["close"] - dataframe["low"]) / candle_range
        )

        buffer_fraction = self.break_buffer_bps / 10_000.0
        displaced = (
            dataframe["prior_range_median"].notna()
            & (
                dataframe["normalized_range"]
                >= dataframe["prior_range_median"] * self.displacement_ratio
            )
        )
        liquid = dataframe["volume"] > 0

        long_break = (
            dataframe["prior_structure_high"].notna()
            & (
                dataframe["close"]
                >= dataframe["prior_structure_high"] * (1.0 + buffer_fraction)
            )
            & displaced
            & (dataframe["close_location"] >= self.close_strength_fraction)
            & liquid
        )
        short_break = (
            dataframe["prior_structure_low"].notna()
            & (
                dataframe["close"]
                <= dataframe["prior_structure_low"] * (1.0 - buffer_fraction)
            )
            & displaced
            & (
                dataframe["close_location"]
                <= 1.0 - self.close_strength_fraction
            )
            & liquid
        )

        dataframe["structure_long_signal"] = False
        dataframe["structure_short_signal"] = False
        dataframe["structure_tag"] = ""

        state = 0
        for pos in range(len(dataframe)):
            is_long_break = bool(long_break.iloc[pos])
            is_short_break = bool(short_break.iloc[pos])

            if is_long_break and is_short_break:
                continue

            if is_long_break:
                if state != 0:
                    dataframe.iat[
                        pos,
                        dataframe.columns.get_loc("structure_long_signal"),
                    ] = True
                    dataframe.iat[
                        pos,
                        dataframe.columns.get_loc("structure_tag"),
                    ] = "bos_long" if state == 1 else "choch_long"
                state = 1

            elif is_short_break:
                if state != 0:
                    dataframe.iat[
                        pos,
                        dataframe.columns.get_loc("structure_short_signal"),
                    ] = True
                    dataframe.iat[
                        pos,
                        dataframe.columns.get_loc("structure_tag"),
                    ] = "bos_short" if state == -1 else "choch_short"
                state = -1

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        long_signal = dataframe["structure_long_signal"].fillna(False).astype(bool)
        short_signal = dataframe["structure_short_signal"].fillna(False).astype(bool)

        dataframe.loc[long_signal, "enter_long"] = 1
        dataframe.loc[long_signal, "enter_tag"] = dataframe.loc[
            long_signal,
            "structure_tag",
        ]
        dataframe.loc[short_signal, "enter_short"] = 1
        dataframe.loc[short_signal, "enter_tag"] = dataframe.loc[
            short_signal,
            "structure_tag",
        ]
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
