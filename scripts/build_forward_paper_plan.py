from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def to_pair(symbol: str) -> str:
    if not symbol.endswith("USDT"):
        raise ValueError(f"Expected USDT symbol, got {symbol}")
    return f"{symbol[:-4]}/USDT:USDT"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--end", required=True, help="Exclusive UTC date YYYY-MM-DD")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-forward-days", type=int, default=1)
    args = parser.parse_args()

    cohort = json.loads(args.cohort.read_text(encoding="utf-8"))
    start = pd.Timestamp(str(cohort["forward_cutoff"]), tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    elapsed_days = int((end - start).total_seconds() // 86400)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ready = elapsed_days >= args.min_forward_days

    rows = []
    if ready:
        for item in cohort["setups"]:
            rows.append({
                "symbol": item["symbol"],
                "freqtrade_pair": to_pair(item["symbol"]),
                "timeframe": item["timeframe"],
                "strategy_id": item["strategy_id"],
                "freqtrade_strategy_class": item["freqtrade_strategy_class"],
                "robust_tier": "A",
                "confidence_score": float(item["risk_score"]),
                "worst_oos_floor_bps": 0.0,
                "worst_15bps_expectancy": 0.0,
                "worst_holdout_profit_factor": 0.0,
                "validation_start": start.date().isoformat(),
                "validation_end": end.date().isoformat(),
                "forward_cohort": cohort["cohort_id"],
            })

    pd.DataFrame(rows).to_csv(args.output_dir / "forward_plan.csv", index=False)
    manifest = {
        "schema_version": 1,
        "cohort_id": cohort["cohort_id"],
        "forward_start": start.date().isoformat(),
        "forward_end": end.date().isoformat(),
        "elapsed_days": elapsed_days,
        "minimum_days": args.min_forward_days,
        "ready": ready,
        "setup_count": len(rows),
        "status": "READY" if ready else "WAITING_FOR_FORWARD_DATA",
    }
    (args.output_dir / "FORWARD_PLAN_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
