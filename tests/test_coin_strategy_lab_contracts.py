import pandas as pd
import pytest

from coin_strategy_lab import StrategyRegistry, Timeframe
from coin_strategy_lab.strategies import discover_builtin_strategies
from coin_strategy_lab.strategies.mavilimw import MavilimW
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


def test_strategy_plugins_are_auto_discovered():
    plugins = discover_builtin_strategies()
    ids = tuple(p.spec.strategy_id for p in plugins)
    assert "mavilimw" in ids
    assert StrategyRegistry.discover_builtins().ids() == ids


def test_all_v1_strategy_plugins_auto_discovered_and_signal():
    import numpy as np
    from coin_strategy_lab import StrategyRegistry

    n = 500
    t = np.arange(n, dtype=float)
    base = 100.0 + 0.03 * t + 4.0 * np.sin(t / 11.0)
    candles = pd.DataFrame(
        {
            "open": base + 0.1 * np.sin(t / 3.0),
            "high": base + 1.0,
            "low": base - 1.0,
            "close": base + 0.2 * np.cos(t / 5.0),
            "volume": 1000.0 + 100.0 * np.sin(t / 7.0),
        }
    )

    registry = StrategyRegistry.discover_builtins()
    expected = {"mavilimw", "alphatrend", "pmax", "utbot", "squeeze_momentum", "qqe_ssl_wae"}
    assert expected.issubset(set(registry.ids()))

    for strategy_id in expected:
        strategy = registry.get(strategy_id)
        prepared = strategy.prepare(candles)
        long_sig = strategy.long_entries(prepared)
        short_sig = strategy.short_entries(prepared)
        assert len(long_sig) == n
        assert len(short_sig) == n
        assert long_sig.dtype == bool
        assert short_sig.dtype == bool
