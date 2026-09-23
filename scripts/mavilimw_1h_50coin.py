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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

BASE_URL = "https://data.binance.vision/data/futures/um"
UNIVERSE = [
    "BTCUSDT","ETHUSDT","SOLUSDT","ZECUSDT","HYPEUSDT","XRPUSDT","DOGEUSDT","BNBUSDT","ADAUSDT","1000PEPEUSDT",
    "WLDUSDT","NEARUSDT","SUIUSDT","PUMPUSDT","LINKUSDT","AKEUSDT","ENAUSDT","UNIUSDT","AAVEUSDT","AVAXUSDT",
    "TAOUSDT","ONDOUSDT","BCHUSDT","LITUSDT","LTCUSDT","XLMUSDT","FILUSDT","TRUMPUSDT","DOTUSDT","BTWUSDT",
    "PENGUUSDT","INJUSDT","XMRUSDT","XPLUSDT","1000SHIBUSDT","ARBUSDT","FETUSDT","ASTERUSDT","FARTCOINUSDT","ALLOUSDT",
    "1000BONKUSDT","APTUSDT","VVVUSDT","HBARUSDT","TIAUSDT","OPUSDT","VIRTUALUSDT","DASHUSDT","ETCUSDT","WIFUSDT",
]
COLS = [
    "open_time","open","high","low","close","volume","close_time","quote_volume",
    "trade_count","taker_buy_base","taker_buy_quote","ignore",
]

def _get(url: str, attempts: int = 4) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent":"Freqtrade-Research-Lab/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
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

def _zip_frame(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not names:
            return pd.DataFrame()
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return frame
    if not str(frame.iloc[0,0]).lstrip("-").isdigit():
        frame = frame.iloc[1:].reset_index(drop=True)
    frame = frame.iloc[:,:12]
    frame.columns = COLS[:frame.shape[1]]
    for c in ["open_time","open","high","low","close","volume","quote_volume","trade_count"]:
        frame[c] = pd.to_numeric(frame[c], errors="coerce")
    frame = frame.dropna(subset=["open_time","open","high","low","close","volume"])
    unit = "us" if float(frame["open_time"].median()) > 100_000_000_000_000 else "ms"
    frame["time"] = pd.to_datetime(frame["open_time"].astype("int64"), unit=unit, utc=True)
    return frame[["time","open","high","low","close","volume","quote_volume","trade_count"]]

def _archive_urls(symbol: str, start: date, end: date) -> list[str]:
    urls=[]
    qdir=urllib.parse.quote(symbol,safe="")
    month=date(start.year,start.month,1)
    end_month=date(end.year,end.month,1)
    while month < end_month:
        ym=month.strftime("%Y-%m")
        fn=urllib.parse.quote(f"{symbol}-1h-{ym}.zip",safe="-_.")
        urls.append(f"{BASE_URL}/monthly/klines/{qdir}/1h/{fn}")
        month=date(month.year + (month.month==12), 1 if month.month==12 else month.month+1, 1)
    day=end_month
    while day < end:
        ds=day.strftime("%Y-%m-%d")
        fn=urllib.parse.quote(f"{symbol}-1h-{ds}.zip",safe="-_.")
        urls.append(f"{BASE_URL}/daily/klines/{qdir}/1h/{fn}")
        day += timedelta(days=1)
    return urls

def load_symbol(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    frames=[]; missing=[]
    for url in _archive_urls(symbol,start.date(),end.date()):
        p=_get(url)
        if p is None:
            missing.append(url.rsplit("/",1)[-1]); continue
        f=_zip_frame(p)
        if not f.empty:
            frames.append(f)
    if not frames:
        return pd.DataFrame(), {"symbol":symbol,"ok":False,"error":"no_data","missing":len(missing)}
    x=pd.concat(frames,ignore_index=True).drop_duplicates("time").sort_values("time")
    x=x[(x.time>=start)&(x.time<end)].reset_index(drop=True)
    expected=int((end-start).total_seconds()//3600)
    coverage=len(x)/expected if expected else 0
    return x, {
        "symbol":symbol,"ok":not x.empty,"candles":len(x),"expected":expected,"coverage":coverage,
        "missing_archives":len(missing),
        "first":x.time.iloc[0].isoformat() if len(x) else None,
        "last":x.time.iloc[-1].isoformat() if len(x) else None,
        "median_hourly_quote_volume":float(x.quote_volume.median()) if len(x) else 0.0,
    }

def wma(s: pd.Series, length: int) -> pd.Series:
    weights=np.arange(1,length+1,dtype=float)
    denom=weights.sum()
    return s.rolling(length,min_periods=length).apply(lambda a: float(np.dot(a,weights)/denom),raw=True)

def mavilim(close: pd.Series, fmal: int=3, smal: int=5) -> pd.Series:
    tmal=fmal+smal
    Fmal=smal+tmal
    Ftmal=tmal+Fmal
    Smal=Fmal+Ftmal
    m1=wma(close,fmal)
    m2=wma(m1,smal)
    m3=wma(m2,tmal)
    m4=wma(m3,Fmal)
    m5=wma(m4,Ftmal)
    return wma(m5,Smal)

def signal_sets(frame: pd.DataFrame) -> dict[str,tuple[pd.Series,pd.Series]]:
    c=frame["close"].astype(float)
    m=mavilim(c,3,5)
    color_long=(m>m.shift(1)) & (m.shift(1)<=m.shift(2))
    color_short=(m<m.shift(1)) & (m.shift(1)>=m.shift(2))
    price_long=(c>m) & (c.shift(1)<=m.shift(1))
    price_short=(c<m) & (c.shift(1)>=m.shift(1))
    return {
        "color_flip":(color_long.fillna(False),color_short.fillna(False)),
        "price_cross":(price_long.fillna(False),price_short.fillna(False)),
    }

def build_trades(frame: pd.DataFrame, lo: pd.Series, sh: pd.Series) -> pd.DataFrame:
    rows=[]
    pos=0
    entry_px=None
    entry_time=None
    n=len(frame)
    for i in range(n-1):
        long_sig=bool(lo.iloc[i]); short_sig=bool(sh.iloc[i])
        if not (long_sig or short_sig):
            continue
        desired=1 if long_sig else -1
        if pos==0:
            entry_px=float(frame.open.iloc[i+1]); entry_time=frame.time.iloc[i+1]; pos=desired
            continue
        if desired==pos:
            continue
        exit_px=float(frame.open.iloc[i+1]); exit_time=frame.time.iloc[i+1]
        gross=(exit_px/entry_px-1.0)*10000*pos
        rows.append({
            "entry_time":entry_time,"exit_time":exit_time,"direction":"LONG" if pos==1 else "SHORT",
            "entry_price":entry_px,"exit_price":exit_px,"gross_bps":gross,
            "holding_hours":float((exit_time-entry_time).total_seconds()/3600),
        })
        entry_px=exit_px; entry_time=exit_time; pos=desired
    if pos!=0 and entry_px is not None and n:
        exit_px=float(frame.close.iloc[-1]); exit_time=frame.time.iloc[-1]
        gross=(exit_px/entry_px-1.0)*10000*pos
        rows.append({
            "entry_time":entry_time,"exit_time":exit_time,"direction":"LONG" if pos==1 else "SHORT",
            "entry_price":entry_px,"exit_price":exit_px,"gross_bps":gross,
            "holding_hours":float((exit_time-entry_time).total_seconds()/3600),
        })
    return pd.DataFrame(rows)

def stats(trades: pd.DataFrame, cost_bps: float) -> dict:
    if trades.empty:
        return {"trades":0,"expectancy_bps":math.nan,"net_sum_bps":0.0,"win_rate":math.nan,
                "profit_factor":math.nan,"median_bps":math.nan,"max_drawdown_bps":math.nan,
                "avg_holding_hours":math.nan,"long_trades":0,"short_trades":0}
    net=trades.gross_bps.to_numpy(float)-cost_bps
    win=net[net>0]; loss=net[net<0]
    pf=float(win.sum()/abs(loss.sum())) if len(loss) and abs(loss.sum())>1e-12 else (math.inf if len(win) else math.nan)
    curve=np.cumsum(net)
    peak=np.maximum.accumulate(np.r_[0.0,curve])
    dd=peak[1:]-curve
    return {
        "trades":int(len(net)),"expectancy_bps":float(net.mean()),"net_sum_bps":float(net.sum()),
        "win_rate":float((net>0).mean()),"profit_factor":pf,"median_bps":float(np.median(net)),
        "max_drawdown_bps":float(dd.max()) if len(dd) else 0.0,
        "avg_holding_hours":float(trades.holding_hours.mean()),
        "long_trades":int((trades.direction=="LONG").sum()),"short_trades":int((trades.direction=="SHORT").sum()),
    }

def evaluate(symbol: str, frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[list[dict],pd.DataFrame]:
    rows=[]; all_trades=[]
    t1=start+(end-start)*0.50
    t2=start+(end-start)*0.75
    for mode,(lo,sh) in signal_sets(frame).items():
        tr=build_trades(frame,lo,sh)
        if not tr.empty:
            tr=tr.copy(); tr["symbol"]=symbol; tr["mode"]=mode
            all_trades.append(tr)
        for split in ["full","train","validation","holdout"]:
            if split=="full":
                s=tr
            elif split=="train":
                s=tr[tr.entry_time<t1] if not tr.empty else tr
            elif split=="validation":
                s=tr[(tr.entry_time>=t1)&(tr.entry_time<t2)] if not tr.empty else tr
            else:
                s=tr[tr.entry_time>=t2] if not tr.empty else tr
            for cost in [6.0,10.0,15.0]:
                rows.append({"symbol":symbol,"timeframe":"1h","mode":mode,"split":split,"cost_bps":cost,**stats(s,cost)})
    trades=pd.concat(all_trades,ignore_index=True) if all_trades else pd.DataFrame()
    return rows,trades

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--start",default="2026-06-24")
    p.add_argument("--end",default="2026-09-24")
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args()
    start=pd.Timestamp(args.start,tz="UTC"); end=pd.Timestamp(args.end,tz="UTC")
    out=args.output_dir; out.mkdir(parents=True,exist_ok=True)

    audit=[]; rows=[]; trades=[]
    downloaded={}
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures={pool.submit(load_symbol,symbol,start,end):symbol for symbol in UNIVERSE}
        for n,fut in enumerate(as_completed(futures),1):
            symbol=futures[fut]
            try:
                frame,a=fut.result()
            except Exception as exc:
                frame=pd.DataFrame()
                a={"symbol":symbol,"ok":False,"error":repr(exc),"coverage":0.0}
            audit.append(a)
            downloaded[symbol]=frame
            print(f"[download {n}/{len(UNIVERSE)}] {symbol} coverage={a.get('coverage',0):.4f}",flush=True)
    for i,symbol in enumerate(UNIVERSE,1):
        frame=downloaded.get(symbol,pd.DataFrame())
        a=next((x for x in audit if x.get("symbol")==symbol),{"coverage":0.0})
        print(f"[eval {i}/{len(UNIVERSE)}] {symbol}",flush=True)
        if frame.empty or a.get("coverage",0)<0.95:
            continue
        rr,tt=evaluate(symbol,frame,start,end)
        rows.extend(rr)
        if not tt.empty:
            trades.append(tt)

    audit_df=pd.DataFrame(audit)
    results=pd.DataFrame(rows)
    trades_df=pd.concat(trades,ignore_index=True) if trades else pd.DataFrame()
    audit_df.to_csv(out/"DATA_AUDIT.csv",index=False)
    results.to_csv(out/"MAVILIM_RESULTS.csv",index=False)
    trades_df.to_csv(out/"MAVILIM_TRADES.csv",index=False)

    base=results[results.cost_bps==10].copy()
    piv=base.pivot_table(index=["symbol","mode"],columns="split",
        values=["trades","expectancy_bps","profit_factor","net_sum_bps","win_rate","max_drawdown_bps","avg_holding_hours"],aggfunc="first")
    piv.columns=[f"{a}_{b}" for a,b in piv.columns]
    piv=piv.reset_index()
    stress=results[(results.split=="full")&(results.cost_bps==15)][["symbol","mode","expectancy_bps"]].rename(columns={"expectancy_bps":"expectancy_full_15bps"})
    piv=piv.merge(stress,on=["symbol","mode"],how="left")
    piv["stable_oos"]=(
        (piv["trades_validation"]>=3)&(piv["trades_holdout"]>=3)&
        (piv["expectancy_bps_validation"]>0)&(piv["expectancy_bps_holdout"]>0)&
        (piv["profit_factor_validation"]>1)&(piv["profit_factor_holdout"]>1)&
        (piv["expectancy_full_15bps"]>0)
    )
    piv["three_period_positive"]=(
        (piv["expectancy_bps_train"]>0)&(piv["expectancy_bps_validation"]>0)&(piv["expectancy_bps_holdout"]>0)
    )
    piv["oos_floor_bps"]=piv[["expectancy_bps_validation","expectancy_bps_holdout"]].min(axis=1)
    piv=piv.sort_values(["stable_oos","three_period_positive","oos_floor_bps","expectancy_bps_full"],ascending=False)
    piv.to_csv(out/"MAVILIM_SUMMARY.csv",index=False)

    top=piv.groupby("symbol",as_index=False).head(1).copy()
    top.to_csv(out/"BEST_MODE_BY_COIN.csv",index=False)

    md=["# MavilimW 1H — 50 Coin / 3 Month Benchmark\n\n",
        "Default parameters: 3/5, Fibonacci WMA cascade 3-5-8-13-21-34. Entries and reversals occur at the next candle open after a closed-candle signal. Main friction assumption is 10 bps round-trip, with 6/15 bps sensitivity.\n\n",
        "Two signal interpretations are tested separately: color_flip (original MavilimW slope/color-change alarms) and price_cross (default 3/5 price crossing the MavilimW line).\n\n",
        "| Coin | Mode | Full exp bps | Train | Val | Holdout | PF full | Trades | DD bps | 15bps exp | Stable OOS |\n",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|\n"]
    for r in piv.head(50).itertuples():
        md.append(f"| {r.symbol} | {r.mode} | {r.expectancy_bps_full:.2f} | {r.expectancy_bps_train:.2f} | {r.expectancy_bps_validation:.2f} | {r.expectancy_bps_holdout:.2f} | {r.profit_factor_full:.2f} | {int(r.trades_full)} | {r.max_drawdown_bps_full:.1f} | {r.expectancy_full_15bps:.2f} | {'YES' if r.stable_oos else 'NO'} |\n")
    (out/"REPORT.md").write_text("".join(md),encoding="utf-8")

    summary={
        "status":"COMPLETE",
        "universe_requested":len(UNIVERSE),
        "universe_tested":int((audit_df.get("coverage",pd.Series(dtype=float))>=0.95).sum()),
        "period":{"start":start.isoformat(),"end_exclusive":end.isoformat()},
        "timeframe":"1h",
        "mavilim_parameters":[3,5],
        "modes":["color_flip","price_cross"],
        "main_cost_bps_round_trip":10,
        "stable_oos_rows":int(piv.stable_oos.sum()),
        "three_period_positive_rows":int(piv.three_period_positive.sum()),
        "coins_with_stable_oos":int(piv[piv.stable_oos].symbol.nunique()),
        "limitations":[
            "Research backtest, not live execution.",
            "Reversal modeled at next candle open; funding, spread, queue position and liquidation risk are not modeled.",
            "Three-month sample is short for a slow trend indicator, so trade counts can be small.",
        ],
    }
    (out/"SUMMARY.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    (out/"RUN_MANIFEST.json").write_text(json.dumps({
        "system":"MavilimW","source_formula":"3/5 Fibonacci WMA cascade: 3,5,8,13,21,34",
        "signal_color_flip":"crossover(MAVW,MAVW[1]) / crossunder(MAVW,MAVW[1])",
        "signal_price_cross":"close cross above/below MAVW",
        "split":"50% train / 25% validation / 25% holdout by entry time",
        "costs_bps_round_trip":[6,10,15],
        "universe":UNIVERSE,
    },indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2),flush=True)
    return 0

if __name__=="__main__":
    raise SystemExit(main())
