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
5. Open the untouched holdout report once selection is frozen.
6. Run Freqtrade execution backtests on the surviving candidates.
7. Promote only stable candidates to dry-run/paper trading.

## First benchmark: 1-minute MACD crossover

The first benchmark deliberately uses a simple model. Its purpose is to prove
the research pipeline before complex price-action or TradingView models are
added.

- Signal is calculated on a closed candle.
- Entry is the next candle's open (no same-candle fill or lookahead).
- Long and short are measured separately.
- Forward horizons: 1, 3, 5, 10, 20, and 60 minutes.
- Metrics include net return, win rate, profit factor, MAE, and MFE.
- Default round-trip cost is 14 bps: 10 bps fee + 4 bps slippage assumption.
- The 365-day range is split chronologically 60% / 20% / 20%.
- Benjamini-Hochberg false-discovery control is applied across the coin universe.

The MACD benchmark is a control group, not a production signal.

## Install beside Freqtrade

From the official Freqtrade repository root in PowerShell:

```powershell
git clone https://github.com/ttaannsseell73-alt/Freqtrade-Research-Lab.git .\user_data\research_lab
```

The normal Freqtrade Docker mount makes the repository available inside the
container at `/freqtrade/user_data/research_lab`.

## Run the event study

Run only after the 1-minute download is complete:

```powershell
docker compose run --rm --entrypoint python freqtrade /freqtrade/user_data/research_lab/scripts/macd_event_study.py `
  --data-dir /freqtrade/user_data/data/binance `
  --output-dir /freqtrade/user_data/research/results/macd_1m_20250919_20260919 `
  --start 2025-09-19 `
  --end 2026-09-20 `
  --timeframe 1m `
  --cost-bps 14
```

Outputs:

- `dataset_coverage.csv`: candles and date coverage for every contract.
- `summary_all_periods.csv`: all pair/direction/horizon/period statistics.
- `candidates_train_validation.csv`: candidates selected without holdout data.
- `holdout_report.csv`: final untouched-period results for those candidates.
- `errors.csv`: new listings, missing data, or unreadable files.
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

