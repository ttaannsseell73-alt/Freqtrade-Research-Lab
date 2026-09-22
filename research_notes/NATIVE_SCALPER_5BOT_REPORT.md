# Native Scalper 5-Bot Benchmark — 2026-09-22

## Scope

Custom signal development is frozen for this phase. This benchmark evaluates five
complete/open-source scalping projects as external systems rather than rewriting
their signal logic.

Projects were pinned to immutable commits and exercised in isolated GitHub Actions
jobs. Where direct exchange REST endpoints were unavailable from the runner, market
data was supplied from immutable Binance Vision USD-M archives without changing the
strategy rules. The common deep window is June-August 2026 where the project could
accept external OHLCV. Passivbot is intentionally excluded because it is being
validated separately.

## Candidates

| Project | Pinned commit | Native focus | Benchmark outcome |
|---|---|---|---|
| ElvisTV/backtestingbots_s1 | 043c8c499b | Binance USD-M BTC scalper, 1m/5m | Reject |
| libaceta/tradingbot scalping_research | 10bdfd01fd | 10 scalping strategies, BTC/ETH, 5m/15m | Reject for 5m; one marginal 15m result only |
| DoganAliSAN/scalping | d731a98907 | 5m first-4h-range scalper, long/short | Reject |
| dniskav/scalper-bot | c6f2455f5b | 5m RSI/ADX/EMA scalper | No-trade / insufficient |
| 360-Crypto-Eye-Scalping | 46d1f15407 | 5m multi-timeframe confluence scalper | Insufficient evidence; retain only for deeper research |

## Results

### 1. ElvisTV/backtestingbots_s1

The project has a dedicated Binance USD-M backtester and models trading costs
internally. Funding was neutralized because the GitHub runner cannot query the
restricted funding endpoint.

**BTCUSDT, 2026-06-01 through 2026-08-31**

| TF | Trades | Win rate | PF | Return | Max DD |
|---|---:|---:|---:|---:|---:|
| 1m | 1,041 | 8.84% | 0.068 | -83.52% | 83.52% |
| 5m | 236 | 30.08% | 0.285 | -30.39% | 30.58% |

The sample is large enough to reject this frozen implementation for the tested
window. Both native timeframes are decisively negative.

### 2. libaceta/tradingbot — scalping_research

The native research harness tests ten strategies on BTC/ETH with next-bar entries,
high/low stop/target resolution, one position at a time, and 0.055% taker fee per
side. The original Bybit download path was unavailable from the runner, so only the
data source was substituted with Binance Vision. Strategy and backtest logic were
left unchanged.

Every 5m strategy was negative on both BTC and ETH. Representative 1% risk results:

| Strategy | BTC 5m PF | BTC return | ETH 5m PF | ETH return |
|---|---:|---:|---:|---:|
| EMA Cross | 0.397 | -97.4% | 0.469 | -92.0% |
| RSI2 Mean Reversion | 0.438 | -100.0% | 0.454 | -100.0% |
| Supertrend | 0.405 | -99.9% | 0.427 | -97.6% |
| BB + RSI | 0.456 | -99.9% | 0.506 | -99.0% |
| MACD + EMA | 0.527 | -99.4% | 0.579 | -96.7% |
| StochRSI + EMA | 0.449 | -99.3% | 0.555 | -95.1% |
| Keltner | 0.476 | -100.0% | 0.596 | -96.2% |
| Heikin Ashi | 0.796 | -100.0% | 0.889 | -99.8% |

One result outside the locked 1m/5m target was positive: ETHUSDT 15m Heikin Ashi at
1% risk produced 772 trades, +27.9% return, 29.0% drawdown, Sharpe 0.64, and PF
1.047. This is marginal, outside the target timeframe, and the native model charges
11 bps round-trip without our additional slippage stress. It is not promoted.

### 3. DoganAliSAN/scalping

The native import path creates a Binance client immediately and cannot run from the
GitHub runner. The benchmark therefore injected a non-live client stub and fed the
unchanged strategy immutable Binance Vision 5m candles.

The project does not natively debit fees/slippage in its monthly backtester.
Results were therefore post-stressed by 14 bps notional round-trip. Because the
strategy is evaluated at 20x leverage, this corresponds to 2.8 percentage points of
margin return per round trip.

| Symbol | Trades | Raw expectancy | Cost-adjusted expectancy | Adjusted PF | Adjusted WR |
|---|---:|---:|---:|---:|---:|
| BTCUSDT | 179 | -1.338 pp/trade | -4.138 pp/trade | 0.292 | 22.35% |
| ETHUSDT | 161 | -0.950 pp/trade | -3.750 pp/trade | 0.516 | 32.30% |

This is decisively negative before and after the common cost stress.

### 4. dniskav/scalper-bot

The project was supplied BTCUSDT 5m candles for June-August 2026 and executed
through its native CSV backtest CLI. Its default RSI/ADX/EMA rules generated:

- Trades: **0**
- Cost-adjusted expectancy: **0**
- Profit factor: **0**

This is not a positive result. It is a no-trade outcome and provides no evidence
that the frozen defaults are useful on this sample.

### 5. 360-Crypto-Eye-Scalping

This project has the most conservative execution semantics of the five: it walks
forward through the 5m series and resolves stop-loss before take-profit if both are
touched on the same candle. BTCUSDT 5m candles for June-August 2026 were supplied,
with 4h and daily data derived from the same chronological stream.

Native result:

- Trades: **2**
- Final equity: **1009.98** from 1000
- Win rate: **50%**
- Max drawdown: **0.2%**
- Native verdict: **NOT READY** because the sample fails its own minimum-trade and
  profit-factor gates.

After a 14 bps round-trip cost stress:

- Trades: **2**
- Adjusted expectancy: **+0.0588% per trade**
- Adjusted PF: **1.488**
- Adjusted simple return sum: **+0.1176%**

This is the only project in the five-bot batch that leaves a positive
cost-adjusted trace, but two trades are nowhere near enough for statistical
selection. It is therefore retained only as the next research candidate, not
promoted.

## Decision

There is **no statistically credible winner** in this five-project batch.

Four projects either lose materially or produce no trades. The only project worth
a second validation pass is **360-Crypto-Eye-Scalping**, solely because its two
observed trades remain positive after the common 14 bps stress and its backtester
uses conservative intrabar exit priority. The sample size is far below an
acceptable decision threshold, so this is a research-selection decision only.

The next validation pass for 360 Eye must increase sample size before any tuning:
broader symbol universe and multiple non-overlapping windows, keeping the frozen
strategy rules and the same cost stress. No parameter optimization should occur
until that evidence exists.

## Reproducibility

Benchmark workflow:
`.github/workflows/deep-scalper-5bots.yml`

Completed workflow run:
`35780828338`

Benchmark branch:
`bench/native-scalper-5bots-v1`
