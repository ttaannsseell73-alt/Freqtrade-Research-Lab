# Ready Scalping Bot Project Benchmark v1

Date: 2026-09-22

Scope: five open-source projects marketed or structured for short-horizon Binance
Futures trading. Passivbot is intentionally excluded because it is being evaluated
separately.

This benchmark does **not** accept README performance claims as evidence. Projects
were pinned to immutable commits and exercised through their native tests,
backtesters, or executable paths where available. Where a project lacked a
credible performance backtest, that limitation is treated as evidence against
promotion rather than filled with a rewritten strategy.

## Candidates

| Project | Pinned commit | Native validation | Performance evidence | Verdict |
|---|---|---|---|---|
| 360 Crypto Eye Scalping | `46d1f154077c14007c4892cb3dc86394c8fd7cb6` | 1,199 tests PASS | Discovery period positive, untouched holdout negative | REJECT |
| Ultra Scalping Bot | `b47351ff8963d8e9f13aa91be91f8cbaf28025b9` | Backtester executes; full compile fails | All four 7-day cost-inclusive balances negative, one trade each | REJECT |
| JODI96 Trader | `4384b050464741c588fdcd0579e1c0b7d43863a4` | Compile + focused smoke PASS | Native backtest is an RL training replay with balance resets, so PnL is not accepted as comparable evidence | REJECT |
| justinmarkdaniel/trading-bot | `8c81bc29ab62178407a8807811cee46bd7edad61` | 32/32 tests PASS | Public repository explicitly ships an engineering chassis with minimal example strategies rather than production strategies | REJECT |
| ryu878/binance_futures_scalp_grid_bot | `79aebef38419af73059b44b0f5241dd1a014e20d` | Install + compile PASS | No native backtest; legacy live-only BUSD grid | REJECT |

## 360 Crypto Eye: deeper validation

The project was the strongest software-quality candidate in this set, so it
received an additional frozen-parameter validation instead of being selected on
the first favorable sample.

### Discovery / diagnostic window

- Data: Binance Vision USD-M 1m candles.
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT.
- Window: 2026-06-01 through 2026-08-31.
- Native strategy logic: unchanged.
- Cost stress: 14 bps round trip, applied as 0.14 percentage points per trade.
- Aggregate trades: 25.
- Adjusted expectancy: **+0.165932% per trade**.
- Adjusted profit factor: **1.513238**.

The result was promising but too small to promote.

### Untouched holdout

Without changing parameters, the exact same strategy and cost model were run on
2026-01-01 through 2026-05-31 for the same five symbols.

| Symbol | Trades | Adjusted expectancy | Adjusted PF |
|---|---:|---:|---:|
| BTCUSDT | 19 | +0.041821% | 1.1100 |
| ETHUSDT | 25 | -0.370316% | 0.4251 |
| SOLUSDT | 17 | -0.650288% | 0.1275 |
| XRPUSDT | 26 | -0.255019% | 0.4770 |
| DOGEUSDT | 35 | -0.053003% | 0.8647 |
| **Aggregate** | **122** | **-0.229539%** | **0.5511** |

The favorable June-August result therefore did not generalize across the
untouched historical window. This candidate is rejected without parameter
retuning.

## Ultra Scalping Bot

A seven-day BTCUSDT 1m smoke used the project's native backtester with 5 bps per
side commission plus 4 bps round-trip slippage. Each bundled strategy produced
only one trade. Final balances from a 10,000 starting balance were:

- EMA/RSI/ATR: 9,997.5834
- Momentum Scalper: 9,999.6547
- Grid Scalper: 9,999.7205
- Combined: 9,998.3187

The repository also fails a full Python compile because
`core/websocket_feed.py` contains invalid trailing source text. The backtester
module itself can execute, but the repository is not accepted as a production
ready bot.

## JODI96 Trader

The project compiles and its core risk component initializes successfully.
However, its native BACKTEST mode is an RL training replay and explicitly resets
depleted balance as a training artifact. That makes headline PnL unsuitable for
a fair bot-performance comparison. A prior native replay attempt on GitHub
Actions also hit Binance's runner-location HTTP 451 restriction.

## justinmarkdaniel/trading-bot

Installation succeeds and 32/32 repository tests pass, including API, state
store, strategy, and tick-order tests. The public repository is a sound
execution/backtest chassis, but it intentionally contains only minimal example
strategies. It is therefore not a ready profitable scalping strategy candidate.

## ryu878 Binance Futures Scalp Grid Bot

The code installs and compiles, but static audit found:

- one unconditional infinite loop,
- eight bare `except` handlers,
- eleven direct live-order/cancel call sites,
- no native backtest path.

It is a legacy BUSD live script and does not satisfy the evidence standard for
promotion.

## Final decision

**No project in this five-bot batch is promoted.**

The strongest initial candidate, 360 Crypto Eye, failed the frozen untouched
holdout decisively (aggregate adjusted PF 0.5511, expectancy -0.229539%).
Selecting it from the favorable June-August sample would therefore be
sample-selection bias.

The benchmark is complete only as an elimination round. No live or paper
capital should be assigned to any of these five on the evidence collected here.
