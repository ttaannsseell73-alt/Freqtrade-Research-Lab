from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freqtrade_research_lab.dataset import (  # noqa: E402
    discover_market_files,
    load_ohlcv,
    load_ready_pairs_from_catalog,
)
from freqtrade_research_lab.event_study import (  # noqa: E402
    EventStudyConfig,
    analyze_pair,
    build_candidate_tables,
    split_boundaries,
)
from freqtrade_research_lab.experiment import ExperimentSpec, tag_result_frame  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a no-lookahead MACD crossover event study over Freqtrade futures data."
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", required=True, help="Inclusive ISO date, e.g. 2025-09-19")
    parser.add_argument("--end", required=True, help="Exclusive ISO date, e.g. 2026-09-20")
    parser.add_argument("--timeframe", default="1m")
    parser.add_argument(
        "--catalog",
        type=Path,
        help="Optional dataset_catalog.csv; when provided only research_ready pairs are scanned.",
    )
    parser.add_argument("--cost-bps", type=float, default=14.0)
    parser.add_argument("--max-files", type=int)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    train_end, validation_end = split_boundaries(start.to_pydatetime(), end.to_pydatetime())
    config = EventStudyConfig(round_trip_cost_bps=args.cost_bps)
    experiment = ExperimentSpec(
        system_id="macd_crossover",
        system_version="1",
        timeframe=args.timeframe,
        parameters=asdict(config),
        cost_bps=args.cost_bps,
        horizons=config.horizons,
    )
    market_files = discover_market_files(args.data_dir, args.timeframe)
    pairs_before_catalog = len(market_files)
    if args.catalog is not None:
        ready_pairs = load_ready_pairs_from_catalog(args.catalog, args.timeframe)
        market_files = [item for item in market_files if item.pair in ready_pairs]
        if not market_files:
            raise SystemExit(
                f"Catalog {args.catalog} selected no research-ready {args.timeframe} pairs"
            )
        print(
            f"Catalog gate selected {len(market_files)}/{pairs_before_catalog} "
            f"{args.timeframe} pairs",
            flush=True,
        )
    if args.max_files is not None:
        market_files = market_files[: args.max_files]
    if not market_files:
        raise SystemExit(f"No {args.timeframe} futures files found in {args.data_dir}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []
    coverage: list[dict[str, object]] = []
    started = time.monotonic()

    for index, item in enumerate(market_files, start=1):
        try:
            frame = load_ohlcv(item)
            frame = frame.loc[(frame["date"] >= start) & (frame["date"] < end)].copy()
            if frame.empty:
                raise ValueError("No candles inside requested timerange")
            coverage.append(
                {
                    "pair": item.pair,
                    "file": item.path.name,
                    "candles": len(frame),
                    "first_candle": frame["date"].iloc[0].isoformat(),
                    "last_candle": frame["date"].iloc[-1].isoformat(),
                    "size_bytes": item.path.stat().st_size,
                }
            )
            summaries.append(analyze_pair(frame, item.pair, config, train_end, validation_end))
        except Exception as exc:  # One bad/newly-listed pair must not abort the universe run.
            errors.append({"pair": item.pair, "file": item.path.name, "error": str(exc)})

        if index % 10 == 0 or index == len(market_files):
            elapsed = time.monotonic() - started
            print(f"Processed {index}/{len(market_files)} pairs in {elapsed:.1f}s", flush=True)

    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    candidates, holdout = build_candidate_tables(summary, config)
    discovery_summary = summary.loc[summary["period"].isin(["train", "validation"])].copy()
    discovery_summary = tag_result_frame(discovery_summary, experiment)
    candidates = tag_result_frame(candidates, experiment)
    holdout = tag_result_frame(holdout, experiment)
    discovery_summary.to_csv(args.output_dir / "summary_discovery.csv", index=False)
    candidates.to_csv(args.output_dir / "candidates_train_validation.csv", index=False)
    holdout.to_csv(args.output_dir / "holdout_report.csv", index=False)
    pd.DataFrame(coverage).to_csv(args.output_dir / "dataset_coverage.csv", index=False)
    pd.DataFrame(errors, columns=["pair", "file", "error"]).to_csv(
        args.output_dir / "errors.csv", index=False
    )

    result_summary = {
        "schema_version": 1,
        "study": "macd_crossover_event_study",
        "experiment_id": experiment.experiment_id(),
        "system_id": experiment.system_id,
        "timeframe": args.timeframe,
        "pairs_analyzed": len(coverage),
        "pairs_failed": len(errors),
        "candidate_rows": int(len(candidates)),
        "holdout_rows": int(len(holdout)),
        "holdout_passed": (
            int(holdout["holdout_pass"].fillna(False).astype(bool).sum())
            if "holdout_pass" in holdout.columns
            else 0
        ),
        "status": "candidates_found" if len(candidates) else "no_candidates",
    }
    (args.output_dir / "result_summary.json").write_text(
        json.dumps(result_summary, indent=2), encoding="utf-8"
    )

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "study": "macd_crossover_event_study",
        "experiment": experiment.manifest(),
        "entry_rule": "Signal on closed candle; entry at next candle open",
        "start_inclusive": start.isoformat(),
        "end_exclusive": end.isoformat(),
        "train_end": train_end.isoformat(),
        "validation_end": validation_end.isoformat(),
        "timeframe": args.timeframe,
        "catalog": str(args.catalog) if args.catalog is not None else None,
        "pairs_before_catalog_gate": pairs_before_catalog,
        "pairs_discovered": len(market_files),
        "pairs_analyzed": len(coverage),
        "pairs_failed": len(errors),
        "config": asdict(config),
        "holdout_policy": (
            "Candidate selection uses train+validation only. Full-universe holdout rows "
            "are not written; holdout_report.csv contains holdout metrics only for the "
            "already-frozen candidate set."
        ),
        "limitations": [
            "Current active-contract universe can contain survivorship bias.",
            "Funding is not included in the event study; execution backtests add it later.",
            "Cost model is a fixed round-trip fee plus slippage assumption.",
            "Holdout results must not be used to retune the same model.",
        ],
    }
    manifest_path = args.output_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (args.output_dir / "SHA256SUMS.txt").write_text(
        "\n".join(
            f"{sha256(path)}  {path.name}"
            for path in sorted(args.output_dir.iterdir())
            if path.is_file() and path.name != "SHA256SUMS.txt"
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Results written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

