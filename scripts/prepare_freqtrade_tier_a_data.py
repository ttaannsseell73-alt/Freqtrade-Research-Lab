from __future__ import annotations

import argparse
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import pandas as pd

from freqtrade.data.history.datahandlers import get_datahandler
from freqtrade.enums import CandleType


VISION_BASE_URL = "https://data.binance.vision/data/futures/um"
TIMEFRAME_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}
COLS = [
    "open_time","open","high","low","close","volume","close_time","quote_volume",
    "trade_count","taker_buy_base","taker_buy_quote","ignore",
]


def _request_bytes(url: str, attempts: int = 4) -> bytes | None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CoinStrategyLab-Freqtrade/1.0"},
    )
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt + 1 == attempts:
                raise
        except Exception:
            if attempt + 1 == attempts:
                raise
        import time
        time.sleep(1.25 * (attempt + 1))
    return None


def _archive_urls(symbol: str, timeframe: str, start: date, end: date) -> list[str]:
    urls: list[str] = []
    qsymbol = urllib.parse.quote(symbol, safe="")
    month = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)

    while month < end_month:
        ym = month.strftime("%Y-%m")
        filename = urllib.parse.quote(
            f"{symbol}-{timeframe}-{ym}.zip",
            safe="-_.",
        )
        urls.append(
            f"{VISION_BASE_URL}/monthly/klines/{qsymbol}/{timeframe}/{filename}"
        )
        if month.month == 12:
            month = date(month.year + 1, 1, 1)
        else:
            month = date(month.year, month.month + 1, 1)

    day = end_month
    while day < end:
        ds = day.strftime("%Y-%m-%d")
        filename = urllib.parse.quote(
            f"{symbol}-{timeframe}-{ds}.zip",
            safe="-_.",
        )
        urls.append(
            f"{VISION_BASE_URL}/daily/klines/{qsymbol}/{timeframe}/{filename}"
        )
        day += timedelta(days=1)
    return urls


def _read_zip(payload: bytes) -> pd.DataFrame:
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
    for col in ["open_time","open","high","low","close","volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open_time","open","high","low","close","volume"])
    unit = "us" if float(frame["open_time"].median()) > 100_000_000_000_000 else "ms"
    frame["date"] = pd.to_datetime(
        frame["open_time"].astype("int64"),
        unit=unit,
        utc=True,
    )
    return frame[["date","open","high","low","close","volume"]]


def download_vision(
    symbol: str,
    timeframe: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for url in _archive_urls(symbol, timeframe, start.date(), end.date()):
        payload = _request_bytes(url)
        if payload is None:
            continue
        frame = _read_zip(payload)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame(columns=["date","open","high","low","close","volume"])

    out = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("date")
        .sort_values("date")
    )
    out = out[(out["date"] >= start) & (out["date"] < end)]
    return out.reset_index(drop=True)


def _pair_start(row: pd.Series) -> pd.Timestamp:
    tf = str(row["timeframe"])
    start = pd.Timestamp(str(row["validation_start"]), tz="UTC")
    warmup = pd.Timedelta(seconds=TIMEFRAME_SECONDS[tf] * 190)
    return start - warmup


def synthetic_zero_funding_aux(
    frame: pd.DataFrame,
    validation_start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build 1h mark/funding placeholders for zero-funding execution stress.

    Mark prices are forward-filled from the already downloaded Binance Vision
    futures candles. Since funding_rate is exactly zero, mark values cannot
    change PnL; they only satisfy Freqtrade's futures-data contract.
    """
    base = frame[
        (frame["date"] >= validation_start)
        & (frame["date"] < end)
    ][["date", "close"]].copy()
    if base.empty:
        return (
            pd.DataFrame(columns=["date","open","high","low","close","volume"]),
            pd.DataFrame(columns=["date","funding_rate"]),
        )

    hourly = (
        base.set_index("date")["close"]
        .resample("1h")
        .ffill()
        .dropna()
        .rename("close")
        .reset_index()
    )
    mark = pd.DataFrame(
        {
            "date": hourly["date"],
            "open": hourly["close"].astype(float),
            "high": hourly["close"].astype(float),
            "low": hourly["close"].astype(float),
            "close": hourly["close"].astype(float),
            "volume": 0.0,
        }
    )
    funding = pd.DataFrame(
        {
            "date": hourly["date"],
            "funding_rate": 0.0,
        }
    )
    return mark, funding


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--minimum-coverage", type=float, default=0.95)
    args = parser.parse_args()

    plan = pd.read_csv(args.plan)
    required = {
        "symbol","freqtrade_pair","timeframe",
        "validation_start","validation_end",
    }
    missing = required - set(plan.columns)
    if missing:
        raise ValueError(f"Execution plan missing columns: {sorted(missing)}")

    unique = (
        plan.sort_values("confidence_score", ascending=False)
        .drop_duplicates(["symbol","timeframe"])
        .reset_index(drop=True)
    )

    args.data_dir.mkdir(parents=True, exist_ok=True)
    handler = get_datahandler(args.data_dir, data_format="feather")

    audit: list[dict] = []
    jobs = {}
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for _, row in unique.iterrows():
            symbol = str(row["symbol"])
            timeframe = str(row["timeframe"])
            start = _pair_start(row)
            end = pd.Timestamp(str(row["validation_end"]), tz="UTC")
            future = pool.submit(download_vision, symbol, timeframe, start, end)
            jobs[future] = (row, start, end)

        for idx, future in enumerate(as_completed(jobs), 1):
            row, start, end = jobs[future]
            symbol = str(row["symbol"])
            pair = str(row["freqtrade_pair"])
            timeframe = str(row["timeframe"])
            validation_start = pd.Timestamp(
                str(row["validation_start"]),
                tz="UTC",
            )
            expected = int(
                (end - validation_start).total_seconds()
                // TIMEFRAME_SECONDS[timeframe]
            )
            try:
                frame = future.result()
                validation_frame = frame[
                    (frame["date"] >= validation_start)
                    & (frame["date"] < end)
                ]
                coverage = (
                    len(validation_frame) / expected
                    if expected
                    else 0.0
                )
                status = "READY" if coverage >= args.minimum_coverage else "INSUFFICIENT"
                if status == "READY":
                    handler.ohlcv_store(
                        pair,
                        timeframe,
                        frame,
                        candle_type=CandleType.FUTURES,
                    )
                    mark_frame, funding_frame = synthetic_zero_funding_aux(
                        frame,
                        validation_start,
                        end,
                    )
                    if mark_frame.empty or funding_frame.empty:
                        raise RuntimeError(
                            "Unable to create futures mark/funding auxiliary data"
                        )
                    handler.ohlcv_store(
                        pair,
                        "1h",
                        mark_frame,
                        candle_type=CandleType.MARK,
                    )
                    handler.ohlcv_store(
                        pair,
                        "1h",
                        funding_frame,
                        candle_type=CandleType.FUNDING_RATE,
                    )
                audit.append(
                    {
                        "symbol": symbol,
                        "pair": pair,
                        "timeframe": timeframe,
                        "candles": len(frame),
                        "validation_candles": len(validation_frame),
                        "expected": expected,
                        "coverage": coverage,
                        "status": status,
                        "first": frame["date"].iloc[0].isoformat() if len(frame) else None,
                        "last": frame["date"].iloc[-1].isoformat() if len(frame) else None,
                    }
                )
            except Exception as exc:
                audit.append(
                    {
                        "symbol": symbol,
                        "pair": pair,
                        "timeframe": timeframe,
                        "candles": 0,
                        "expected": expected,
                        "coverage": 0.0,
                        "status": "ERROR",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            print(
                f"[{idx}/{len(unique)}] {symbol} {timeframe} "
                f"{audit[-1]['status']}",
                flush=True,
            )

    audit_frame = pd.DataFrame(audit)
    audit_path = args.data_dir.parent / "FREQTRADE_DATA_AUDIT.csv"
    audit_frame.to_csv(audit_path, index=False)

    bad = audit_frame[~audit_frame["status"].eq("READY")]
    print(
        f"prepared={int((audit_frame['status'] == 'READY').sum())} "
        f"failed={len(bad)} audit={audit_path}",
        flush=True,
    )
    if not bad.empty:
        raise SystemExit(
            "Freqtrade execution data preparation incomplete: "
            + ", ".join(
                f"{r.symbol}:{r.timeframe}:{r.status}"
                for r in bad.itertuples()
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
