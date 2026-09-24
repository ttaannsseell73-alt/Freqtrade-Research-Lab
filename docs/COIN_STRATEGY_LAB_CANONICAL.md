# CoinStrategyLab — Canonical V1

## Goal

Automatically discover which strategy works best for each Binance USD-M perpetual futures symbol and timeframe.

Canonical research key:

`symbol × timeframe × strategy × strategy_version × evaluation_window`

## Universe

- Binance USD-M perpetual futures
- Exchange status must be TRADING
- USDT quote
- All eligible symbols are included; no fixed top-50 cap
- Symbols without sufficient history are retained as INSUFFICIENT_HISTORY

## Canonical timeframes

- 1m
- 5m
- 15m
- 1h
- 4h
- 1d

## Evaluation windows

| Timeframe | Discovery | Robustness |
| --- | --- | --- |
| 1m | 90d | 180d |
| 5m | 90d | 180d |
| 15m | 180d | 365d |
| 1h | 90d | 180d + 365d |
| 4h | 365d | 730d |
| 1d | 730d | 1095d |

## Architecture

1. Universe Scanner
2. Market Data Store
3. Strategy Registry
4. Research Backtest Engine (VectorBT adapter first)
5. Execution Validator (Freqtrade adapter)
6. Metrics + OOS Validator
7. Coin × Timeframe × Strategy Matrix
8. Strategy Selector / Router
9. API + UI

Backtest engines are adapters. No strategy is allowed to depend directly on VectorBT or Freqtrade.

## V1 benchmark library

1. MavilimW
2. AlphaTrend
3. PMax
4. UT Bot
5. Squeeze Momentum
6. QQE MOD + SSL Hybrid + Waddah Attar Explosion

## Validation principles

- Closed candle signals only
- Next-candle execution for common signal-quality comparison
- 6 / 10 / 15 bps round-trip cost sensitivity
- Chronological train / validation / holdout
- Minimum sample requirements
- Profit factor, expectancy, drawdown
- Long/short split
- Temporal stability
- Cost robustness
- Concentration / outlier audit
- Parameter-neighbour stability
- Multiple-testing / false-discovery control

A positive backtest is not automatically a router assignment.

## Router

The router may return NO_TRADE. Strategy switching must use hysteresis and must not chase the latest winner.
