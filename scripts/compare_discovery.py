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
    build_system_coverage,
    combine_discovery_frames,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Combine train+validation candidate tables across systems/timeframes "
            "without exposing holdout metrics."
        )
    )
    parser.add_argument("--inputs", nargs="+", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frames = [pd.read_csv(path) for path in args.inputs]
    combined = combine_discovery_frames(frames)
    coverage = build_system_coverage(combined)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.output_dir / "combined_candidates.csv", index=False)
    coverage.to_csv(args.output_dir / "system_coverage.csv", index=False)

    manifest = {
        "schema_version": 1,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "stage": "discovery_only",
        "holdout_policy": "Holdout columns are rejected fail-closed.",
        "inputs": [str(path) for path in args.inputs],
        "candidate_rows": int(len(combined)),
        "unique_pairs": int(combined["pair"].nunique()) if not combined.empty else 0,
        "experiments": int(combined["experiment_id"].nunique()) if not combined.empty else 0,
    }
    (args.output_dir / "comparison_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
