from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

import pandas as pd

from ..contracts import StrategyPlugin, Timeframe


@dataclass(frozen=True)
class BacktestRequest:
    symbol: str
    timeframe: Timeframe
    strategy: StrategyPlugin
    candles: pd.DataFrame
    cost_bps_round_trip: float = 10.0
    parameters: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class BacktestResult:
    symbol: str
    timeframe: Timeframe
    strategy_id: str
    strategy_version: str
    trades: int
    net_return: float
    expectancy_bps: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    metadata: Mapping[str, Any]


class BacktestEngine(ABC):
    @abstractmethod
    def run(self, request: BacktestRequest) -> BacktestResult:
        raise NotImplementedError
