from __future__ import annotations

import argparse
import calendar
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import one_shot_yesterday_96 as base


def replay_period(plan_path: Path, metrics_path: Path, year: int, month: int, out: Path) -> dict:
    plan = pd.read_csv(plan_path)
    metrics = pd.read_csv(metrics_path)

    original_request_bytes = base.request_bytes

    @lru_cache(maxsize=None)
    def cached_request_bytes(url: str, attempts: int = 4):
        return original_request_bytes(url, attempts)

    base.request_bytes = cached_request_bytes

    def replay_day(date_str: str) -> dict:
        local_start = pd.Timestamp(date_str, tz="Europe/Istanbul")
        day_start = local_start.tz_convert("UTC")
        day_end = (local_start + pd.Timedelta(days=1)).tz_convert("UTC")

        jobs = {}
        native = {}
        detail = {}
        with ThreadPoolExecutor(max_workers=16) as pool:
            for r in plan.itertuples():
                tf = str(r.timeframe)
                sid = str(r.strategy_id)
                warm = pd.Timedelta(seconds=base.TF_SECONDS[tf] * base.WARMUP.get(sid, 190))
                start = day_start - warm
                fut = pool.submit(base.download, str(r.symbol), tf, start, day_end)
                jobs[fut] = ("native", str(r.symbol), tf)
                if tf == "15m":
                    fut2 = pool.submit(
                        base.download,
                        str(r.symbol),
                        "5m",
                        day_start - pd.Timedelta(minutes=10),
                        day_end,
                    )
                    jobs[fut2] = ("detail", str(r.symbol), "5m")

            for i, fut in enumerate(as_completed(jobs), 1):
                kind, sym, tf = jobs[fut]
                x = fut.result()
                if kind == "native":
                    native[sym] = x
                else:
                    detail[sym] = x
                if i % 25 == 0 or i == len(jobs):
                    print(
                        f"[{date_str}] downloads {i}/{len(jobs)} cache={cached_request_bytes.cache_info()}",
                        flush=True,
                    )

        all_trades = []
        errors = []
        for r in plan.itertuples():
            sym = str(r.symbol)
            x = native.get(sym, pd.DataFrame())
            if x.empty:
                errors.append(f"{sym}:{r.timeframe}:NO_DATA")
                continue
            try:
                all_trades.extend(
                    base.simulate_symbol(r, x, detail.get(sym), day_start, day_end)
                )
            except Exception as exc:
                errors.append(f"{sym}:{r.timeframe}:{type(exc).__name__}:{exc}")

        all_trades = [
            t for t in all_trades if day_start <= t["entry_time"] < day_end
        ]

        reports = [base.portfolio(all_trades, lev) for lev in (1.0, 2.0, 3.0)]
        full_reports = [base.full_participation(all_trades, lev) for lev in (1.0, 2.0, 3.0)]
        quality_reports = [
            base.quality_filtered_full_participation(all_trades, metrics, lev)
            for lev in (1.0, 2.0, 3.0)
        ]

        return {
            "date_local": date_str,
            "candidate_trades": len(all_trades),
            "symbols_with_candidate_trades": len(set(t["symbol"] for t in all_trades)),
            "reports": [{k: v for k, v in r.items() if k != "accepted"} for r in reports],
            "full_participation_reports": full_reports,
            "quality_filtered_reports": quality_reports,
            "errors": errors,
        }

    days = calendar.monthrange(year, month)[1]
    daily = []
    day_dir = out / "days"
    day_dir.mkdir(parents=True, exist_ok=True)

    for day in range(1, days + 1):
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        summary = replay_day(date_str)
        daily.append(summary)
        (day_dir / f"{date_str}.json").write_text(
            json.dumps(summary, indent=2, default=str),
            encoding="utf-8",
        )
        p1 = summary["reports"][0]
        print(
            f"DAY_RESULT {date_str} pnl={p1['pnl']:.9f} trades={p1['accepted_trades']} "
            f"wins={p1['wins']} losses={p1['losses']} max_open={p1['max_open_positions']}",
            flush=True,
        )

    rows = []
    for x in daily:
        def bylev(key: str, lev: int):
            for r in x.get(key, []):
                if float(r.get("leverage", 0)) == float(lev):
                    return r
            return {}

        row = {
            "date": x["date_local"],
            "candidate_trades": x["candidate_trades"],
            "symbols": x["symbols_with_candidate_trades"],
            "errors": len(x.get("errors", [])),
        }
        for key, prefix in (
            ("reports", "portfolio"),
            ("full_participation_reports", "full"),
            ("quality_filtered_reports", "quality"),
        ):
            for lev in (1, 2, 3):
                r = bylev(key, lev)
                row[f"{prefix}_{lev}x_pnl"] = r.get("pnl")
                row[f"{prefix}_{lev}x_return_pct"] = r.get("return_pct")
                row[f"{prefix}_{lev}x_trades"] = r.get("accepted_trades")
                row[f"{prefix}_{lev}x_wins"] = r.get("wins")
                row[f"{prefix}_{lev}x_losses"] = r.get("losses")
                row[f"{prefix}_{lev}x_max_open"] = r.get(
                    "max_open_positions", r.get("peak_concurrent_positions")
                )
        rows.append(row)

    fields = sorted({k for r in rows for k in r}, key=lambda k: (k != "date", k))
    with (out / "DAILY.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    p1 = [float(r["portfolio_1x_pnl"]) for r in rows]
    report = {
        "year": year,
        "month": month,
        "period": f"{year:04d}-{month:02d}-01..{year:04d}-{month:02d}-{days:02d}",
        "days": len(rows),
        "method": "independent daily fixed-10k portfolio tests; monthly PnL is sum of daily PnL, no compounding",
        "lookahead_note": "retrospective robustness only: frozen 96 PASS plan was selected later, so this is not true forward/out-of-sample evidence",
        "frozen_source_plan_run_id": 36125698776,
        "source_strategy_commit": "b620587b955bf05c9f8e028bba563fb97ab5706b",
        "portfolio_rule": {
            "start_usdt_per_day": 10000.0,
            "margin_per_trade_usdt": 500.0,
            "max_positions": 20,
            "same_symbol_max": 1,
            "leverage": 1.0,
        },
        "exit_rule": {
            "5m_15m": "activate +1.5%, trail 0.75%, opposite fallback",
            "1h_4h_1d": "opposite-signal runner",
            "roundtrip_cost_bps": 15.0,
        },
        "portfolio_1x": {
            "sum_pnl_fixed_10k": sum(p1),
            "avg_daily_pnl": sum(p1) / len(p1),
            "positive_days": sum(v > 0 for v in p1),
            "negative_days": sum(v < 0 for v in p1),
            "flat_days": sum(abs(v) < 1e-12 for v in p1),
            "best_day": max(
                ((r["date"], float(r["portfolio_1x_pnl"])) for r in rows),
                key=lambda z: z[1],
            ),
            "worst_day": min(
                ((r["date"], float(r["portfolio_1x_pnl"])) for r in rows),
                key=lambda z: z[1],
            ),
            "trades": sum(int(r["portfolio_1x_trades"]) for r in rows),
            "wins": sum(int(r["portfolio_1x_wins"]) for r in rows),
            "losses": sum(int(r["portfolio_1x_losses"]) for r in rows),
        },
    }
    (out / "MONTH_SUMMARY.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    print("MONTH_RESULT")
    print(json.dumps(report, indent=2))
    return report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--metrics", type=Path, required=True)
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    replay_period(args.plan, args.metrics, args.year, args.month, args.out)


if __name__ == "__main__":
    main()
