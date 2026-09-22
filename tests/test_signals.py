import pandas as pd
import pytest

from freqtrade_research_lab.signals import (
    SignalSet,
    breakout_retest_signals,
    compression_expansion_signals,
    liquidity_sweep_reclaim_signals,
    macd_crossover_signals,
    validate_signal_set,
)


def frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": [100.0 + value for value in range(60)],
            "volume": [1.0] * 60,
        }
    )


def test_signal_validation_rejects_overlapping_directions() -> None:
    data = frame()
    signals = SignalSet(
        long=pd.Series([True] + [False] * 59),
        short=pd.Series([True] + [False] * 59),
    )
    with pytest.raises(ValueError, match="cannot overlap"):
        validate_signal_set(data, signals)


def test_signal_validation_rejects_misaligned_index() -> None:
    data = frame()
    signals = SignalSet(
        long=pd.Series([False] * 60, index=range(1, 61)),
        short=pd.Series([False] * 60, index=range(1, 61)),
    )
    with pytest.raises(ValueError, match="indexes"):
        validate_signal_set(data, signals)


def test_macd_signal_set_matches_frame_and_directions_do_not_overlap() -> None:
    data = frame()
    signals = validate_signal_set(data, macd_crossover_signals(data))
    assert len(signals.long) == len(data)
    assert len(signals.short) == len(data)
    assert not (signals.long & signals.short).any()



def test_liquidity_sweep_reclaim_detects_closed_candle_long_and_short() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.0, 104.0, 105.0, 103.0, 106.2],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0, 98.0, 101.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 101.5, 103.0],
            "volume": [1.0] * 7,
        }
    )
    signals = validate_signal_set(
        data,
        liquidity_sweep_reclaim_signals(
            data,
            lookback=5,
            min_sweep_bps=5.0,
            rejection_close_fraction=0.60,
        ),
    )

    assert bool(signals.long.iloc[5])
    assert bool(signals.short.iloc[6])
    assert not (signals.long & signals.short).any()


def test_liquidity_sweep_reclaim_uses_only_prior_window() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.0, 104.0, 105.0, 104.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0, 98.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 102.0],
            "volume": [1.0] * 6,
        }
    )
    base = liquidity_sweep_reclaim_signals(data, lookback=5)

    mutated = data.copy()
    mutated.loc[5, "high"] = 500.0
    mutated.loc[5, "close"] = 450.0
    changed = liquidity_sweep_reclaim_signals(mutated, lookback=5)

    # Future/current extremes never alter the prior rolling liquidity level.
    assert bool(base.long.iloc[5])
    assert bool(changed.long.iloc[5])


def test_liquidity_sweep_reclaim_rejects_touch_without_reclaim() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.0, 104.0, 105.0, 100.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0, 98.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 98.5],
            "volume": [1.0] * 6,
        }
    )
    signals = liquidity_sweep_reclaim_signals(data, lookback=5)
    assert not bool(signals.long.iloc[5])


def test_liquidity_sweep_reclaim_validates_parameters() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0] * 6,
            "low": [99.0] * 6,
            "close": [100.0] * 6,
            "volume": [1.0] * 6,
        }
    )
    with pytest.raises(ValueError, match="lookback"):
        liquidity_sweep_reclaim_signals(data, lookback=1)
    with pytest.raises(ValueError, match="min_sweep_bps"):
        liquidity_sweep_reclaim_signals(data, min_sweep_bps=-1)
    with pytest.raises(ValueError, match="rejection_close_fraction"):
        liquidity_sweep_reclaim_signals(data, rejection_close_fraction=0.5)



def test_breakout_retest_detects_long() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 106.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0, 104.8, 105.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.2, 105.8],
            "volume": [1.0] * 7,
        }
    )
    signals = validate_signal_set(
        data,
        breakout_retest_signals(
            data,
            lookback=5,
            breakout_buffer_bps=5.0,
            retest_tolerance_bps=10.0,
            close_strength_fraction=0.55,
        ),
    )
    assert bool(signals.long.iloc[6])
    assert not bool(signals.short.iloc[6])


def test_breakout_retest_detects_short() -> None:
    data = pd.DataFrame(
        {
            "high": [111.0, 110.0, 109.0, 108.0, 107.0, 105.2, 105.0],
            "low": [109.0, 108.0, 107.0, 106.0, 105.0, 104.0, 104.0],
            "close": [110.0, 109.0, 108.0, 107.0, 106.0, 104.8, 104.2],
            "volume": [1.0] * 7,
        }
    )
    signals = validate_signal_set(
        data,
        breakout_retest_signals(
            data,
            lookback=5,
            breakout_buffer_bps=5.0,
            retest_tolerance_bps=10.0,
            close_strength_fraction=0.55,
        ),
    )
    assert bool(signals.short.iloc[6])
    assert not bool(signals.long.iloc[6])


def test_breakout_retest_rejects_breakout_without_retest() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0, 104.8, 106.0],
            "close": [100.0, 101.0, 102.0, 103.0, 104.0, 105.2, 106.8],
            "volume": [1.0] * 7,
        }
    )
    signals = breakout_retest_signals(data, lookback=5)
    assert not bool(signals.long.iloc[6])


def test_breakout_retest_validates_parameters() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0] * 7,
            "low": [99.0] * 7,
            "close": [100.0] * 7,
            "volume": [1.0] * 7,
        }
    )
    with pytest.raises(ValueError, match="lookback"):
        breakout_retest_signals(data, lookback=1)
    with pytest.raises(ValueError, match="breakout_buffer_bps"):
        breakout_retest_signals(data, breakout_buffer_bps=-1)
    with pytest.raises(ValueError, match="retest_tolerance_bps"):
        breakout_retest_signals(data, retest_tolerance_bps=-1)
    with pytest.raises(ValueError, match="close_strength_fraction"):
        breakout_retest_signals(data, close_strength_fraction=1.0)



def test_compression_expansion_detects_long_breakout() -> None:
    data = pd.DataFrame(
        {
            "high": [102.0, 102.0, 102.0, 102.0, 102.0, 102.0, 100.2, 100.2, 100.2, 102.0],
            "low": [98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 99.8, 99.8, 99.8, 99.9],
            "close": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.1, 100.1, 101.8],
            "volume": [1.0] * 10,
        }
    )
    signals = validate_signal_set(
        data,
        compression_expansion_signals(
            data,
            compression_window=3,
            baseline_window=3,
            compression_ratio=0.60,
            expansion_ratio=1.50,
            breakout_buffer_bps=5.0,
            close_strength_fraction=0.65,
        ),
    )
    assert bool(signals.long.iloc[9])
    assert not bool(signals.short.iloc[9])


def test_compression_expansion_detects_short_breakout() -> None:
    data = pd.DataFrame(
        {
            "high": [102.0, 102.0, 102.0, 102.0, 102.0, 102.0, 100.2, 100.2, 100.2, 100.1],
            "low": [98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 99.8, 99.8, 99.8, 98.0],
            "close": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 99.9, 99.9, 98.2],
            "volume": [1.0] * 10,
        }
    )
    signals = validate_signal_set(
        data,
        compression_expansion_signals(
            data,
            compression_window=3,
            baseline_window=3,
            compression_ratio=0.60,
            expansion_ratio=1.50,
            breakout_buffer_bps=5.0,
            close_strength_fraction=0.65,
        ),
    )
    assert bool(signals.short.iloc[9])
    assert not bool(signals.long.iloc[9])


def test_compression_expansion_requires_expansion() -> None:
    data = pd.DataFrame(
        {
            "high": [102.0, 102.0, 102.0, 102.0, 102.0, 102.0, 100.2, 100.2, 100.2, 100.35],
            "low": [98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 99.8, 99.8, 99.8, 100.0],
            "close": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.1, 100.1, 100.3],
            "volume": [1.0] * 10,
        }
    )
    signals = compression_expansion_signals(
        data,
        compression_window=3,
        baseline_window=3,
        expansion_ratio=2.0,
    )
    assert not bool(signals.long.iloc[9])


def test_compression_expansion_validates_parameters() -> None:
    data = pd.DataFrame(
        {
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.0] * 10,
            "volume": [1.0] * 10,
        }
    )
    with pytest.raises(ValueError, match="compression_window"):
        compression_expansion_signals(data, compression_window=2)
    with pytest.raises(ValueError, match="baseline_window"):
        compression_expansion_signals(data, compression_window=5, baseline_window=4)
    with pytest.raises(ValueError, match="compression_ratio"):
        compression_expansion_signals(data, compression_ratio=1.0)
    with pytest.raises(ValueError, match="expansion_ratio"):
        compression_expansion_signals(data, expansion_ratio=1.0)
    with pytest.raises(ValueError, match="breakout_buffer_bps"):
        compression_expansion_signals(data, breakout_buffer_bps=-1)
    with pytest.raises(ValueError, match="close_strength_fraction"):
        compression_expansion_signals(data, close_strength_fraction=0.5)
