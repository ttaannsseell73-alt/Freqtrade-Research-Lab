import pandas as pd
import pytest

from freqtrade_research_lab.comparison import (
    apply_global_fdr,
    build_system_coverage,
    combine_discovery_frames,
    validate_discovery_frame,
)


def hypothesis(
    *,
    experiment_id: str,
    system_id: str,
    timeframe: str,
    pair: str,
    validation_p: float,
    direction: str = "long",
    horizon: int = 5,
) -> dict[str, object]:
    bar_minutes = 15 if timeframe == "15m" else 5
    return {
        "experiment_id": experiment_id,
        "system_id": system_id,
        "system_version": "1",
        "timeframe": timeframe,
        "pair": pair,
        "direction": direction,
        "horizon_bars": horizon,
        "holding_minutes": horizon * bar_minutes,
        "events_train": 200,
        "events_validation": 50,
        "non_overlapping_events_train": 120,
        "non_overlapping_events_validation": 40,
        "mean_net_return_train": 0.01,
        "mean_net_return_validation": 0.005,
        "test_mean_net_return_train": 0.01,
        "test_mean_net_return_validation": 0.005,
        "p_value_validation": validation_p,
        "minimum_train_events": 100,
        "minimum_validation_events": 30,
        "discovery_score": 0.005,
        "validation_q_value": validation_p,
    }


def test_discovery_comparison_rejects_holdout_leakage() -> None:
    frame = pd.DataFrame(
        [
            {
                **hypothesis(
                    experiment_id="abc",
                    system_id="macd_crossover",
                    timeframe="15m",
                    pair="BTC/USDT:USDT",
                    validation_p=0.01,
                ),
                "mean_net_return_holdout": 0.01,
            }
        ]
    )

    with pytest.raises(ValueError, match="holdout"):
        validate_discovery_frame(frame)


def test_combine_discovery_frames_preserves_full_hypothesis_family() -> None:
    first = pd.DataFrame(
        [
            hypothesis(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="BTC/USDT:USDT",
                validation_p=0.04,
            )
        ]
    )
    second = pd.DataFrame(
        [
            hypothesis(
                experiment_id="exp2",
                system_id="liquidity_sweep",
                timeframe="5m",
                pair="ETH/USDT:USDT",
                validation_p=0.20,
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
            hypothesis(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="BTC/USDT:USDT",
                validation_p=0.01,
            )
        ]
    )

    with pytest.raises(ValueError, match="duplicate"):
        combine_discovery_frames([frame, frame])


def test_global_fdr_can_reject_candidate_that_passed_inside_one_experiment() -> None:
    first = pd.DataFrame(
        [
            hypothesis(
                experiment_id="exp1",
                system_id="system_a",
                timeframe="5m",
                pair="A/USDT:USDT",
                validation_p=0.04,
            )
        ]
    )
    second = pd.DataFrame(
        [
            hypothesis(
                experiment_id="exp2",
                system_id="system_b",
                timeframe="15m",
                pair="B/USDT:USDT",
                validation_p=0.20,
            )
        ]
    )

    combined = combine_discovery_frames([first, second])
    corrected = apply_global_fdr(combined, global_fdr=0.05)

    first_row = corrected.loc[corrected["experiment_id"] == "exp1"].iloc[0]
    assert abs(first_row["global_validation_q_value"] - 0.08) < 1e-12
    assert first_row["global_fdr_family_size"] == 2
    assert not bool(first_row["global_discovery_pass"])


def test_global_discovery_uses_each_experiments_event_thresholds() -> None:
    row = hypothesis(
        experiment_id="exp1",
        system_id="system_a",
        timeframe="5m",
        pair="A/USDT:USDT",
        validation_p=0.001,
    )
    row["minimum_validation_events"] = 45
    corrected = apply_global_fdr(pd.DataFrame([row]), global_fdr=0.10)
    assert not bool(corrected["global_discovery_pass"].iloc[0])


def test_system_coverage_counts_only_global_candidates() -> None:
    rows = pd.DataFrame(
        [
            hypothesis(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="BTC/USDT:USDT",
                validation_p=0.001,
            ),
            hypothesis(
                experiment_id="exp1",
                system_id="macd_crossover",
                timeframe="15m",
                pair="ETH/USDT:USDT",
                validation_p=0.9,
                direction="short",
                horizon=10,
            ),
        ]
    )
    corrected = apply_global_fdr(rows, global_fdr=0.10)
    coverage = build_system_coverage(corrected)

    assert len(coverage) == 1
    assert coverage["hypotheses_tested"].iloc[0] == 2
    assert coverage["global_candidate_rows"].iloc[0] == 1
    assert coverage["unique_candidate_pairs"].iloc[0] == 1
    assert coverage["directions"].iloc[0] == "long"
    assert coverage["horizon_bars"].iloc[0] == "5"


def test_global_fdr_excludes_train_rejected_hypotheses_from_family() -> None:
    eligible = hypothesis(
        experiment_id="exp1",
        system_id="system_a",
        timeframe="15m",
        pair="A/USDT:USDT",
        validation_p=0.04,
    )
    rejected = hypothesis(
        experiment_id="exp1",
        system_id="system_a",
        timeframe="15m",
        pair="B/USDT:USDT",
        validation_p=0.20,
        direction="short",
    )
    rejected["test_mean_net_return_train"] = -0.01

    corrected = apply_global_fdr(pd.DataFrame([eligible, rejected]), global_fdr=0.05)

    a = corrected.loc[corrected["pair"] == "A/USDT:USDT"].iloc[0]
    b = corrected.loc[corrected["pair"] == "B/USDT:USDT"].iloc[0]
    assert abs(a["global_validation_q_value"] - 0.04) < 1e-12
    assert pd.isna(b["global_validation_q_value"])
    assert a["global_fdr_family_size"] == 1
    assert a["global_fdr_scope"] == "all_experiments_train_screened_validation_family"
