from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from coin_strategy_lab.universe import fetch_usdt_perpetuals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", type=Path, default=Path("artifacts/FUTURES_UNIVERSE.csv"))
    parser.add_argument("--json", type=Path, default=Path("artifacts/FUTURES_UNIVERSE.json"))
    args = parser.parse_args()

    symbols = fetch_usdt_perpetuals()
    args.csv.parent.mkdir(parents=True, exist_ok=True)
    args.json.parent.mkdir(parents=True, exist_ok=True)

    with args.csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["symbol","base_asset","quote_asset","contract_type","status","onboard_date"],
        )
        writer.writeheader()
        for item in symbols:
            writer.writerow(item.__dict__)

    args.json.write_text(
        json.dumps([item.__dict__ for item in symbols], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"eligible_usdt_perpetuals={len(symbols)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
