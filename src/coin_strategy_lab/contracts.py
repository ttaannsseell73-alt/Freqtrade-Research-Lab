from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

import pandas as pd


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str
    version: str
    supported_timeframes: tuple[Timeframe, ...]
    warmup_bars: int
    supports_long: bool = True
    supports_short: bool = True
    default_parameters: Mapping[str, Any] = field(default_factory=dict)
    source_url: str | None = None


class StrategyPlugin(ABC):
    spec: StrategySpec

    @abstractmethod
    def prepare(self, candles: pd.DataFrame, parameters: Mapping[str, Any] | None = None) -> pd.DataFrame:
        ...

    @abstractmethod
    def long_entries(self, prepared: pd.DataFrame) -> pd.Series:
        ...

    @abstractmethod
    def short_entries(self, prepared: pd.DataFrame) -> pd.Series:
        ...

    def validate_input(self, candles: pd.DataFrame) -> None:
        required = {"open", "high", "low", "close", "volume"}
        missing = sorted(required - set(candles.columns))
        if missing:
            raise ValueError(f"Missing OHLCV columns: {missing}")
