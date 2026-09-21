from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freqtrade_research_lab.execution_runner import (  # noqa: E402
    ExecutionRunConfig,
    run_execution_benchmark,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a catalog-gated Freqtrade futures execution benchmark, "
            "summarize exported trades, and optionally run lookahead-analysis."
        )
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--strategy-path",
        type=Path,
        default=ROOT / "strategies",
    )
    parser.add_argument(
        "--strategy",
        default="BenchmarkMacdExecution",
    )
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--timeframe-detail")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--fee-per-side",
        type=float,
        default=0.0005,
        help="Fee ratio per side. 0.0005 = 5 bps entry + 5 bps exit.",
    )
    parser.add_argument(
        "--slippage-bps-round-trip",
        type=float,
        default=4.0,
        help="Additional round-trip slippage applied to exported trade returns.",
    )
    parser.add_argument("--stake-amount", type=float, default=1000.0)
    parser.add_argument("--minimum-trade-amount", type=int, default=20)
    parser.add_argument("--targeted-trade-amount", type=int, default=100)
    parser.add_argument("--max-pairs", type=int)
    parser.add_argument(
        "--pair-offset",
        type=int,
        default=0,
        help="Skip this many alphabetically sorted research-ready pairs before max-pairs.",
    )
    parser.add_argument(
        "--skip-lookahead",
        action="store_true",
        help="Skip Freqtrade lookahead-analysis for this run.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = ExecutionRunConfig(
        config_path=args.config,
        data_dir=args.data_dir,
        catalog_path=args.catalog,
        strategy_path=args.strategy_path,
        output_dir=args.output_dir,
        strategy_name=args.strategy,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        timeframe_detail=args.timeframe_detail,
        fee_per_side=args.fee_per_side,
        slippage_bps_round_trip=args.slippage_bps_round_trip,
        stake_amount=args.stake_amount,
        run_lookahead=not args.skip_lookahead,
        minimum_trade_amount=args.minimum_trade_amount,
        targeted_trade_amount=args.targeted_trade_amount,
        max_pairs=args.max_pairs,
        pair_offset=args.pair_offset,
    )
    manifest = run_execution_benchmark(config)
    print(
        f"Execution benchmark complete: {manifest['pair_count']} pairs, "
        f"{manifest['timeframe']} timeframe"
    )
    print(f"Results written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
