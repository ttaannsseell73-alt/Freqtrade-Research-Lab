from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coin_strategy_lab.active_pool import build_active_pool, simulate_historical_portfolio


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-results", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    execution = pd.read_csv(args.execution_results)
    active = build_active_pool(execution)
    trades, summary = simulate_historical_portfolio(active, args.artifact_root)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    active.to_csv(args.output_dir / "ACTIVE_POOL.csv", index=False)
    trades.to_csv(args.output_dir / "HISTORICAL_PORTFOLIO_TRADES.csv", index=False)
    (args.output_dir / "HISTORICAL_PORTFOLIO_SUMMARY.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    config = {
        "schema_version": 1,
        "mode": "paper_active_pool",
        "default": "NO_TRADE",
        "setup_count": int(len(active)),
        "setups": [
            {
                "symbol": str(r.symbol),
                "timeframe": str(r.timeframe),
                "strategy_id": str(r.strategy_id),
                "strategy_class": str(r.strategy_class),
                "pool_tier": str(r.pool_tier),
                "direction": str(r.paper_direction),
                "paper_weight": float(r.paper_weight),
                "active_score": float(r.active_score),
            }
            for r in active.itertuples(index=False)
        ],
    }
    (args.output_dir / "ACTIVE_POOL_CONFIG.json").write_text(
        json.dumps(config, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
