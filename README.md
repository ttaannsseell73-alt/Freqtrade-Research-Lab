# Freqtrade Research Lab

Reproducible research for answering one concrete question:

> Which Binance USDT-M futures contracts respond to which signal, timeframe,
> direction, and market regime after realistic costs?

This repository is a research layer beside Freqtrade. It is not a live-trading
bot and it never contains exchange credentials or downloaded market data.

## Canonical pipeline

1. Archive Binance futures data with a SHA-256 manifest.
2. Audit every signal for repainting and future-data leakage.
3. Run an event study across the whole contract universe.
4. Select candidates using train + validation only.
5. Freeze the candidate set from train + validation, then expose holdout metrics only for those candidates.
6. Run Freqtrade execution backtests on the surviving candidates.
7. Promote only stable candidates to dry-run/paper trading.

## First benchmark: 1-minute MACD crossover

The first benchmark deliberately uses a simple model. Its purpose is to prove
the research pipeline before complex price-action or TradingView models are
added.

- Signal is calculated on a closed candle.
- Entry is the next candle's open (no same-candle fill or lookahead).
- Events whose signal/entry/exit cross a train/validation/holdout boundary are dropped.
- Events whose forward window crosses a missing candle/gap are also dropped; bar horizons must be truly contiguous.
- Long and short are measured separately.
- Forward horizons: 1, 3, 5, 10, 20, and 60 **bars**. On 1m these equal minutes; on 15m/4h the output also records the true `holding_minutes`.
- Metrics include net return, win rate, profit factor, MAE, and MFE.
- Descriptive metrics use every valid event, but p-values and minimum event-count thresholds use a deterministic non-overlapping event subset for each horizon so overlapping forward windows are not treated as independent samples.
- Default round-trip cost is 14 bps: 10 bps fee + 4 bps slippage assumption.
- The 365-day range is split chronologically 60% / 20% / 20%.
- Discovery is two-stage: train screens hypotheses using independent-event count and positive independent-sample expectancy; Benjamini-Hochberg is then applied only to that pre-screened validation family.

The MACD benchmark is a control group, not a production signal. The event study measures fixed-horizon response after a signal; it is not a complete strategy P&L backtest and does not model stop/TP/trailing exits, leverage, funding, or order-book execution.

Every research run now carries a deterministic `experiment_id` built from
`system_id + system_version + timeframe + parameters + costs + horizon bars +
study date range + actual research universe fingerprint`. The same strategy on
a different date range or coin universe is therefore a different experiment.
This lets later single-system, per-coin system, and hybrid experiments coexist
without mixing incompatible results.

## Install beside Freqtrade

From the official Freqtrade repository root in PowerShell:

```powershell
git clone https://github.com/ttaannsseell73-alt/Freqtrade-Research-Lab.git .\user_data\research_lab
```

The normal Freqtrade Docker mount makes the repository available inside the
container at `/freqtrade/user_data/research_lab`.

## Run the event study

Run only after the 1-minute download is complete:

First build the canonical coverage and quality catalog:

```powershell
docker compose run --rm --entrypoint python freqtrade /freqtrade/user_data/research_lab/scripts/build_dataset_catalog.py `
  --data-dir /freqtrade/user_data/data/binance/futures `
  --output-dir /freqtrade/user_data/research/catalog_20250920_20260920 `
  --start 2025-09-20 `
  --end 2026-09-20 `
  --timeframes 1m 5m 15m 1h 4h 1d
```

The catalog marks files as `ready`, `partial_coverage`, `too_short`,
`invalid_ohlc`, or `empty`. Newly listed contracts remain visible instead of
being silently mixed with full-history contracts. Passing `--catalog` to the
event study makes the quality gate executable: only rows with
`research_ready=true` for the requested timeframe enter discovery.

Catalog writes are fail-closed: if `dataset_catalog.csv` or its summary already
exists in the target directory, the builder stops instead of overwriting it.
Use a new output directory for a different timeframe set. `--replace` exists
only for an intentional full replacement and the summary records the coverage
and minimum-candle thresholds used.

Canonical catalogs also SHA-256 hash every source futures OHLCV file. Event
studies validate that the catalog's start/end range and timeframe match the
requested run, fail if a catalog-ready pair is missing from disk, and bind the
selected universe fingerprint into `experiment_id`. Legacy catalogs without
source hashes remain readable through an explicitly labeled metadata-fingerprint
fallback so the existing 1m baseline is not destroyed.

Then run the 1-minute benchmark:

```powershell
docker compose run --rm --entrypoint python freqtrade /freqtrade/user_data/research_lab/scripts/macd_event_study.py `
  --data-dir /freqtrade/user_data/data/binance/futures `
  --catalog /freqtrade/user_data/research/catalog_20250920_20260920/dataset_catalog.csv `
  --output-dir /freqtrade/user_data/research/results/macd_1m_20250920_20260920 `
  --start 2025-09-20 `
  --end 2026-09-20 `
  --timeframe 1m `
  --horizon-bars 1 3 5 10 20 60 `
  --cost-bps 14 `
  --min-train-events 100 `
  --min-validation-events 30 `
  --min-holdout-events 30 `
  --validation-fdr 0.10
```

The event-count thresholds and FDR level are explicit CLI parameters and are
included in the experiment fingerprint/manifest. Thresholds apply to
`non_overlapping_events`, not the raw event count. This is important on slower
timeframes and long horizons: a dense cluster of overlapping signals cannot
manufacture statistical sample size.

The output directory must be new or empty. A non-empty directory causes a
fail-closed stop so an earlier research run cannot be overwritten.

Outputs:

- `dataset_coverage.csv`: candles and date coverage for every contract.
- `summary_discovery.csv`: long-form train + validation statistics only; no holdout rows.
- `discovery_tests.csv`: every train+validation hypothesis in wide form, including experiment-wide FDR q-values and pass/fail status.
- `candidates_train_validation.csv`: candidates selected without holdout data.
- `holdout_report.csv`: holdout results only for the already-frozen candidate set.
- `errors.csv`: new listings, missing data, or unreadable files.
- `result_summary.json`: compact machine-readable outcome counts and status.
- `run_manifest.json`: exact settings, split boundaries, and limitations.
- `SHA256SUMS.txt`: integrity hashes for every result file.

## Freqtrade execution benchmark

The event study is an exploratory signal-response tool only. Promotion decisions
must use Freqtrade's trade-level backtester and bias checks.

The canonical execution runner:

1. Loads only `research_ready` pairs from the dataset catalog.
2. Runs one Freqtrade futures backtest across that fixed pair universe with
   `max_open_trades=pair_count`, a large static wallet, and fixed stake sizing
   so portfolio slot competition does not suppress otherwise valid pair trades.
3. Uses `--cache none` and a fixed per-side fee.
4. Uses a smaller detail timeframe when supplied (15m research -> 1m detail;
   4h research -> 15m detail).
5. Reads the exported Freqtrade ZIP and writes per-pair trade metrics.
6. Applies the explicit extra round-trip slippage assumption to exported trade
   returns after Freqtrade's fee/funding accounting.
7. Runs Freqtrade `lookahead-analysis` unless explicitly disabled.

For the canonical 15m MACD execution control:

```powershell
docker compose run --rm --entrypoint python freqtrade /freqtrade/user_data/research_lab/scripts/run_execution_benchmark.py `
  --config /freqtrade/user_data/config.json `
  --data-dir /freqtrade/user_data/data/binance `
  --catalog /freqtrade/user_data/research/catalog_15m4h_full_20250920_20260920/dataset_catalog.csv `
  --output-dir /freqtrade/user_data/research/execution/macd_15m_v1 `
  --strategy BenchmarkMacdExecution `
  --timeframe 15m `
  --timeframe-detail 1m `
  --start 2025-09-20 `
  --end 2026-09-20 `
  --fee-per-side 0.0005 `
  --slippage-bps-round-trip 4
```

The MACD execution control enters on a 12/26/9 crossover and exits on the
opposite crossover. ROI and stop exits are effectively disabled for this control
so its trade-level result measures the crossover rule rather than a tuned risk
overlay. It uses 1x leverage.

Canonical outputs include:

- `backtest/`: Freqtrade's reproducible ZIP result bundle.
- `metrics/pair_metrics.csv`: pair-level trades, adjusted expectancy, profit
  factor, win rate, sequential pair drawdown, long/short counts, duration, and
  funding-fee totals when present.
- `metrics/overall_metrics.json`: aggregate trade statistics. A synthetic
  multi-pair drawdown is intentionally not invented; Freqtrade remains the
  portfolio-level drawdown source.
- `metrics/exit_reason_metrics.csv`: exit-reason counts and expectancy.
- `lookahead.csv`: Freqtrade lookahead-bias analysis.
- `pairs.txt`: exact fixed research universe.
- `run_manifest.json`: costs, timeframe/detail timeframe, pair fingerprint,
  source ZIP, and run settings.

The 5 bps `--fee-per-side` value is applied by Freqtrade on both entry and
exit (10 bps round trip). The additional 4 bps round-trip slippage assumption is
kept separate and visible in the research metrics instead of being hidden inside
the strategy.

## Liquidity Sweep -> Reclaim scalping benchmark

The first locked production-direction research candidate is
`LiquiditySweepReclaimScalp`, intended for **1m and 5m Binance Futures
scalping research**.

The v1 signal is pre-registered before results are inspected:

- prior liquidity level = previous 20 completed candles' rolling low/high,
- minimum sweep depth = 5 bps beyond that prior level,
- reclaim must close back through the swept level,
- long closes in the upper 60% of the rejection candle; short closes in the
  lower 40%,
- signal candle must have non-zero volume,
- Freqtrade enters on the next candle, not inside the signal candle.

The v1 execution profile is also frozen before results:

- 1x leverage for edge isolation,
- +0.8% gross ROI target,
- -0.5% stop,
- maximum holding time 30 minutes,
- 5 bps fee per side plus the explicit 4 bps round-trip slippage stress already
  applied by the execution metrics layer.

Do not tune these values on the same sample after seeing results. If a later
parameter search is required, it must use a separate development window and an
untouched final test.

Large universes are run in deterministic alphabetical batches with
`--pair-offset` and `--max-pairs`. The selected pair list and the SHA-256 of
the exact strategy source are written into the run manifest.

## Breakout -> Retest scalping benchmark

The second locked production-direction research candidate is
`BreakoutRetestScalp`, also tested on **1m and 5m Binance Futures scalping**
under the same execution overlay as the liquidity-sweep benchmark.

The v1 rule is frozen before results are inspected:

- prior range = previous 20 completed candles,
- breakout candle = previous candle closes at least 5 bps beyond that range,
- retest candle = immediately following candle revisits the broken level within
  a 10 bps tolerance,
- retest must close back on the breakout side,
- long retest closes in the upper 55% of its candle; short retest closes in the
  lower 45%,
- entry occurs on the candle after the completed retest signal.

Execution remains unchanged for comparability: 1x leverage, +0.8% gross ROI
target, -0.5% stop, 30-minute maximum hold, the same Freqtrade fee, and the same
post-backtest slippage stress. Do not tune these values on the same sample after
seeing results.

## Compression -> Expansion scalping benchmark

The third locked production-direction research candidate is
`CompressionExpansionScalp`, tested on **1m and 5m Binance Futures scalping**
under the same execution overlay as the previous price-action benchmarks.

The v1 rule is frozen before results are inspected:

- compression window = previous 10 completed candles,
- historical compression baseline = median 10-candle range width from an older,
  non-overlapping 50-sample history,
- compression requires current prior 10-candle range width <= 60% of that
  historical baseline,
- expansion candle range must be at least 1.5x the median normalized range of
  the prior 10 candles,
- expansion close must break the compressed range by at least 5 bps,
- long close must finish in the upper 65% of the expansion candle; short close
  must finish in the lower 35%,
- entry occurs on the candle after the completed expansion signal.

Execution remains unchanged for comparability: 1x leverage, +0.8% gross ROI
target, -0.5% stop, 30-minute maximum hold, the same Freqtrade fee, and the same
post-backtest slippage stress. Do not tune these values on the same sample after
seeing results.

## One-command scalping benchmark suite

Use `scripts/run_scalping_suite.py` to run the locked 1m/5m scalping smoke
benchmarks without manually launching each strategy/timeframe combination.

The suite currently covers:

- `LiquiditySweepReclaimScalp`
- `BreakoutRetestScalp`
- `CompressionExpansionScalp`
- `BosChochScalp`
- 1m and 5m
- the same fee, slippage, stake, pair batch, and date window for every run.

When `--reuse-root` is supplied, the suite reuses an existing run only when the
strategy SHA-256, timeframe, date window, costs, pair offset, pair count, and
exact `pairs.txt` all match. Anything missing or mismatched is executed
automatically.

Example:

```powershell
docker compose run --rm --entrypoint python freqtrade /freqtrade/user_data/research_lab/scripts/run_scalping_suite.py `
  --config /freqtrade/user_data/config.json `
  --data-dir /freqtrade/user_data/data/binance `
  --catalog-1m /freqtrade/user_data/research/catalog_20250920_20260920/dataset_catalog.csv `
  --catalog-5m /freqtrade/user_data/research/catalog_5m_20250920_20260920/dataset_catalog.csv `
  --reuse-root /freqtrade/user_data/research/execution `
  --output-dir /freqtrade/user_data/research/execution/scalping_suite_smoke5_v1 `
  --start 2025-09-20 `
  --end 2026-09-20 `
  --max-pairs 5
```

Outputs:

- `suite_summary.csv`: consolidated fee + slippage adjusted metrics.
- `SCALPING_SUITE_REPORT.md`: readable report with total, long, and short
  profit factor/expectancy.
- `suite_manifest.json`: run/reuse settings and any errors.
- `runs/`: only benchmarks that were not safely reusable.

A `POSITIVE_SMOKE` label is diagnostic only. It is not promotion to live
trading; lookahead checks, broader-universe testing, walk-forward validation,
and paper trading are still required.

## BOS / CHOCH scalping benchmark

The fourth locked price-action candidate is `BosChochScalp`.

The v1 rule is frozen before results are inspected:

- market-structure levels = previous 20 completed candles,
- confirmed break requires a close at least 5 bps beyond the prior structure level,
- break candle normalized range must be at least 1.20x the prior 20-candle median,
- long breaks must close in the upper 65% of the candle; short breaks in the lower 35%,
- the first confirmed break only initializes directional structure state,
- a later same-direction break is tagged BOS,
- a later opposite-direction break is tagged CHOCH,
- entry occurs only after the completed break candle.

No centered/fractal pivot requiring future candles is used. Execution remains the
same 1x, +0.8% ROI, -0.5% stop, 30-minute maximum hold benchmark so signal
families remain comparable.

## Kivanc / TradingView locked batch v1

The remaining locked TradingView/Kivanc research set is executed in the same
one-command 1m/5m suite. The first smoke test freezes the published/default
logic before any result-driven tuning:

- `TurtleTradeChannelsScalp`: 20-bar prior high/low breakout entry from TuTCI.
- `IsolatedPeakBottomScalp`: the published PEAK1/PEAK2 and BOT1/BOT2
  confirmation logic; signals are acted on only after the confirming candle.
- `VolumeBasedColouredBarsScalp`: crypto-oriented 30-bar average volume and
  only the original strong-volume (>150% average) bullish/bearish bar class is
  promoted to an entry candidate.
- `FollowLineScalp`: published BB(21, 1 std) + ATR(5) stateful Follow Line;
  entries occur when trend direction flips.
- `SqueezeMomentumV2Scalp`: published LazyBear/Kivanc momentum line with
  5-bar signal SMA; entries are line crossovers.
- `ProgressiveTrendTrackerScalp`: published PTT default periods
  (5, 5, 2, 10), VIDYA/VAR smoothing, and price cross of PTT lower/upper lines.
- `TurtleVhfFilteredScalp`: VHF is not treated as a standalone buy/sell
  system. It is used as a trend-regime filter on the Turtle breakout because
  the original VHF description explicitly defines it as a trend/range filter.

All seven candidates keep the same suite execution overlay (1x, +0.8% gross
ROI, -0.5% stop, 30-minute maximum hold, identical fee and post-backtest
slippage) so the first pass compares signal quality rather than exit tuning.
Any positive smoke result still requires lookahead checks, broader-universe
testing, walk-forward validation, and paper trading.

## Known research limitations

- The current 725-contract list contains contracts active on the download date;
  it does not include every delisted contract and can have survivorship bias.
- Newly listed contracts do not have 365 days of history.
- OHLCV cannot reproduce order-book queue position or market impact.
- A fixed slippage assumption is a benchmark, not an execution guarantee.
- Invite-only TradingView scripts cannot be reconstructed; they require forward
  testing from recorded alerts.


## Multi-system discovery comparison

Each signal/timeframe experiment is run independently first. The full
`discovery_tests.csv` preserves all tested hypotheses before candidate filtering.
Those full tables—not prefiltered candidate files—are the canonical inputs to
`scripts/compare_discovery.py`. The comparison recomputes Benjamini-Hochberg
across every hypothesis from every supplied system/timeframe, producing
`global_validation_q_value` and `global_discovery_pass`. Cross-system FDR is also applied only to hypotheses that independently passed the train screen, so train-rejected rows cannot inflate the validation family.

This comparison stage is deliberately **train + validation only**. If any input
contains a column with `holdout` in its name, the comparison fails closed.
Each row also carries its experiment's minimum train/validation event thresholds,
so global selection cannot silently loosen a stricter experiment. Holdout remains
untouched until the global candidate set is frozen. This is the basis for
comparing one universal system, different systems per coin, or later hybrid
systems without using holdout as an optimizer.

Cross-system outputs are `combined_discovery_tests.csv`,
`global_candidates_train_validation.csv`, `system_coverage.csv`, and
`comparison_manifest.json`.


### Timeframe-safe horizon semantics

Event-study horizons are candle counts, never mislabeled as minutes. The output
always contains both `horizon_bars` and the derived `holding_minutes`.

Examples with the default bar horizons:

| Timeframe | 1 bar | 3 bars | 5 bars | 10 bars | 20 bars | 60 bars |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1m | 1m | 3m | 5m | 10m | 20m | 60m |
| 15m | 15m | 45m | 75m | 150m | 300m | 900m |
| 4h | 240m | 720m | 1200m | 2400m | 4800m | 14400m |

This prevents a 5-bar event on 15m data from being incorrectly reported as a
5-minute hold.


## Generic signal engine

Signal generation and statistical evaluation are separate contracts. A strategy
produces a `SignalSet(long, short)` aligned to the closed-candle OHLCV frame;
`analyze_signals(...)` then applies the same next-open entry, costs, gap checks,
split isolation, horizons, MAE/MFE, and discovery statistics to every strategy.

`analyze_pair(...)` remains the MACD compatibility wrapper and is regression
tested to produce the same output as
`analyze_signals(..., macd_crossover_signals(...))`. New Kıvanç-style,
liquidity-sweep, breakout/retest, price-action, or hybrid signal generators
therefore plug into one canonical evaluator instead of duplicating research
logic.
