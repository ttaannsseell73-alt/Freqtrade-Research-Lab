from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy


class LiquiditySweepReclaimScalp(IStrategy):
    """Pre-registered 1m/5m liquidity-sweep/reclaim scalping benchmark."""

    INTERFACE_VERSION = 3
    timeframe = "1m"
    can_short = True
    startup_candle_count = 21
    process_only_new_candles = True

    # Fixed v1 execution profile. These values are intentionally not optimized
    # against the research sample.
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
    min_sweep_bps = 5.0
    rejection_close_fraction = 0.60

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["prior_liquidity_low"] = (
            dataframe["low"]
            .rolling(self.lookback, min_periods=self.lookback)
            .min()
            .shift(1)
        )
        dataframe["prior_liquidity_high"] = (
            dataframe["high"]
            .rolling(self.lookback, min_periods=self.lookback)
            .max()
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
        sweep_fraction = self.min_sweep_bps / 10_000.0
        liquid = dataframe["volume"] > 0

        long_signal = (
            dataframe["prior_liquidity_low"].notna()
            & (
                dataframe["low"]
                <= dataframe["prior_liquidity_low"] * (1.0 - sweep_fraction)
            )
            & (dataframe["close"] > dataframe["prior_liquidity_low"])
            & (dataframe["close_location"] >= self.rejection_close_fraction)
            & liquid
        )
        short_signal = (
            dataframe["prior_liquidity_high"].notna()
            & (
                dataframe["high"]
                >= dataframe["prior_liquidity_high"] * (1.0 + sweep_fraction)
            )
            & (dataframe["close"] < dataframe["prior_liquidity_high"])
            & (
                dataframe["close_location"]
                <= 1.0 - self.rejection_close_fraction
            )
            & liquid
        )

        dataframe.loc[
            long_signal,
            ["enter_long", "enter_tag"],
        ] = (1, "liquidity_sweep_reclaim_long")
        dataframe.loc[
            short_signal,
            ["enter_short", "enter_tag"],
        ] = (1, "liquidity_sweep_reclaim_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # v1 exits are deliberately fixed: +0.8% gross target, -0.5% stop,
        # otherwise time exit after 30 minutes via minimal_roi.
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
