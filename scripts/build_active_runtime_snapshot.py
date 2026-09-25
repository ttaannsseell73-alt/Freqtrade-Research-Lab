from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coin_strategy_lab.runtime import ActiveRouter


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--runtime-config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    router = ActiveRouter.from_files(args.cohort, args.runtime_config)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "symbol": x.symbol,
            "timeframe": x.timeframe,
            "strategy_id": x.strategy_id,
            "strategy_class": x.strategy_class,
            "pool_tier": x.pool_tier,
            "direction": x.direction,
            "paper_weight": x.paper_weight,
            "active_score": x.active_score,
        }
        for x in router.setups
    ]
    pd.DataFrame(rows).to_csv(
        args.output_dir / "ACTIVE_RUNTIME_TABLE.csv",
        index=False,
    )

    runtime = {
        "schema_version": 1,
        "runtime_id": "active-runtime-v1",
        "cohort_id": router.cohort_id,
        "mode": "paper_only",
        "default": "NO_TRADE",
        "setup_count": len(router.setups),
        "configured_weight": router.configured_weight,
        "policy": {
            "max_gross_exposure": router.policy.max_gross_exposure,
            "max_open_positions": router.policy.max_open_positions,
            "daily_loss_limit": router.policy.daily_loss_limit,
            "portfolio_drawdown_limit": router.policy.portfolio_drawdown_limit,
            "reject_duplicate_symbol": router.policy.reject_duplicate_symbol,
        },
        "routes": {
            x.symbol: {
                "timeframe": x.timeframe,
                "strategy_id": x.strategy_id,
                "strategy_class": x.strategy_class,
                "pool_tier": x.pool_tier,
                "direction": x.direction,
                "paper_weight": x.paper_weight,
                "active_score": x.active_score,
            }
            for x in router.setups
        },
    }
    (args.output_dir / "ACTIVE_RUNTIME_ROUTER.json").write_text(
        json.dumps(runtime, indent=2),
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "cohort_id": router.cohort_id,
        "setup_count": len(router.setups),
        "core_count": sum(x.pool_tier == "CORE" for x in router.setups),
        "active_count": sum(x.pool_tier == "ACTIVE" for x in router.setups),
        "configured_weight": router.configured_weight,
        "max_gross_exposure": router.policy.max_gross_exposure,
        "remaining_weight_buffer": (
            router.policy.max_gross_exposure - router.configured_weight
        ),
        "directions": {
            d: sum(x.direction == d for x in router.setups)
            for d in ["BOTH", "LONG_ONLY", "SHORT_ONLY"]
        },
        "status": "RUNTIME_READY",
    }
    (args.output_dir / "ACTIVE_RUNTIME_SUMMARY.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
