from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from .event_study import benjamini_hochberg


IDENTITY_COLUMNS = [
    "experiment_id",
    "system_id",
    "system_version",
    "timeframe",
]
DISCOVERY_KEY_COLUMNS = [
    *IDENTITY_COLUMNS,
    "pair",
    "direction",
    "horizon_bars",
    "holding_minutes",
]
REQUIRED_DISCOVERY_COLUMNS = [
    *DISCOVERY_KEY_COLUMNS,
    "events_train",
    "events_validation",
    "non_overlapping_events_train",
    "non_overlapping_events_validation",
    "mean_net_return_train",
    "mean_net_return_validation",
    "p_value_validation",
    "minimum_train_events",
    "minimum_validation_events",
]


def validate_discovery_frame(frame: pd.DataFrame) -> None:
    missing = [column for column in REQUIRED_DISCOVERY_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(
            "Discovery table is missing required columns: " + ", ".join(missing)
        )

    holdout_columns = [
        column for column in frame.columns if "holdout" in str(column).lower()
    ]
    if holdout_columns:
        raise ValueError(
            "Discovery comparison refuses holdout columns: "
            + ", ".join(sorted(holdout_columns))
        )

    duplicated = frame.duplicated(DISCOVERY_KEY_COLUMNS, keep=False)
    if duplicated.any():
        raise ValueError(
            "Discovery table contains duplicate experiment/pair/direction/horizon rows"
        )


def combine_discovery_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    checked: list[pd.DataFrame] = []
    for frame in frames:
        validate_discovery_frame(frame)
        checked.append(frame.copy())

    if not checked:
        return pd.DataFrame(columns=REQUIRED_DISCOVERY_COLUMNS)

    combined = pd.concat(checked, ignore_index=True, sort=False)
    duplicated = combined.duplicated(DISCOVERY_KEY_COLUMNS, keep=False)
    if duplicated.any():
        raise ValueError(
            "Combined discovery inputs contain duplicate experiment/pair/direction/horizon rows"
        )
    return combined.sort_values(
        ["system_id", "system_version", "timeframe", "pair", "direction", "horizon_bars"]
    ).reset_index(drop=True)


def apply_global_fdr(
    combined: pd.DataFrame,
    *,
    global_fdr: float = 0.10,
) -> pd.DataFrame:
    validate_discovery_frame(combined)
    if not 0 < global_fdr <= 1:
        raise ValueError("global_fdr must be in (0, 1]")

    result = combined.copy()
    result["global_validation_q_value"] = benjamini_hochberg(
        result["p_value_validation"].fillna(1.0)
    )
    result["global_fdr_family_size"] = int(len(result))
    result["global_fdr_threshold"] = float(global_fdr)
    result["global_fdr_scope"] = "all_experiments_pair_direction_horizon"
    result["global_discovery_pass"] = (
        (result["non_overlapping_events_train"] >= result["minimum_train_events"])
        & (
            result["non_overlapping_events_validation"]
            >= result["minimum_validation_events"]
        )
        & (result["mean_net_return_train"] > 0)
        & (result["mean_net_return_validation"] > 0)
        & (result["global_validation_q_value"] <= global_fdr)
    )
    return result


def build_system_coverage(combined: pd.DataFrame) -> pd.DataFrame:
    validate_discovery_frame(combined)
    if combined.empty:
        return pd.DataFrame(
            columns=[
                *IDENTITY_COLUMNS,
                "hypotheses_tested",
                "global_candidate_rows",
                "unique_candidate_pairs",
                "directions",
                "horizon_bars",
            ]
        )
    if "global_discovery_pass" not in combined.columns:
        raise ValueError("Global FDR must be applied before building system coverage")

    rows: list[dict[str, object]] = []
    for keys, group in combined.groupby(IDENTITY_COLUMNS, sort=True, observed=True):
        selected = group.loc[group["global_discovery_pass"]].copy()
        row = dict(zip(IDENTITY_COLUMNS, keys, strict=True))
        row.update(
            {
                "hypotheses_tested": int(len(group)),
                "global_candidate_rows": int(len(selected)),
                "unique_candidate_pairs": int(selected["pair"].nunique()),
                "directions": ",".join(
                    sorted(selected["direction"].astype(str).unique())
                ),
                "horizon_bars": ",".join(
                    str(value)
                    for value in sorted(selected["horizon_bars"].astype(int).unique())
                ),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
