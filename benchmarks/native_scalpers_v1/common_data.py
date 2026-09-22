from __future__ import annotations
import glob
import json
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
PATTERN = ROOT / "benchmarks" / "native_scalpers_v1" / "data" / "BTCUSDT_1m_20260908_20260915_part*.json"

def load_rows() -> list[list]:
    rows = []
    for path in sorted(glob.glob(str(PATTERN))):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        rows.extend(payload["rows"])
    by_ts = {int(r[0]): r for r in rows}
    out = [by_ts[k] for k in sorted(by_ts)]
    if len(out) != 10080:
        raise RuntimeError(f"expected 10080 one-minute candles, got {len(out)}")
    return out

def slice_days(rows: list[list], start_day: int, days: int) -> list[list]:
    start = int(rows[0][0]) + start_day * 86400000
    end = start + days * 86400000
    return [r for r in rows if start <= int(r[0]) < end]

def resample(rows: list[list], minutes: int) -> list[list]:
    import pandas as pd
    if minutes == 1:
        return list(rows)
    frame = pd.DataFrame(rows, columns=[
        "open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore",
    ])
    frame["ts"] = pd.to_datetime(frame["open_time"], unit="ms", utc=True)
    for col in ["open","high","low","close","volume","quote_volume","taker_buy_base","taker_buy_quote"]:
        frame[col] = pd.to_numeric(frame[col])
    frame["trades"] = pd.to_numeric(frame["trades"])
    frame = frame.set_index("ts")
    rule=f"{minutes}min"
    agg=frame.resample(rule, label="left", closed="left").agg({
        "open_time":"first","open":"first","high":"max","low":"min","close":"last",
        "volume":"sum","close_time":"last","quote_volume":"sum","trades":"sum",
        "taker_buy_base":"sum","taker_buy_quote":"sum","ignore":"last",
    }).dropna(subset=["open"])
    out=[]
    for _,r in agg.iterrows():
        out.append([
            int(r.open_time), str(float(r.open)), str(float(r.high)), str(float(r.low)),
            str(float(r.close)), str(float(r.volume)), int(r.close_time),
            str(float(r.quote_volume)), int(r.trades), str(float(r.taker_buy_base)),
            str(float(r.taker_buy_quote)), "0",
        ])
    return out

def to_frame(rows: list[list]):
    import pandas as pd
    frame=pd.DataFrame({
        "timestamp":pd.to_datetime([int(r[0]) for r in rows],unit="ms",utc=True),
        "open":[float(r[1]) for r in rows],
        "high":[float(r[2]) for r in rows],
        "low":[float(r[3]) for r in rows],
        "close":[float(r[4]) for r in rows],
        "volume":[float(r[5]) for r in rows],
    })
    return frame.set_index("timestamp")

def to_smallfish(rows: list[list]) -> list[dict]:
    return [{
        "ts":int(r[0]),"open":float(r[1]),"high":float(r[2]),"low":float(r[3]),
        "close":float(r[4]),"volume":float(r[5])
    } for r in rows]
