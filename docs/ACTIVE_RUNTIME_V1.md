# CoinStrategyLab Active Runtime v1

This document is the canonical runtime contract built on the frozen 30-setup
active pool.

## Canonical chain

```
Binance USD-M universe
  -> research matrix
  -> multi-window robustness
  -> 96 Tier-A execution candidates
  -> 30-setup ACTIVE pool
  -> Active Runtime Router
  -> direction gate
  -> portfolio risk budget
  -> paper execution
  -> forward evidence
  -> API/UI
```

The 30-setup pool is the operating base. The 10 CORE setups are a stricter
subset, not the entire system.

## Runtime invariants

- Default is `NO_TRADE`.
- Only symbols in `config/active_pool_v1.json` are routable.
- Direction is enforced per setup: `BOTH`, `LONG_ONLY`, or `SHORT_ONLY`.
- Frozen setup weights total 0.68 of equity.
- Runtime maximum gross exposure is 0.70.
- Maximum simultaneous positions is 20.
- A symbol cannot be opened twice.
- Paper daily loss kill-switch: -3%.
- Paper portfolio drawdown kill-switch: -10%.
- Runtime is paper-only until forward evidence and execution safety are
  separately promoted.

## Position sizing

- CORE setup: 3% notional weight.
- ACTIVE setup: 2% notional weight.
- One-direction ACTIVE setup: 1.5% notional weight.

These are paper portfolio weights, not leverage recommendations.

## Promotion rule

Historical diagnostics are supporting evidence only. The frozen cohort must
survive forward data that starts after 2026-09-25. No setup may be silently
reselected using forward results.
