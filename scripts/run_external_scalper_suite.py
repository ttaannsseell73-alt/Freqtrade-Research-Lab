from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freqtrade_research_lab.external_freqtrade_suite import (  # noqa: E402
    ExternalSuiteConfig,
    run_external_suite,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download immutable third-party Freqtrade scalp strategies, verify "
            "their Git blob hashes, run native-timeframe benchmarks, and write "
            "one consolidated report."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--catalog-1m", type=Path, required=True)
    parser.add_argument("--catalog-5m", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reuse-root", type=Path)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--fee-per-side", type=float, default=0.0005)
    parser.add_argument("--slippage-bps-round-trip", type=float, default=4.0)
    parser.add_argument("--stake-amount", type=float, default=1000.0)
    parser.add_argument("--max-pairs", type=int, default=5)
    parser.add_argument("--pair-offset", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = run_external_suite(
        ExternalSuiteConfig(
            config_path=args.config,
            data_dir=args.data_dir,
            catalog_1m=args.catalog_1m,
            catalog_5m=args.catalog_5m,
            output_dir=args.output_dir,
            start=args.start,
            end=args.end,
            reuse_root=args.reuse_root,
            fee_per_side=args.fee_per_side,
            slippage_bps_round_trip=args.slippage_bps_round_trip,
            stake_amount=args.stake_amount,
            max_pairs=args.max_pairs,
            pair_offset=args.pair_offset,
        )
    )
    print(
        f"External scalper suite complete: {manifest['completed_runs']} runs, "
        f"{len(manifest['errors'])} errors"
    )
    print(f"Report: {args.output_dir / 'EXTERNAL_SCALPER_REPORT.md'}")
    return 1 if manifest["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
