# Active Dashboard v1

The dashboard is a separate React/Vite client for the Active Runtime API.

## Purpose

It is an operator view for the frozen 30-setup paper system. It shows:

- 10 CORE + 20 ACTIVE setups,
- coin, strategy, timeframe and allowed direction,
- per-setup paper weight and active score,
- configured versus maximum gross exposure,
- daily and portfolio kill-switch thresholds,
- current paper portfolio state.

The dashboard has no live-order action.

## Development

Backend:

```bash
python -m pip install -e ".[api]"
python scripts/run_active_api.py
```

Dashboard:

```bash
cd ui/active-dashboard
npm install
npm run dev
```

The dashboard defaults to `http://127.0.0.1:8787` for the API. Override with
`VITE_API_BASE` if needed.
