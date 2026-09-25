# Active Runtime API v1

The API is the stable boundary between the CoinStrategyLab engine and future UI
clients. UI code must not read research CSV files directly.

## Start

```bash
python -m pip install -e ".[api]"
python scripts/run_active_api.py
```

Default bind: `127.0.0.1:8787`.

## Endpoints

- `GET /health` - runtime health and frozen cohort identity.
- `GET /v1/system` - active/core counts and portfolio risk policy.
- `GET /v1/routes` - all 30 frozen routes.
- `GET /v1/routes/{symbol}` - one route.
- `POST /v1/admit` - pure paper admission decision.

`POST /v1/admit` does not place an order. It returns one of:
`TRADE`, `NO_TRADE`, or `KILL_SWITCH`, with the exact reason.

The API remains paper-only until forward evidence and exchange execution safety
are promoted separately.
