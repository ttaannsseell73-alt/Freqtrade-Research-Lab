from __future__ import annotations

from pandas import DataFrame
from freqtrade.strategy import IStrategy

from coin_strategy_lab import StrategyRegistry


class CoinStrategyLabExecutionBase(IStrategy):
    """Freqtrade execution adapter for CoinStrategyLab signal plugins.

    The research plugin is the single source of signal logic. Freqtrade supplies
    execution semantics, fees, trade accounting and exported trades.
    """

    INTERFACE_VERSION = 3
    can_short = True
    process_only_new_candles = True
    minimal_roi = {"0": 100.0}
    stoploss = -0.99
    trailing_stop = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    plugin_id = ""
    timeframe = "1h"
    startup_candle_count = 180
    direction_mode = "BOTH"

    @classmethod
    def _plugin(cls):
        return StrategyRegistry.discover_builtins().get(cls.plugin_id)

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        plugin = self._plugin()
        prepared = plugin.prepare(dataframe)
        prepared["_csl_long"] = plugin.long_entries(prepared).fillna(False).astype(bool)
        prepared["_csl_short"] = plugin.short_entries(prepared).fillna(False).astype(bool)
        return prepared

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        liquid = dataframe["volume"] > 0
        if self.direction_mode != "SHORT_ONLY":
            dataframe.loc[
                liquid & dataframe["_csl_long"],
                ["enter_long", "enter_tag"],
            ] = (1, f"{self.plugin_id}_long")
        if self.direction_mode != "LONG_ONLY":
            dataframe.loc[
                liquid & dataframe["_csl_short"],
                ["enter_short", "enter_tag"],
            ] = (1, f"{self.plugin_id}_short")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            dataframe["_csl_short"],
            ["exit_long", "exit_tag"],
        ] = (1, "opposite_signal")
        dataframe.loc[
            dataframe["_csl_long"],
            ["exit_short", "exit_tag"],
        ] = (1, "opposite_signal")
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


class CSL5mPMax(CoinStrategyLabExecutionBase):
    plugin_id = "pmax"
    timeframe = "5m"
    startup_candle_count = 80


class CSL5mQQESSLWAE(CoinStrategyLabExecutionBase):
    plugin_id = "qqe_ssl_wae"
    timeframe = "5m"
    startup_candle_count = 180


class CSL15mAlphaTrend(CoinStrategyLabExecutionBase):
    plugin_id = "alphatrend"
    timeframe = "15m"
    startup_candle_count = 80


class CSL15mPMax(CoinStrategyLabExecutionBase):
    plugin_id = "pmax"
    timeframe = "15m"
    startup_candle_count = 80


class CSL15mSqueezeMomentum(CoinStrategyLabExecutionBase):
    plugin_id = "squeeze_momentum"
    timeframe = "15m"
    startup_candle_count = 80


class CSL1hAlphaTrend(CoinStrategyLabExecutionBase):
    plugin_id = "alphatrend"
    timeframe = "1h"
    startup_candle_count = 80


class CSL1hMavilimW(CoinStrategyLabExecutionBase):
    plugin_id = "mavilimw"
    timeframe = "1h"
    startup_candle_count = 90


class CSL1hPMax(CoinStrategyLabExecutionBase):
    plugin_id = "pmax"
    timeframe = "1h"
    startup_candle_count = 80


class CSL1hQQESSLWAE(CoinStrategyLabExecutionBase):
    plugin_id = "qqe_ssl_wae"
    timeframe = "1h"
    startup_candle_count = 180


class CSL1hSqueezeMomentum(CoinStrategyLabExecutionBase):
    plugin_id = "squeeze_momentum"
    timeframe = "1h"
    startup_candle_count = 80


class CSL1hUTBot(CoinStrategyLabExecutionBase):
    plugin_id = "utbot"
    timeframe = "1h"
    startup_candle_count = 60


class CSL4hAlphaTrend(CoinStrategyLabExecutionBase):
    plugin_id = "alphatrend"
    timeframe = "4h"
    startup_candle_count = 80


class CSL4hMavilimW(CoinStrategyLabExecutionBase):
    plugin_id = "mavilimw"
    timeframe = "4h"
    startup_candle_count = 90


class CSL4hQQESSLWAE(CoinStrategyLabExecutionBase):
    plugin_id = "qqe_ssl_wae"
    timeframe = "4h"
    startup_candle_count = 180


class CSL4hSqueezeMomentum(CoinStrategyLabExecutionBase):
    plugin_id = "squeeze_momentum"
    timeframe = "4h"
    startup_candle_count = 80


class CSL4hUTBot(CoinStrategyLabExecutionBase):
    plugin_id = "utbot"
    timeframe = "4h"
    startup_candle_count = 60


class CSL1dQQESSLWAE(CoinStrategyLabExecutionBase):
    plugin_id = "qqe_ssl_wae"
    timeframe = "1d"
    startup_candle_count = 180


class CSL1dUTBot(CoinStrategyLabExecutionBase):
    plugin_id = "utbot"
    timeframe = "1d"
    startup_candle_count = 60


class CSL1hMavilimWShort(CSL1hMavilimW):
    direction_mode = "SHORT_ONLY"


class CSL4hMavilimWShort(CSL4hMavilimW):
    direction_mode = "SHORT_ONLY"


class CSL4hQQESSLWAEShort(CSL4hQQESSLWAE):
    direction_mode = "SHORT_ONLY"


class CSL1hQQESSLWAELong(CSL1hQQESSLWAE):
    direction_mode = "LONG_ONLY"
