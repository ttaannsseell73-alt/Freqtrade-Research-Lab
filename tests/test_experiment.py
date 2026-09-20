import pandas as pd
import pytest

from freqtrade_research_lab.experiment import ExperimentSpec, tag_result_frame


def make_spec(**overrides) -> ExperimentSpec:
    values = {
        "system_id": "macd_crossover",
        "system_version": "1",
        "timeframe": "1m",
        "parameters": {"fast": 12, "slow": 26, "signal": 9},
        "cost_bps": 14.0,
        "horizons_bars": (1, 3, 5, 10, 20, 60),
    }
    values.update(overrides)
    return ExperimentSpec(**values)


def test_experiment_id_is_deterministic_and_parameter_sensitive() -> None:
    first = make_spec(parameters={"slow": 26, "fast": 12, "signal": 9})
    second = make_spec(parameters={"signal": 9, "fast": 12, "slow": 26})
    changed = make_spec(parameters={"fast": 10, "slow": 26, "signal": 9})

    assert first.experiment_id() == second.experiment_id()
    assert first.experiment_id() != changed.experiment_id()
    assert len(first.experiment_id()) == 16


def test_tag_result_frame_adds_canonical_identity_columns() -> None:
    frame = pd.DataFrame(
        [{"pair": "BTC/USDT:USDT", "direction": "long", "discovery_score": 0.001}]
    )
    spec = make_spec(timeframe="15m")

    tagged = tag_result_frame(frame, spec)

    assert list(tagged.columns[:4]) == [
        "experiment_id",
        "system_id",
        "system_version",
        "timeframe",
    ]
    assert tagged["experiment_id"].iloc[0] == spec.experiment_id()
    assert tagged["system_id"].iloc[0] == "macd_crossover"
    assert tagged["timeframe"].iloc[0] == "15m"


def test_invalid_system_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        make_spec(system_id="MACD Cross")



def test_experiment_manifest_declares_bar_horizon_semantics() -> None:
    manifest = make_spec(timeframe="4h").manifest()
    assert manifest["horizons_bars"] == [1, 3, 5, 10, 20, 60]
    assert manifest["horizon_semantics"] == "bars"
