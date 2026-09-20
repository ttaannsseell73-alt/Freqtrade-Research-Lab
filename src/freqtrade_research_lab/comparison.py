from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


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
    "discovery_score",
    "validation_q_value",
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


def build_system_coverage(combined: pd.DataFrame) -> pd.DataFrame:
    validate_discovery_frame(combined)
    if combined.empty:
        return pd.DataFrame(
            columns=[
                *IDENTITY_COLUMNS,
                "candidate_rows",
                "unique_pairs",
                "directions",
                "horizons",
            ]
        )

    group_columns = IDENTITY_COLUMNS
    rows: list[dict[str, object]] = []
    for keys, group in combined.groupby(group_columns, sort=True, observed=True):
        row = dict(zip(group_columns, keys, strict=True))
        row.update(
            {
                "candidate_rows": int(len(group)),
                "unique_pairs": int(group["pair"].nunique()),
                "directions": ",".join(sorted(group["direction"].astype(str).unique())),
                "horizon_bars": ",".join(
                    str(value)
                    for value in sorted(group["horizon_bars"].astype(int).unique())
                ),
            }
        )
        rows.append(row)
    return pd.DataFrame(rows)
