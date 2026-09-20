import pandas as pd
import pytest

from freqtrade_research_lab.comparison import (
    build_system_coverage,
    combine_discovery_frames,
    validate_discovery_frame,
)


def candidate(
    *,
    experiment_id: str,
    system_id: str,
    timeframe: str,
    pair: str,
    direction: str = "long",
    horizon: int = 5,
) -> dict[str, object]:
    return {
        "experiment_id": experiment_id,
        "system_id": system_id,
        "system_version": "1",
        "timeframe": timeframe,
        "pair": pair,
        "direction": direction,
        "horizon_minutes": horizon,
        "discovery_score": 0.001,
        "validation_q_value": 0.05,
    }


def test_discovery_comparison_rejects_holdout_leakage() -> None:
    frame = pd.DataFrame(
        [
            {
                **candidate(
                    experiment_id="abc",
                    system_id="macd_crossover",
                    timeframe="15m",
                    pair="BTC/USDT:USDT",
                ),
                "mean_net_return_holdout": 0.01,
            }
        ]
    )

    with pytest.raises(ValueError, match="holdout"):
        validate_discovery_frame(frame)


def test_combine_discovery_frames_preserves_multiple_systems() -> None:
    first = pd.DataFrame(
        [
            candidate(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="BTC/USDT:USDT",
            )
        ]
    )
    second = pd.DataFrame(
        [
            candidate(
                experiment_id="exp2",
                system_id="liquidity_sweep",
                timeframe="5m",
                pair="ETH/USDT:USDT",
                direction="short",
            )
        ]
    )

    combined = combine_discovery_frames([first, second])

    assert len(combined) == 2
    assert set(combined["system_id"]) == {"macd_crossover", "liquidity_sweep"}


def test_combined_discovery_rejects_duplicate_inputs() -> None:
    frame = pd.DataFrame(
        [
            candidate(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="BTC/USDT:USDT",
            )
        ]
    )

    with pytest.raises(ValueError, match="duplicate"):
        combine_discovery_frames([frame, frame])


def test_system_coverage_counts_pairs_without_ranking_holdout() -> None:
    frame = pd.DataFrame(
        [
            candidate(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="BTC/USDT:USDT",
            ),
            candidate(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="ETH/USDT:USDT",
                direction="short",
                horizon=10,
            ),
        ]
    )

    coverage = build_system_coverage(frame)

    assert len(coverage) == 1
    assert coverage["candidate_rows"].iloc[0] == 2
    assert coverage["unique_pairs"].iloc[0] == 2
    assert coverage["directions"].iloc[0] == "long,short"
    assert coverage["horizons"].iloc[0] == "5,10"
