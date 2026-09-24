import pandas as pd
import pytest

from coin_strategy_lab import StrategyRegistry, Timeframe
from coin_strategy_lab.strategies import MavilimW
from coin_strategy_lab.timeframes import CANONICAL_TIMEFRAMES, WINDOW_POLICY


def test_canonical_timeframes_locked():
    assert tuple(tf.value for tf in CANONICAL_TIMEFRAMES) == ("1m", "5m", "15m", "1h", "4h", "1d")
    assert WINDOW_POLICY[Timeframe.H1].discovery_days == 90
    assert WINDOW_POLICY[Timeframe.D1].discovery_days == 730


def test_registry_rejects_duplicate_ids():
    registry = StrategyRegistry([MavilimW()])
    with pytest.raises(ValueError):
        registry.register(MavilimW())


def test_mavilimw_produces_boolean_signals():
    n = 250
    candles = pd.DataFrame(
        {
            "open": range(1, n + 1),
            "high": range(2, n + 2),
            "low": range(0, n),
            "close": [100 + i * 0.1 for i in range(n)],
            "volume": [1000.0] * n,
        }
    )
    strategy = MavilimW()
    prepared = strategy.prepare(candles)
    assert "mavilimw" in prepared
    assert strategy.long_entries(prepared).dtype == bool
    assert strategy.short_entries(prepared).dtype == bool


def test_missing_ohlcv_is_rejected():
    strategy = MavilimW()
    with pytest.raises(ValueError):
        strategy.prepare(pd.DataFrame({"close": [1.0, 2.0]}))
