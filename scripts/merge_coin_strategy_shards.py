from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


CSV_FILES = [
    "FUTURES_UNIVERSE.csv",
    "DETAILED_RESULTS.csv",
    "TRADES.csv",
    "MASTER_COIN_STRATEGY_MATRIX.csv",
]


def _concat(inputs: list[Path], filename: str) -> pd.DataFrame:
    frames = []
    for path in inputs:
        file = path / filename
        if file.exists() and file.stat().st_size:
            frame = pd.read_csv(file)
            if not frame.empty:
                frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    merged: dict[str, pd.DataFrame] = {}
    for filename in CSV_FILES:
        frame = _concat(args.input_dir, filename)
        merged[filename] = frame
        frame.to_csv(out / filename, index=False)

    master = merged["MASTER_COIN_STRATEGY_MATRIX.csv"]
    if not master.empty:
        master = master.sort_values(
            ["candidate","oos_floor_bps","profit_factor_full","expectancy_bps_full"],
            ascending=False,
        ).reset_index(drop=True)
        master.to_csv(out / "MASTER_COIN_STRATEGY_MATRIX.csv", index=False)

        best_rows = []
        for symbol, group in master.groupby("symbol", sort=True):
            candidates = group[group["candidate"].astype(bool)]
            pool = candidates if not candidates.empty else group
            row = pool.iloc[0].copy()
            row["selection_status"] = "CANDIDATE" if bool(row["candidate"]) else "NO_QUALIFIED_STRATEGY"
            best_rows.append(row)
        pd.DataFrame(best_rows).to_csv(out / "BEST_STRATEGY_BY_COIN.csv", index=False)

        candidates = master[master["candidate"].astype(bool)].copy()
        candidates.to_csv(out / "CANDIDATES.csv", index=False)
        candidates.sort_values(
            ["strategy_id","oos_floor_bps"], ascending=[True,False]
        ).to_csv(out / "BEST_COINS_BY_STRATEGY.csv", index=False)
    else:
        pd.DataFrame().to_csv(out / "BEST_STRATEGY_BY_COIN.csv", index=False)
        pd.DataFrame().to_csv(out / "CANDIDATES.csv", index=False)
        pd.DataFrame().to_csv(out / "BEST_COINS_BY_STRATEGY.csv", index=False)

    errors = []
    summaries = []
    for path in args.input_dir:
        error_file = path / "ERRORS.json"
        if error_file.exists():
            errors.extend(json.loads(error_file.read_text(encoding="utf-8")))
        summary_file = path / "SUMMARY.json"
        if summary_file.exists():
            summaries.append(json.loads(summary_file.read_text(encoding="utf-8")))
    (out / "ERRORS.json").write_text(json.dumps(errors, indent=2, ensure_ascii=False), encoding="utf-8")

    ready = merged["FUTURES_UNIVERSE.csv"]
    summary = {
        "status": "COMPLETE",
        "shards_merged": len(args.input_dir),
        "universe_count": int(len(ready)) if not ready.empty else 0,
        "ready_symbols": int((ready["status"] == "READY").sum()) if not ready.empty and "status" in ready else 0,
        "evaluated_pairs": int(len(master)),
        "candidate_pairs": int(master["candidate"].astype(bool).sum()) if not master.empty else 0,
        "coins_with_candidate": int(master[master["candidate"].astype(bool)]["symbol"].nunique()) if not master.empty else 0,
        "source_summaries": summaries,
    }
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
