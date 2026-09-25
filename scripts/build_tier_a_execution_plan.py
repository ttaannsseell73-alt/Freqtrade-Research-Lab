from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


WINDOWS = {
    "5m": ("2026-03-28", "2026-09-24"),
    "15m": ("2025-09-24", "2026-09-24"),
    "1h": ("2025-09-24", "2026-09-24"),
    "4h": ("2024-09-24", "2026-09-24"),
    "1d": ("2023-09-25", "2026-09-24"),
}

CLASS_MAP = {
    ("5m", "pmax"): "CSL5mPMax",
    ("5m", "qqe_ssl_wae"): "CSL5mQQESSLWAE",
    ("15m", "alphatrend"): "CSL15mAlphaTrend",
    ("15m", "pmax"): "CSL15mPMax",
    ("15m", "squeeze_momentum"): "CSL15mSqueezeMomentum",
    ("1h", "alphatrend"): "CSL1hAlphaTrend",
    ("1h", "mavilimw"): "CSL1hMavilimW",
    ("1h", "pmax"): "CSL1hPMax",
    ("1h", "qqe_ssl_wae"): "CSL1hQQESSLWAE",
    ("1h", "squeeze_momentum"): "CSL1hSqueezeMomentum",
    ("1h", "utbot"): "CSL1hUTBot",
    ("4h", "alphatrend"): "CSL4hAlphaTrend",
    ("4h", "mavilimw"): "CSL4hMavilimW",
    ("4h", "qqe_ssl_wae"): "CSL4hQQESSLWAE",
    ("4h", "squeeze_momentum"): "CSL4hSqueezeMomentum",
    ("4h", "utbot"): "CSL4hUTBot",
    ("1d", "qqe_ssl_wae"): "CSL1dQQESSLWAE",
    ("1d", "utbot"): "CSL1dUTBot",
}


def to_freqtrade_pair(symbol: str) -> str:
    if not symbol.endswith("USDT"):
        raise ValueError(f"Unsupported non-USDT symbol: {symbol}")
    return f"{symbol[:-4]}/USDT:USDT"


def build_plan(router_csv: Path, mode: str) -> pd.DataFrame:
    frame = pd.read_csv(router_csv)
    required = {
        "symbol", "timeframe", "strategy_id", "robust_tier",
        "confidence_score", "worst_oos_floor_bps",
        "worst_15bps_expectancy", "worst_holdout_profit_factor",
        "total_trades",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Router CSV missing columns: {sorted(missing)}")

    frame = frame[frame["robust_tier"].eq("A")].copy()
    frame = frame[frame["timeframe"].isin(WINDOWS)].copy()
    if frame.empty:
        raise RuntimeError("No Tier-A execution candidates found")

    frame["freqtrade_pair"] = frame["symbol"].map(to_freqtrade_pair)
    frame["freqtrade_strategy_class"] = [
        CLASS_MAP[(str(tf), str(strategy))]
        for tf, strategy in zip(frame["timeframe"], frame["strategy_id"])
    ]
    frame["validation_start"] = frame["timeframe"].map(lambda tf: WINDOWS[str(tf)][0])
    frame["validation_end"] = frame["timeframe"].map(lambda tf: WINDOWS[str(tf)][1])

    frame = frame.sort_values(
        ["timeframe", "strategy_id", "confidence_score"],
        ascending=[True, True, False],
    ).reset_index(drop=True)

    if mode == "smoke":
        frame = (
            frame.groupby(["timeframe", "strategy_id"], as_index=False, sort=True)
            .head(1)
            .reset_index(drop=True)
        )
    elif mode != "all":
        raise ValueError(f"Unknown mode: {mode}")

    return frame


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--router-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=["smoke", "all"], default="smoke")
    parser.add_argument("--max-groups", type=int, default=0)
    args = parser.parse_args()

    plan = build_plan(args.router_csv, args.mode)
    if args.max_groups > 0:
        group_keys = (
            plan[["timeframe", "strategy_id"]]
            .drop_duplicates()
            .head(args.max_groups)
        )
        plan = plan.merge(group_keys, on=["timeframe", "strategy_id"], how="inner")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan.to_csv(args.output_dir / "execution_plan.csv", index=False)

    groups = []
    for (timeframe, strategy_id, strategy_class), group in plan.groupby(
        ["timeframe", "strategy_id", "freqtrade_strategy_class"],
        sort=True,
    ):
        groups.append(
            {
                "timeframe": str(timeframe),
                "strategy_id": str(strategy_id),
                "strategy_class": str(strategy_class),
                "pair_count": int(len(group)),
                "pairs": group["freqtrade_pair"].tolist(),
                "symbols": group["symbol"].tolist(),
                "start": str(group["validation_start"].iloc[0]),
                "end": str(group["validation_end"].iloc[0]),
            }
        )

    manifest = {
        "schema_version": 1,
        "mode": args.mode,
        "tier": "A",
        "candidate_count": int(len(plan)),
        "group_count": len(groups),
        "groups": groups,
    }
    (args.output_dir / "execution_plan.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    print(
        f"mode={args.mode} candidates={len(plan)} groups={len(groups)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
