# Active Intent Gate v1

The frozen 30-setup production cohort remains unchanged. This layer imports only
the useful signal-management rules proven in the former TradingView/Kivanc
research lane.

## Locked behavior

- The frozen setup remains the primary trigger for each active symbol.
- A primary signal must be fresh and come from a confirmed closed candle.
- Additional same-direction strategies never create additional positions. They
  increase `support_count` on the same symbol-level intent.
- Opposing fresh LONG and SHORT signals create `DIRECTION_CONFLICT` and no
  order intent.
- Thin samples, extreme PF, excessive drawdown, extreme compounding or
  inconsistent evidence put the primary signal into `EVIDENCE_REVIEW`.
- Weak or unavailable liquidity is `OBSERVE_ONLY`.
- The existing ActiveRouter remains the final risk gate: allowed direction,
  one-position-per-symbol, 70% gross exposure, maximum 20 positions and both
  kill-switches remain unchanged.
- Candidate strategies are support/research inputs only. They cannot silently
  replace the frozen 30 or become the primary trigger.

## API

`POST /v1/intent/admit` accepts one symbol, its fresh strategy observations and
the current portfolio state. A TRADE response contains one deterministic
`intent_id`, one side and one weight regardless of the number of supporting
strategies.

`GET /v1/intent/policy` exposes the current evidence and conflict rules.

The lower-level `POST /v1/admit` endpoint is retained for router diagnostics
and execution plumbing tests. Production strategy flow should use the intent
gate before exchange submission.
