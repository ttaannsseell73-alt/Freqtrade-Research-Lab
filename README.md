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
- Default round-trip cost is 14 bps: 10 bps fee + 4 bps slippage assumption.
- The 365-day range is split chronologically 60% / 20% / 20%.
- Benjamini-Hochberg false-discovery control is applied across the coin universe.

The MACD benchmark is a control group, not a production signal.

Every research run now carries a deterministic `experiment_id` built from
`system_id + system_version + timeframe + parameters + costs + horizon bars`.
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
  --cost-bps 14
```

Outputs:

- `dataset_coverage.csv`: candles and date coverage for every contract.
- `summary_discovery.csv`: train + validation statistics only; no holdout rows; tagged with experiment identity.
- `candidates_train_validation.csv`: candidates selected without holdout data.
- `holdout_report.csv`: holdout results only for the already-frozen candidate set.
- `errors.csv`: new listings, missing data, or unreadable files.
- `result_summary.json`: compact machine-readable outcome counts and status.
- `run_manifest.json`: exact settings, split boundaries, and limitations.
- `SHA256SUMS.txt`: integrity hashes for every result file.

## Freqtrade execution benchmark

After the event study freezes a shortlist, use the included strategy with
Freqtrade's normal backtester:

```powershell
docker compose run --rm freqtrade backtesting `
  --config user_data/config.json `
  --strategy-path user_data/research_lab/strategies `
  --strategy BenchmarkMacd1m `
  --timeframe 1m `
  --timerange 20250919-20260920 `
  --cache none
```

Do not run this across all 725 pairs with `max_open_trades=3`; portfolio slot
competition would contaminate the per-coin comparison. The event study performs
independent discovery first, and execution backtests follow on frozen batches.

## Known research limitations

- The current 725-contract list contains contracts active on the download date;
  it does not include every delisted contract and can have survivorship bias.
- Newly listed contracts do not have 365 days of history.
- OHLCV cannot reproduce order-book queue position or market impact.
- A fixed slippage assumption is a benchmark, not an execution guarantee.
- Invite-only TradingView scripts cannot be reconstructed; they require forward
  testing from recorded alerts.


## Multi-system discovery comparison

Each signal/timeframe experiment is run independently first. Candidate tables can
then be combined with `scripts/compare_discovery.py` to measure which systems
produce validated candidates on which contracts.

This comparison stage is deliberately **train + validation only**. If any input
contains a column with `holdout` in its name, the comparison fails closed.
Holdout remains untouched until the system/candidate set is frozen. This is the
basis for comparing one universal system, different systems per coin, or later
hybrid systems without using holdout as an optimizer.


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
