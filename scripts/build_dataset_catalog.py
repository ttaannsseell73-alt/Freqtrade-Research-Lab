from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freqtrade_research_lab.catalog_io import write_catalog_bundle  # noqa: E402
from freqtrade_research_lab.dataset import build_dataset_catalog  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a quality and coverage catalog for Freqtrade futures OHLCV files."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--timeframes",
        nargs="+",
        default=["1m", "5m", "15m", "1h", "4h", "1d"],
    )
    parser.add_argument("--minimum-coverage", type=float, default=0.80)
    parser.add_argument("--minimum-candles", type=int, default=1_000)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly replace an existing catalog bundle in --output-dir.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    catalog = build_dataset_catalog(
        args.data_dir,
        tuple(args.timeframes),
        start,
        end,
        minimum_coverage=args.minimum_coverage,
        minimum_candles=args.minimum_candles,
        include_source_sha256=True,
    )
    if catalog.empty:
        raise SystemExit("No matching futures OHLCV files were found")

    catalog = catalog.sort_values(["timeframe", "pair"]).reset_index(drop=True)
    summary = {
        "schema_version": 3,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "start_inclusive": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "timeframes": args.timeframes,
        "minimum_coverage": args.minimum_coverage,
        "minimum_candles": args.minimum_candles,
        "source_sha256": True,
        "files": int(len(catalog)),
        "unique_pairs": int(catalog["pair"].nunique()),
        "research_ready_files": int(catalog["research_ready"].sum()),
        "status_counts": {
            str(key): int(value) for key, value in catalog["status"].value_counts().items()
        },
    }
    try:
        write_catalog_bundle(
            catalog,
            summary,
            args.output_dir,
            replace=args.replace,
        )
    except FileExistsError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
