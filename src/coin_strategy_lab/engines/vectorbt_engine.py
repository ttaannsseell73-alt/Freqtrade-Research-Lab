from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .base import BacktestEngine, BacktestRequest, BacktestResult


class VectorBTDiscoveryEngine(BacktestEngine):
    """Fast signal-quality discovery engine.

    Strategies emit closed-candle signals. This adapter shifts them by one bar and
    executes at the next candle open. Opposite signals reverse the position.
    """

    def run(self, request: BacktestRequest) -> BacktestResult:
        try:
            import vectorbt as vbt
        except ImportError as exc:
            raise RuntimeError(
                'VectorBT research extra is not installed. Install with: pip install -e ".[research]"'
            ) from exc

        strategy = request.strategy
        strategy.validate_input(request.candles)
        prepared = strategy.prepare(request.candles, request.parameters)

        long_entries = strategy.long_entries(prepared).shift(1, fill_value=False).astype(bool)
        short_entries = strategy.short_entries(prepared).shift(1, fill_value=False).astype(bool)

        close = prepared["close"].astype(float)
        execution_price = prepared["open"].astype(float)
        fee_per_side = float(request.cost_bps_round_trip) / 2.0 / 10_000.0

        pf = vbt.Portfolio.from_signals(
            close,
            entries=long_entries,
            exits=False,
            short_entries=short_entries,
            short_exits=False,
            price=execution_price,
            size=np.inf,
            fees=fee_per_side,
            upon_opposite_entry="reverse",
            init_cash=1.0,
            freq=request.timeframe.value,
        )

        closed = pf.trades.closed
        returns = np.asarray(closed.returns.values, dtype=float)
        trades = int(returns.size)

        if trades:
            expectancy_bps = float(np.nanmean(returns) * 10_000.0)
            wins = returns[returns > 0]
            losses = returns[returns < 0]
            win_rate = float((returns > 0).mean())
            profit_factor = (
                float(wins.sum() / abs(losses.sum()))
                if losses.size and abs(losses.sum()) > 1e-15
                else (math.inf if wins.size else math.nan)
            )
        else:
            expectancy_bps = math.nan
            win_rate = math.nan
            profit_factor = math.nan

        total_return = float(pf.total_return())
        max_drawdown = float(abs(pf.max_drawdown()))

        return BacktestResult(
            symbol=request.symbol,
            timeframe=request.timeframe,
            strategy_id=strategy.spec.strategy_id,
            strategy_version=strategy.spec.version,
            trades=trades,
            net_return=total_return,
            expectancy_bps=expectancy_bps,
            win_rate=win_rate,
            profit_factor=profit_factor,
            max_drawdown=max_drawdown,
            metadata={
                "engine": "vectorbt",
                "cost_bps_round_trip": request.cost_bps_round_trip,
                "execution": "closed-signal_next-open",
            },
        )
