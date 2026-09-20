from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping

import pandas as pd


_SYSTEM_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class ExperimentSpec:
    system_id: str
    system_version: str
    timeframe: str
    parameters: Mapping[str, object]
    cost_bps: float
    horizons_bars: tuple[int, ...]

    def __post_init__(self) -> None:
        if not _SYSTEM_ID.fullmatch(self.system_id):
            raise ValueError(
                "system_id must use lowercase letters, digits, '_' or '-'"
            )
        if not self.system_version:
            raise ValueError("system_version cannot be empty")
        if not self.timeframe:
            raise ValueError("timeframe cannot be empty")
        if self.cost_bps < 0:
            raise ValueError("cost_bps cannot be negative")
        if not self.horizons_bars or any(horizon <= 0 for horizon in self.horizons_bars):
            raise ValueError("horizons_bars must contain positive integers")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "system_id": self.system_id,
            "system_version": self.system_version,
            "timeframe": self.timeframe,
            "parameters": dict(self.parameters),
            "cost_bps": float(self.cost_bps),
            "horizons_bars": list(self.horizons_bars),
            "horizon_semantics": "bars",
        }

    def experiment_id(self) -> str:
        payload = json.dumps(
            self.canonical_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()[:16]

    def manifest(self) -> dict[str, object]:
        payload = self.canonical_payload()
        payload["experiment_id"] = self.experiment_id()
        return payload


def tag_result_frame(frame: pd.DataFrame, spec: ExperimentSpec) -> pd.DataFrame:
    result = frame.copy()
    identity = {
        "experiment_id": spec.experiment_id(),
        "system_id": spec.system_id,
        "system_version": spec.system_version,
        "timeframe": spec.timeframe,
    }
    for column, value in identity.items():
        result[column] = value

    ordered = list(identity)
    ordered.extend(column for column in result.columns if column not in identity)
    return result.loc[:, ordered]
