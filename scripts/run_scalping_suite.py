from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freqtrade_research_lab.scalping_suite import (  # noqa: E402
    ScalpingSuiteConfig,
    run_scalping_suite,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run or reuse the locked 1m/5m scalping smoke benchmarks and write "
            "one consolidated CSV + Markdown report."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--catalog-1m", type=Path, required=True)
    parser.add_argument("--catalog-5m", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--reuse-root",
        type=Path,
        help="Search this directory for exact reusable benchmark runs.",
    )
    parser.add_argument(
        "--strategy-path",
        type=Path,
        default=ROOT / "strategies",
    )
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--fee-per-side", type=float, default=0.0005)
    parser.add_argument("--slippage-bps-round-trip", type=float, default=4.0)
    parser.add_argument("--stake-amount", type=float, default=1000.0)
    parser.add_argument("--max-pairs", type=int, default=5)
    parser.add_argument("--pair-offset", type=int, default=0)
    parser.add_argument(
        "--run-lookahead",
        action="store_true",
        help="Run lookahead-analysis for every newly executed suite member.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = run_scalping_suite(
        ScalpingSuiteConfig(
            config_path=args.config,
            data_dir=args.data_dir,
            catalog_1m=args.catalog_1m,
            catalog_5m=args.catalog_5m,
            strategy_path=args.strategy_path,
            output_dir=args.output_dir,
            start=args.start,
            end=args.end,
            reuse_root=args.reuse_root,
            fee_per_side=args.fee_per_side,
            slippage_bps_round_trip=args.slippage_bps_round_trip,
            stake_amount=args.stake_amount,
            max_pairs=args.max_pairs,
            pair_offset=args.pair_offset,
            run_lookahead=args.run_lookahead,
        )
    )

    print(
        f"Scalping suite complete: {manifest['completed_runs']} runs, "
        f"{len(manifest['errors'])} errors"
    )
    print(f"Report: {args.output_dir / 'SCALPING_SUITE_REPORT.md'}")
    return 1 if manifest["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
