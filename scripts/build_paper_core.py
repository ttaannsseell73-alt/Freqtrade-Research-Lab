from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from coin_strategy_lab.risk_filter import (
    CORE_MAX_DRAWDOWN,
    CORE_MIN_PROFIT_FACTOR,
    CORE_MIN_EXPECTANCY_BPS,
    WATCH_MAX_DRAWDOWN,
    WATCH_MIN_PROFIT_FACTOR,
    WATCH_MIN_EXPECTANCY_BPS,
    classify_risk,
    build_paper_router,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execution-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    frame = pd.read_csv(args.execution_results)
    filtered = classify_risk(frame)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    filtered.to_csv(args.output_dir / "RISK_FILTERED_ALL.csv", index=False)

    core = filtered[filtered["risk_tier"].eq("CORE")].copy()
    watch = filtered[filtered["risk_tier"].eq("WATCH")].copy()
    rejected = filtered[filtered["risk_tier"].eq("REJECT")].copy()

    core.to_csv(args.output_dir / "PAPER_CORE.csv", index=False)
    watch.to_csv(args.output_dir / "PAPER_WATCH.csv", index=False)
    rejected.to_csv(args.output_dir / "RISK_REJECTED.csv", index=False)

    router = build_paper_router(filtered)
    (args.output_dir / "PAPER_ROUTER_CONFIG.json").write_text(
        json.dumps(router, indent=2),
        encoding="utf-8",
    )

    summary = {
        "schema_version": 1,
        "input_pairs": int(len(filtered)),
        "paper_core": int(len(core)),
        "paper_watch": int(len(watch)),
        "risk_rejected": int(len(rejected)),
        "core_by_timeframe": {
            str(k): int(v)
            for k, v in core["timeframe"].value_counts().sort_index().items()
        },
        "core_by_strategy": {
            str(k): int(v)
            for k, v in core["strategy_id"].value_counts().sort_index().items()
        },
        "policy": router["risk_policy"],
    }
    (args.output_dir / "RISK_SUMMARY.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
