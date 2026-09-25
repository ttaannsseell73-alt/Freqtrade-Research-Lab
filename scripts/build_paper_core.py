from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


CORE_MAX_DRAWDOWN = 0.45
CORE_MIN_PROFIT_FACTOR = 1.50
CORE_MIN_EXPECTANCY_BPS = 20.0

WATCH_MAX_DRAWDOWN = 0.55
WATCH_MIN_PROFIT_FACTOR = 1.35
WATCH_MIN_EXPECTANCY_BPS = 10.0


def classify_risk(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol",
        "timeframe",
        "strategy_id",
        "status",
        "trades",
        "expectancy_bps",
        "profit_factor",
        "max_drawdown_compounded",
        "both_directions_positive",
        "long_expectancy",
        "short_expectancy",
        "research_confidence_score",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Execution results missing columns: {sorted(missing)}")

    out = frame.copy()
    out["drawdown_abs"] = out["max_drawdown_compounded"].abs()

    core = (
        out["status"].eq("EXECUTION_PASS")
        & (out["drawdown_abs"] <= CORE_MAX_DRAWDOWN)
        & (out["profit_factor"] >= CORE_MIN_PROFIT_FACTOR)
        & (out["expectancy_bps"] >= CORE_MIN_EXPECTANCY_BPS)
        & out["both_directions_positive"].astype(bool)
    )

    watch = (
        out["status"].eq("EXECUTION_PASS")
        & ~core
        & (out["drawdown_abs"] <= WATCH_MAX_DRAWDOWN)
        & (out["profit_factor"] >= WATCH_MIN_PROFIT_FACTOR)
        & (out["expectancy_bps"] >= WATCH_MIN_EXPECTANCY_BPS)
    )

    out["risk_tier"] = "REJECT"
    out.loc[watch, "risk_tier"] = "WATCH"
    out.loc[core, "risk_tier"] = "CORE"

    def direction(row: pd.Series) -> str:
        long_exp = float(row["long_expectancy"])
        short_exp = float(row["short_expectancy"])
        if long_exp > 0 and short_exp > 0:
            return "BOTH"
        if long_exp > 0:
            return "LONG_ONLY"
        if short_exp > 0:
            return "SHORT_ONLY"
        return "NONE"

    out["paper_direction"] = out.apply(direction, axis=1)

    # Risk score is intentionally bounded and interpretable.
    dd_component = (1.0 - (out["drawdown_abs"] / WATCH_MAX_DRAWDOWN)).clip(0.0, 1.0)
    pf_component = ((out["profit_factor"] - 1.0) / 1.5).clip(0.0, 1.0)
    exp_component = (out["expectancy_bps"] / 250.0).clip(0.0, 1.0)
    research_component = (out["research_confidence_score"] / 100.0).clip(0.0, 1.0)

    out["risk_score"] = 100.0 * (
        0.40 * dd_component
        + 0.25 * pf_component
        + 0.20 * exp_component
        + 0.15 * research_component
    )

    tier_rank = out["risk_tier"].map({"CORE": 2, "WATCH": 1, "REJECT": 0})
    out["_tier_rank"] = tier_rank
    out = out.sort_values(
        ["_tier_rank", "risk_score", "profit_factor", "expectancy_bps"],
        ascending=[False, False, False, False],
    ).drop(columns=["_tier_rank"]).reset_index(drop=True)
    return out


def build_paper_router(filtered: pd.DataFrame) -> dict:
    core = filtered[filtered["risk_tier"].eq("CORE")].copy()
    setups = {}
    for row in core.itertuples(index=False):
        setups[str(row.symbol)] = {
            "timeframe": str(row.timeframe),
            "strategy_id": str(row.strategy_id),
            "direction": str(row.paper_direction),
            "risk_score": float(row.risk_score),
            "expectancy_bps": float(row.expectancy_bps),
            "profit_factor": float(row.profit_factor),
            "max_drawdown_compounded": float(row.max_drawdown_compounded),
            "trades": int(row.trades),
            "status": "PAPER_CORE",
        }

    return {
        "schema_version": 1,
        "mode": "paper_core",
        "default": "NO_TRADE",
        "risk_policy": {
            "core_max_drawdown": CORE_MAX_DRAWDOWN,
            "core_min_profit_factor": CORE_MIN_PROFIT_FACTOR,
            "core_min_expectancy_bps": CORE_MIN_EXPECTANCY_BPS,
            "core_requires_both_directions_positive": True,
            "watch_max_drawdown": WATCH_MAX_DRAWDOWN,
            "watch_min_profit_factor": WATCH_MIN_PROFIT_FACTOR,
            "watch_min_expectancy_bps": WATCH_MIN_EXPECTANCY_BPS,
        },
        "setup_count": len(setups),
        "setups": setups,
    }


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
