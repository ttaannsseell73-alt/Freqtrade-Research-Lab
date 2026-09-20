import pandas as pd
import pytest

from freqtrade_research_lab.signals import (
    SignalSet,
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
