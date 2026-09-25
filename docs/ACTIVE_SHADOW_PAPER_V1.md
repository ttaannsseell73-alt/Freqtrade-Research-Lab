# Active Shadow Paper v1

This layer turns the frozen 30-setup cohort into a persistent paper/shadow
portfolio without enabling live orders.

## Signal semantics

- Only confirmed closed candles can create a new event.
- Entry price is the next bar open after the signal candle.
- First run evaluates only the latest confirmed candle; later runs use per-symbol
  checkpoints so scheduler gaps can be caught up without replaying old history.
- Same-symbol same-direction repeats are suppressed.
- Opposite signals close the existing paper position first. A reversal is only
  opened if the frozen direction rule, evidence gate, liquidity gate and
  portfolio-risk gate all admit it.
- One-sided frozen setups still exit on the opposite strategy signal, but they
  cannot reverse into a disabled direction.

## Risk semantics

The shadow portfolio uses the canonical runtime policy:

- max gross exposure: 70%
- max open positions: 20
- one position per symbol
- daily loss kill-switch: -3%
- portfolio drawdown kill-switch: -10%

Paper returns use a conservative 15 bps modeled round-trip cost, matching the
research execution stress convention (fees plus slippage).

## Evidence and tradability

The exact evidence snapshot that produced the frozen active pool is preserved in
`config/active_evidence_v1.json`.

The TradingView/Kivanc tradability thresholds are preserved in
`config/tradability_policy_v1.json`.

On a runner that can reach Binance mainnet public Futures endpoints, fresh
entries use the live STRONG / TRADEABLE / REVIEW / BLOCK classification.

GitHub-hosted runners in the current environment cannot reach the Binance
mainnet Futures REST endpoint. The shadow runner therefore fails over to
Binance Futures TESTNET price candles. This fallback is used only for
near-real-time operational shadowing:

- it never enables live orders;
- TESTNET liquidity/depth is not treated as mainnet liquidity;
- AlphaTrend setups are observe-only under fallback because AlphaTrend uses
  MFI volume and TESTNET volume is not a faithful mainnet proxy;
- the daily Binance Vision forward-active workflow remains the canonical source
  of unseen mainnet performance evidence.

A self-hosted runner with mainnet public market access can use the same code
without the fallback and will automatically re-enable the full tradability gate.
