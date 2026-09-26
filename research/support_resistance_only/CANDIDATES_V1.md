# Support / Resistance Only Research — Candidate Pool v1

Bu havuzda adaylar kaynak iddialarına göre ELENMEZ.
Kaynakta yazan win-rate / accuracy yalnızca "CLAIMED" metadata'dır.
Nihai kabul yalnızca bizim Binance Futures backtest, maliyet, determinism ve OOS kontrollerinden sonra yapılır.

## Adaylar

| ID | Aile | Ana Mantık | Kaynak İddiası | Veri | Ön Eleme |
|---|---|---|---|---|---|
| SR_MTF_CONFLUENCE | Multi-Timeframe S/R | 1H/4H/D gibi çoklu TF swing S/R seviyelerini cluster edip ortak seviyede bounce / rejection / breakout | CLAIMED 80–90% major S/R, 75–85% intraday | OHLCV | YOK |
| SR_ORDERBOOK_REJECTION | Order-book / wick S/R | Destek/direnç savunması, wick rejection, kırılım / failed break | CLAIMED 70–80% tick, 80–90% L2, 85–95% snapshots | OHLCV + opsiyonel L2 | YOK |
| SR_ENSEMBLE | Ensemble S/R | Statistical peaks + MTF + volume-profile + rejection/orderbook level agreement | CLAIMED family; source marks empirical validation pending | OHLCV (+ optional richer data) | YOK |
| SR_STAT_CLUSTER | Statistical Peak/Trough Clustering | Confirmed pivot highs/lows, multi-touch clustering, recency/strength | CLAIMED 70–80% D/4H, 65–75% intraday | OHLCV | YOK |
| SR_BREAK_RETEST | Breakout → Retest | Resistance break + retest hold = long; support break + retest reject = short | No trusted WR claim | OHLCV | YOK |
| SR_ROLE_REVERSAL | S↔R Flip | Broken resistance becomes support / broken support becomes resistance | No trusted WR claim | OHLCV | YOK |
| SR_MULTI_TOUCH_BOUNCE | Multi-touch Bounce | 2/3+ confirmed touches in price zone + closed-candle rejection | No trusted WR claim | OHLCV | YOK |
| SR_PIVOT_REVERSAL | Pivot Reversal | Confirmed swing support bounce / resistance rejection | No trusted WR claim | OHLCV | YOK |
| SR_RANGE_EDGE | Range Edge | Established support-resistance box: support long, resistance short | No trusted WR claim | OHLCV | YOK |
| SR_RANGE_BREAK_RETEST | Range Breakout → Retest | Box break + return to broken boundary + hold/reject | No trusted WR claim | OHLCV | YOK |

## Kaynak notları

- DennisDyallo/PineScript — Statistical Peak/Trough S/R clustering.
- DennisDyallo/PineScript — Multi-Timeframe Confluence S/R; documentation claims 80–90% for major S/R.
- DennisDyallo/PineScript — Order Book S/R; documentation claims 80–90% with Level 2 and 85–95% with snapshots.
- DennisDyallo/PineScript — Ensemble S/R; source explicitly says empirical validation pending. Candidate remains in pool.
- Other public strategy examples are used only to extract generic S/R behavior (break/retest, role reversal, multi-touch rejection), not to trust advertised performance.

## Locked research rules

1. No candidate is rejected because its public accuracy claim looks unrealistic.
2. Public accuracy is not treated as verified performance.
3. Signal must be generated from support/resistance structure and price interaction with those levels.
4. Completed-candle execution only; no future bars may be used at decision time.
5. Repainting / lookahead is an automatic implementation failure, not a strategy-family rejection.
6. Same strategy scans the Binance USD-M Futures universe coin-by-coin.
7. Primary timeframes: 1m, 5m, 15m, 1h, 4h.
8. Costs and slippage are applied before ranking.
9. Output is per strategy × coin × timeframe: trades, win rate, expectancy, PF, net PnL, max drawdown, hold time and robustness.
10. High claimed accuracy strategies are tested first, not discarded first.

## First execution batch

Priority order for implementation/testing, NOT performance ranking:
1. SR_MTF_CONFLUENCE
2. SR_ORDERBOOK_REJECTION (OHLCV-compatible version first; L2 separately)
3. SR_ENSEMBLE
4. SR_STAT_CLUSTER
5. SR_BREAK_RETEST
6. SR_ROLE_REVERSAL
7. SR_MULTI_TOUCH_BOUNCE
8. SR_PIVOT_REVERSAL
9. SR_RANGE_EDGE
10. SR_RANGE_BREAK_RETEST
