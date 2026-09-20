from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from freqtrade_research_lab.comparison import (  # noqa: E402
    apply_global_fdr,
    build_system_coverage,
    combine_discovery_frames,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine full train+validation discovery tables across systems/timeframes, "
            "then apply one global false-discovery correction without holdout."
        )
    )
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--global-fdr", type=float, default=0.10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frames = [pd.read_csv(path) for path in args.inputs]
    combined = combine_discovery_frames(frames)
    corrected = apply_global_fdr(combined, global_fdr=args.global_fdr)
    candidates = corrected.loc[corrected["global_discovery_pass"]].copy()
    coverage = build_system_coverage(corrected)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    corrected.to_csv(args.output_dir / "combined_discovery_tests.csv", index=False)
    candidates.to_csv(
        args.output_dir / "global_candidates_train_validation.csv", index=False
    )
    coverage.to_csv(args.output_dir / "system_coverage.csv", index=False)

    manifest = {
        "schema_version": 2,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "cross_system_discovery_only",
        "holdout_policy": "Holdout columns are rejected fail-closed.",
        "fdr_scope": "all_experiments_pair_direction_horizon",
        "global_fdr": args.global_fdr,
        "inputs": [str(path) for path in args.inputs],
        "hypotheses_tested": int(len(corrected)),
        "global_candidate_rows": int(len(candidates)),
        "unique_candidate_pairs": (
            int(candidates["pair"].nunique()) if not candidates.empty else 0
        ),
        "experiments": (
            int(corrected["experiment_id"].nunique()) if not corrected.empty else 0
        ),
    }
    (args.output_dir / "comparison_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
