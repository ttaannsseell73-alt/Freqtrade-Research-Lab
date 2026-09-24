from __future__ import annotations

import argparse
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from coin_strategy_lab import StrategyRegistry
from coin_strategy_lab.universe import fetch_usdt_perpetuals


KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"
VISION_BASE_URL = "https://data.binance.vision/data/futures/um"
INTERVAL_MS = 60 * 60 * 1000
COLS = [
    "open_time","open","high","low","close","volume","close_time","quote_volume",
    "trade_count","taker_buy_base","taker_buy_quote","ignore",
]


def _request_json(url: str, *, attempts: int = 5) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": "CoinStrategyLab/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in {418, 429}:
                retry = float(exc.headers.get("Retry-After", "3"))
                time.sleep(max(retry, 2.0) + attempt)
                continue
            raise
        except Exception:
            if attempt + 1 == attempts:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("request retry exhausted")



def _request_bytes(url: str, *, attempts: int = 4) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "CoinStrategyLab/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt + 1 == attempts:
                raise
        except Exception:
            if attempt + 1 == attempts:
                raise
        time.sleep(1.25 * (attempt + 1))
    return None


def _vision_zip_frame(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        if not names:
            return pd.DataFrame()
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return frame
    if not str(frame.iloc[0, 0]).lstrip("-").isdigit():
        frame = frame.iloc[1:].reset_index(drop=True)
    frame = frame.iloc[:, :12]
    frame.columns = COLS[: frame.shape[1]]
    for col in ["open_time","open","high","low","close","volume","quote_volume","trade_count"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open_time","open","high","low","close","volume"])
    unit = "us" if float(frame["open_time"].median()) > 100_000_000_000_000 else "ms"
    frame["time"] = pd.to_datetime(frame["open_time"].astype("int64"), unit=unit, utc=True)
    return frame[["time","open","high","low","close","volume","quote_volume","trade_count"]]


def _vision_archive_urls(symbol: str, start: date, end: date) -> list[str]:
    urls: list[str] = []
    qsymbol = urllib.parse.quote(symbol, safe="")
    month = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while month < end_month:
        ym = month.strftime("%Y-%m")
        filename = urllib.parse.quote(f"{symbol}-1h-{ym}.zip", safe="-_.")
        urls.append(f"{VISION_BASE_URL}/monthly/klines/{qsymbol}/1h/{filename}")
        month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
    day = end_month
    while day < end:
        ds = day.strftime("%Y-%m-%d")
        filename = urllib.parse.quote(f"{symbol}-1h-{ds}.zip", safe="-_.")
        urls.append(f"{VISION_BASE_URL}/daily/klines/{qsymbol}/1h/{filename}")
        day += timedelta(days=1)
    return urls


def download_klines_vision(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for url in _vision_archive_urls(symbol, start.date(), end.date()):
        payload = _request_bytes(url)
        if payload is None:
            continue
        frame = _vision_zip_frame(payload)
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["time","open","high","low","close","volume","quote_volume","trade_count"])
    out = pd.concat(frames, ignore_index=True).drop_duplicates("time").sort_values("time")
    out = out[(out["time"] >= start) & (out["time"] < end)]
    return out.reset_index(drop=True)


def load_snapshot(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload["symbols"])


def load_universe(snapshot: Path) -> tuple[list[dict], str]:
    try:
        live = fetch_usdt_perpetuals()
        if live:
            return [asdict(x) for x in live], "live_exchange_info"
    except Exception as exc:
        print(f"[universe] live scanner failed, using snapshot: {exc!r}", flush=True)
    return load_snapshot(snapshot), "snapshot_fallback"


def download_klines(symbol: str, start_ms: int, end_ms: int, request_pause: float) -> pd.DataFrame:
    rows: list[list] = []
    cursor = start_ms
    while cursor < end_ms:
        query = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "interval": "1h",
                "startTime": cursor,
                "endTime": end_ms - 1,
                "limit": 1500,
            }
        )
        payload = _request_json(f"{KLINES_URL}?{query}")
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        last_open = int(payload[-1][0])
        next_cursor = last_open + INTERVAL_MS
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        time.sleep(request_pause)
        if len(payload) < 1500:
            break

    if not rows:
        return pd.DataFrame(columns=["time","open","high","low","close","volume","quote_volume","trade_count"])

    frame = pd.DataFrame(rows, columns=COLS[: len(rows[0])])
    for col in ["open_time","open","high","low","close","volume","quote_volume","trade_count"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open_time","open","high","low","close","volume"])
    frame["time"] = pd.to_datetime(frame["open_time"].astype("int64"), unit="ms", utc=True)
    frame = frame.drop_duplicates("time").sort_values("time")
    frame = frame[(frame["open_time"] >= start_ms) & (frame["open_time"] < end_ms)]
    return frame[["time","open","high","low","close","volume","quote_volume","trade_count"]].reset_index(drop=True)


def build_reversal_trades(frame: pd.DataFrame, long_signal: pd.Series, short_signal: pd.Series) -> pd.DataFrame:
    rows: list[dict] = []
    position = 0
    entry_px = None
    entry_time = None

    for i in range(len(frame) - 1):
        long_now = bool(long_signal.iloc[i])
        short_now = bool(short_signal.iloc[i])
        if long_now == short_now:
            continue
        desired = 1 if long_now else -1
        next_open = float(frame["open"].iloc[i + 1])
        next_time = frame["time"].iloc[i + 1]

        if position == 0:
            position = desired
            entry_px = next_open
            entry_time = next_time
            continue
        if desired == position:
            continue

        gross_bps = (next_open / float(entry_px) - 1.0) * 10_000.0 * position
        rows.append(
            {
                "entry_time": entry_time,
                "exit_time": next_time,
                "direction": "LONG" if position == 1 else "SHORT",
                "entry_price": float(entry_px),
                "exit_price": next_open,
                "gross_bps": gross_bps,
                "holding_hours": (next_time - entry_time).total_seconds() / 3600.0,
            }
        )
        position = desired
        entry_px = next_open
        entry_time = next_time

    if position != 0 and entry_px is not None and len(frame):
        exit_px = float(frame["close"].iloc[-1])
        exit_time = frame["time"].iloc[-1]
        gross_bps = (exit_px / float(entry_px) - 1.0) * 10_000.0 * position
        rows.append(
            {
                "entry_time": entry_time,
                "exit_time": exit_time,
                "direction": "LONG" if position == 1 else "SHORT",
                "entry_price": float(entry_px),
                "exit_price": exit_px,
                "gross_bps": gross_bps,
                "holding_hours": (exit_time - entry_time).total_seconds() / 3600.0,
            }
        )
    return pd.DataFrame(rows)


def metrics(trades: pd.DataFrame, cost_bps: float) -> dict:
    if trades.empty:
        return {
            "trades": 0,
            "expectancy_bps": math.nan,
            "median_bps": math.nan,
            "net_sum_bps": 0.0,
            "win_rate": math.nan,
            "profit_factor": math.nan,
            "max_drawdown_bps": math.nan,
            "avg_holding_hours": math.nan,
            "long_trades": 0,
            "short_trades": 0,
        }
    net = trades["gross_bps"].to_numpy(float) - cost_bps
    wins = net[net > 0]
    losses = net[net < 0]
    pf = (
        float(wins.sum() / abs(losses.sum()))
        if len(losses) and abs(losses.sum()) > 1e-12
        else (math.inf if len(wins) else math.nan)
    )
    curve = np.cumsum(net)
    peaks = np.maximum.accumulate(np.r_[0.0, curve])[1:]
    dd = peaks - curve
    return {
        "trades": int(len(net)),
        "expectancy_bps": float(net.mean()),
        "median_bps": float(np.median(net)),
        "net_sum_bps": float(net.sum()),
        "win_rate": float((net > 0).mean()),
        "profit_factor": pf,
        "max_drawdown_bps": float(dd.max()) if len(dd) else 0.0,
        "avg_holding_hours": float(trades["holding_hours"].mean()),
        "long_trades": int((trades["direction"] == "LONG").sum()),
        "short_trades": int((trades["direction"] == "SHORT").sum()),
    }


def split_trades(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, split: str) -> pd.DataFrame:
    t1 = start + (end - start) * 0.50
    t2 = start + (end - start) * 0.75
    if trades.empty or split == "full":
        return trades
    if split == "train":
        return trades[trades["entry_time"] < t1]
    if split == "validation":
        return trades[(trades["entry_time"] >= t1) & (trades["entry_time"] < t2)]
    if split == "holdout":
        return trades[trades["entry_time"] >= t2]
    raise ValueError(split)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-06-24")
    parser.add_argument("--end", default="2026-09-24")
    parser.add_argument("--snapshot", type=Path, default=Path("config/futures_universe_snapshot.json"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--request-pause", type=float, default=0.34)
    parser.add_argument("--data-source", choices=["api","vision"], default="vision")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--max-symbols", type=int, default=0)
    args = parser.parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    expected_candles = int((end - start).total_seconds() // 3600)

    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)

    universe, universe_source = load_universe(args.snapshot)
    universe = sorted(universe, key=lambda x: x["symbol"])
    if args.symbols:
        wanted = {item.strip().upper() for item in args.symbols.split(",") if item.strip()}
        universe = [item for item in universe if item["symbol"] in wanted]
    if args.max_symbols > 0:
        universe = universe[: args.max_symbols]

    registry = StrategyRegistry.discover_builtins()
    strategy_ids = registry.ids()
    print(f"[run] universe={len(universe)} strategies={strategy_ids}", flush=True)

    audit_rows: list[dict] = []
    detailed_rows: list[dict] = []
    trade_rows: list[pd.DataFrame] = []
    errors: list[dict] = []

    downloaded: dict[str, tuple[pd.DataFrame | None, Exception | None]] = {}
    if args.data_source == "vision" and args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            pending = {
                pool.submit(download_klines_vision, meta["symbol"], start, end): meta["symbol"]
                for meta in universe
            }
            for done_idx, future in enumerate(as_completed(pending), 1):
                symbol = pending[future]
                try:
                    downloaded[symbol] = (future.result(), None)
                except Exception as exc:
                    downloaded[symbol] = (None, exc)
                print(f"[download {done_idx}/{len(universe)}] {symbol}", flush=True)

    for idx, meta in enumerate(universe, 1):
        symbol = meta["symbol"]
        print(f"[eval {idx}/{len(universe)}] {symbol}", flush=True)
        try:
            if args.data_source == "vision":
                if downloaded:
                    frame, download_error = downloaded.get(symbol, (None, RuntimeError("missing download result")))
                    if download_error is not None:
                        raise download_error
                    assert frame is not None
                else:
                    frame = download_klines_vision(symbol, start, end)
            else:
                frame = download_klines(symbol, start_ms, end_ms, args.request_pause)
        except Exception as exc:
            errors.append({"symbol": symbol, "stage": "download", "error": repr(exc)})
            audit_rows.append(
                {
                    "symbol": symbol,
                    "candles": 0,
                    "expected_candles": expected_candles,
                    "coverage": 0.0,
                    "status": "DOWNLOAD_ERROR",
                    "onboard_date": meta.get("onboard_date") or meta.get("onboardDate"),
                }
            )
            continue

        coverage = len(frame) / expected_candles if expected_candles else 0.0
        status = "READY" if coverage >= 0.95 else "INSUFFICIENT_HISTORY"
        audit_rows.append(
            {
                "symbol": symbol,
                "candles": len(frame),
                "expected_candles": expected_candles,
                "coverage": coverage,
                "status": status,
                "onboard_date": meta.get("onboard_date") or meta.get("onboardDate"),
                "first": frame["time"].iloc[0].isoformat() if len(frame) else None,
                "last": frame["time"].iloc[-1].isoformat() if len(frame) else None,
                "median_hourly_quote_volume": float(frame["quote_volume"].median()) if len(frame) else 0.0,
            }
        )
        if status != "READY":
            continue

        for strategy_id in strategy_ids:
            strategy = registry.get(strategy_id)
            try:
                prepared = strategy.prepare(frame)
                long_signal = strategy.long_entries(prepared).fillna(False).astype(bool)
                short_signal = strategy.short_entries(prepared).fillna(False).astype(bool)
                trades = build_reversal_trades(frame, long_signal, short_signal)
                if not trades.empty:
                    tcopy = trades.copy()
                    tcopy["symbol"] = symbol
                    tcopy["strategy_id"] = strategy_id
                    trade_rows.append(tcopy)

                for split in ("full","train","validation","holdout"):
                    split_df = split_trades(trades, start, end, split)
                    for cost in (6.0, 10.0, 15.0):
                        detailed_rows.append(
                            {
                                "symbol": symbol,
                                "timeframe": "1h",
                                "strategy_id": strategy_id,
                                "strategy_version": strategy.spec.version,
                                "split": split,
                                "cost_bps": cost,
                                **metrics(split_df, cost),
                            }
                        )
            except Exception as exc:
                errors.append({"symbol": symbol, "strategy_id": strategy_id, "stage": "strategy", "error": repr(exc)})

    audit = pd.DataFrame(audit_rows)
    details = pd.DataFrame(detailed_rows)
    trades_all = pd.concat(trade_rows, ignore_index=True) if trade_rows else pd.DataFrame()

    audit.to_csv(out / "FUTURES_UNIVERSE.csv", index=False)
    details.to_csv(out / "DETAILED_RESULTS.csv", index=False)
    trades_all.to_csv(out / "TRADES.csv", index=False)
    (out / "ERRORS.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")

    if details.empty:
        summary = {
            "status": "NO_EVALUABLE_DATA",
            "period": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
            "timeframe": "1h",
            "universe_source": universe_source,
            "universe_count": len(universe),
            "ready_symbols": int((audit.get("status") == "READY").sum()) if not audit.empty else 0,
            "insufficient_history": int((audit.get("status") == "INSUFFICIENT_HISTORY").sum()) if not audit.empty else 0,
            "download_errors": int((audit.get("status") == "DOWNLOAD_ERROR").sum()) if not audit.empty else 0,
            "strategy_count": len(strategy_ids),
            "strategies": list(strategy_ids),
            "data_source": args.data_source,
        }
        (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
        return 0

    base = details[details["cost_bps"] == 10.0].copy()
    piv = base.pivot_table(
        index=["symbol","strategy_id","strategy_version"],
        columns="split",
        values=["trades","expectancy_bps","profit_factor","net_sum_bps","win_rate","max_drawdown_bps","avg_holding_hours"],
        aggfunc="first",
    )
    piv.columns = [f"{metric}_{split}" for metric, split in piv.columns]
    piv = piv.reset_index()

    stress = details[(details["split"] == "full") & (details["cost_bps"].isin([6.0,15.0]))]
    stress = stress.pivot_table(
        index=["symbol","strategy_id"],
        columns="cost_bps",
        values="expectancy_bps",
        aggfunc="first",
    ).reset_index().rename(columns={6.0:"expectancy_full_6bps",15.0:"expectancy_full_15bps"})
    master = piv.merge(stress, on=["symbol","strategy_id"], how="left")

    master["candidate"] = (
        (master["trades_full"] >= 20)
        & (master["trades_validation"] >= 5)
        & (master["trades_holdout"] >= 5)
        & (master["expectancy_bps_train"] > 0)
        & (master["expectancy_bps_validation"] > 0)
        & (master["expectancy_bps_holdout"] > 0)
        & (master["profit_factor_validation"] > 1.0)
        & (master["profit_factor_holdout"] > 1.0)
        & (master["expectancy_full_15bps"] > 0)
    )
    master["oos_floor_bps"] = master[["expectancy_bps_validation","expectancy_bps_holdout"]].min(axis=1)
    master = master.sort_values(
        ["candidate","oos_floor_bps","profit_factor_full","expectancy_bps_full"],
        ascending=False,
    )
    master.to_csv(out / "MASTER_COIN_STRATEGY_MATRIX.csv", index=False)

    best_rows = []
    for symbol, group in master.groupby("symbol", sort=True):
        candidates = group[group["candidate"]]
        pool = candidates if not candidates.empty else group
        row = pool.iloc[0].copy()
        row["selection_status"] = "CANDIDATE" if bool(row["candidate"]) else "NO_QUALIFIED_STRATEGY"
        best_rows.append(row)
    best = pd.DataFrame(best_rows)
    best.to_csv(out / "BEST_STRATEGY_BY_COIN.csv", index=False)

    strategy_rank = (
        master[master["candidate"]]
        .sort_values(["strategy_id","oos_floor_bps"], ascending=[True,False])
    )
    strategy_rank.to_csv(out / "BEST_COINS_BY_STRATEGY.csv", index=False)

    candidate_rows = master[master["candidate"]].copy()
    candidate_rows.to_csv(out / "CANDIDATES.csv", index=False)

    summary = {
        "status": "COMPLETE",
        "period": {"start": start.isoformat(), "end_exclusive": end.isoformat()},
        "timeframe": "1h",
        "universe_source": universe_source,
        "universe_count": len(universe),
        "ready_symbols": int((audit.get("status") == "READY").sum()) if not audit.empty else 0,
        "insufficient_history": int((audit.get("status") == "INSUFFICIENT_HISTORY").sum()) if not audit.empty else 0,
        "download_errors": int((audit.get("status") == "DOWNLOAD_ERROR").sum()) if not audit.empty else 0,
        "strategy_count": len(strategy_ids),
        "strategies": list(strategy_ids),
        "evaluated_pairs": int(len(master)),
        "candidate_pairs": int(master["candidate"].sum()) if not master.empty else 0,
        "coins_with_candidate": int(master[master["candidate"]]["symbol"].nunique()) if not master.empty else 0,
        "costs_bps_round_trip": [6,10,15],
        "split": "50/25/25 chronological",
        "execution": "closed-bar signal, next 1h open, reverse on opposite signal",
        "data_source": args.data_source,
        "note": "Discovery classification only; candidates require longer-horizon and native execution validation.",
    }
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# CoinStrategyLab — 1H / 90d Discovery\n\n",
        f"- Universe: {summary['universe_count']} symbols ({summary['ready_symbols']} ready)\n",
        f"- Strategies: {summary['strategy_count']}\n",
        f"- Candidate pairs: {summary['candidate_pairs']}\n",
        f"- Coins with candidate: {summary['coins_with_candidate']}\n\n",
        "## Top candidate mappings\n\n",
        "| Coin | Strategy | OOS floor bps | Full expectancy | PF | Trades | 15bps expectancy |\n",
        "|---|---|---:|---:|---:|---:|---:|\n",
    ]
    for row in candidate_rows.head(100).itertuples():
        lines.append(
            f"| {row.symbol} | {row.strategy_id} | {row.oos_floor_bps:.2f} | "
            f"{row.expectancy_bps_full:.2f} | {row.profit_factor_full:.2f} | "
            f"{int(row.trades_full)} | {row.expectancy_full_15bps:.2f} |\n"
        )
    (out / "REPORT.md").write_text("".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
