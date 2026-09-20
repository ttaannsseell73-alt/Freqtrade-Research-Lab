# Upstream Grid Benchmark V1

Purpose: evaluate ready-made grid engines without changing the canonical BinanceGridBot.

## Candidates

1. enarjord/passivbot v8.1.0
   - perpetual futures
   - shared live/backtest Rust planner
   - optimizer
   - grid/trailing/market-making behavior

2. jordantete/grid_trading_bot
   - arithmetic/geometric simple and hedged grids
   - historical backtest
   - paper/sandbox mode
   - CCXT exchange layer

3. 51bitquant/binance_grid_trader
   - Binance spot/futures grid
   - legacy compatibility reference only

OctoBot is retained for phase 2 because its runtime/backtest stack is much heavier; it will only enter the performance comparison if the first two do not give enough coverage.

## Phase 1 gate

A candidate must:
- install on a clean runner;
- expose a working backtest or simulation command;
- pass its own deterministic tests or provide a reproducible equivalent smoke;
- require no live capital for evaluation.

## Phase 2 common benchmark

No project is called profitable from repository popularity or its own example output.

Survivors will be compared on the same Binance market windows using:
- BTCUSDT, ETHUSDT, SOLUSDT;
- chronological train / validation / holdout windows;
- fee and slippage assumptions recorded explicitly;
- net return after costs;
- max drawdown;
- profit factor;
- trade/fill count;
- exposure utilization;
- liquidation / hard-stop events;
- stability across symbols and windows.

A candidate only advances to paper/testnet if its holdout behavior remains acceptable after costs.
