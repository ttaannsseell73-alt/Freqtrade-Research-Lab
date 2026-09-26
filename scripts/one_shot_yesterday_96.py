from __future__ import annotations

import argparse
import io
import json
import math
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from coin_strategy_lab import StrategyRegistry

VISION_BASE = "https://data.binance.vision/data/futures/um"
TF_SECONDS = {"5m":300,"15m":900,"1h":3600,"4h":14400,"1d":86400}
WARMUP = {"alphatrend":90,"pmax":90,"squeeze_momentum":90,"mavilimw":100,"qqe_ssl_wae":190,"utbot":70}
COLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trade_count","taker_buy_base","taker_buy_quote","ignore"]

def request_bytes(url: str, attempts: int = 4) -> bytes | None:
    req=urllib.request.Request(url,headers={"User-Agent":"CSL-yesterday96/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req,timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code==404:
                return None
            if attempt+1==attempts:
                raise
        except Exception:
            if attempt+1==attempts:
                raise
        import time
        time.sleep(1.0*(attempt+1))
    return None

def archive_urls(symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp):
    urls=[]
    month=date(start.year,start.month,1)
    end_month=date(end.year,end.month,1)
    while month < end_month:
        ym=month.strftime("%Y-%m")
        fn=f"{symbol}-{timeframe}-{ym}.zip"
        urls.append(f"{VISION_BASE}/monthly/klines/{symbol}/{timeframe}/{fn}")
        month=date(month.year+1,1,1) if month.month==12 else date(month.year,month.month+1,1)
    day=end_month
    # inclusive end date because target window may end mid-UTC-day
    while day <= end.date():
        ds=day.strftime("%Y-%m-%d")
        fn=f"{symbol}-{timeframe}-{ds}.zip"
        urls.append(f"{VISION_BASE}/daily/klines/{symbol}/{timeframe}/{fn}")
        day += timedelta(days=1)
    return urls

def read_zip(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names=[n for n in z.namelist() if not n.endswith("/")]
        if not names:
            return pd.DataFrame()
        raw=z.read(names[0])
    f=pd.read_csv(io.BytesIO(raw),header=None,low_memory=False)
    if f.empty:
        return f
    if not str(f.iloc[0,0]).lstrip("-").isdigit():
        f=f.iloc[1:].reset_index(drop=True)
    f=f.iloc[:,:12]
    f.columns=COLS[:f.shape[1]]
    for c in ["open_time","open","high","low","close","volume"]:
        f[c]=pd.to_numeric(f[c],errors="coerce")
    f=f.dropna(subset=["open_time","open","high","low","close","volume"])
    unit="us" if float(f["open_time"].median()) > 100_000_000_000_000 else "ms"
    f["date"]=pd.to_datetime(f["open_time"].astype("int64"),unit=unit,utc=True)
    return f[["date","open","high","low","close","volume"]].astype({"open":"float64","high":"float64","low":"float64","close":"float64","volume":"float64"})

def download(symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    fs=[]
    for u in archive_urls(symbol,timeframe,start,end):
        b=request_bytes(u)
        if b is None:
            continue
        q=read_zip(b)
        if not q.empty:
            fs.append(q)
    if not fs:
        return pd.DataFrame(columns=["date","open","high","low","close","volume"])
    x=pd.concat(fs,ignore_index=True).drop_duplicates("date").sort_values("date")
    return x[(x.date>=start)&(x.date<=end)].reset_index(drop=True)

def ret(side: str, entry: float, exit_price: float) -> float:
    if side=="LONG":
        return exit_price/entry - 1.0
    return (entry-exit_price)/entry

def signal_events(frame: pd.DataFrame, strategy_id: str):
    plugin=StrategyRegistry.discover_builtins().get(strategy_id)
    p=plugin.prepare(frame)
    ls=plugin.long_entries(p).fillna(False).astype(bool)
    ss=plugin.short_entries(p).fillna(False).astype(bool)
    ev=[]
    for i in range(len(p)-1):
        l=bool(ls.iloc[i]); s=bool(ss.iloc[i])
        if l==s:
            continue
        nxt=p.iloc[i+1]
        ev.append({
            "time":pd.Timestamp(nxt["date"]),
            "side":"LONG" if l else "SHORT",
            "price":float(nxt["open"]),
        })
    return ev

def simulate_symbol(row, native: pd.DataFrame, detail5: pd.DataFrame | None, day_start, day_end, cost_rt=0.0015):
    tf=str(row.timeframe); sid=str(row.strategy_id); symbol=str(row.symbol)
    events=signal_events(native,sid)
    events=[e for e in events if day_start <= e["time"] < day_end]
    event_map={}
    for e in events:
        event_map[e["time"]]=e

    trades=[]
    pos=None
    # For 5m/15m use 5m path. For slower TF, event times plus day-end mark are enough.
    if tf in {"5m","15m"}:
        path=detail5 if detail5 is not None else native
        path=path[(path.date>=day_start)&(path.date<day_end)].copy()
        for _,bar in path.iterrows():
            t=pd.Timestamp(bar.date)
            e=event_map.get(t)
            if e is not None:
                if pos is None:
                    pos={"entry_time":t,"side":e["side"],"entry":e["price"],"trail_on":False,"extreme":e["price"]}
                elif e["side"] != pos["side"]:
                    gross=ret(pos["side"],pos["entry"],e["price"])
                    trades.append({**pos,"exit_time":t,"exit":e["price"],"gross":gross,"net":gross-cost_rt,"exit_reason":"opposite"})
                    pos={"entry_time":t,"side":e["side"],"entry":e["price"],"trail_on":False,"extreme":e["price"]}
                # same-direction fresh signal does not pyramid
            if pos is None:
                continue
            hi=float(bar.high); lo=float(bar.low)
            if not pos["trail_on"]:
                hit = hi >= pos["entry"]*1.015 if pos["side"]=="LONG" else lo <= pos["entry"]*(1-0.015)
                if hit:
                    pos["trail_on"]=True
                    pos["extreme"]=hi if pos["side"]=="LONG" else lo
                continue
            stop=pos["extreme"]*(1-0.0075) if pos["side"]=="LONG" else pos["extreme"]*(1+0.0075)
            hit=lo <= stop if pos["side"]=="LONG" else hi >= stop
            if hit:
                gross=ret(pos["side"],pos["entry"],stop)
                trades.append({**pos,"exit_time":t,"exit":stop,"gross":gross,"net":gross-cost_rt,"exit_reason":"trail"})
                pos=None
                continue
            pos["extreme"]=max(pos["extreme"],hi) if pos["side"]=="LONG" else min(pos["extreme"],lo)
    else:
        for e in events:
            if pos is None:
                pos={"entry_time":e["time"],"side":e["side"],"entry":e["price"]}
            elif e["side"] != pos["side"]:
                gross=ret(pos["side"],pos["entry"],e["price"])
                trades.append({**pos,"exit_time":e["time"],"exit":e["price"],"gross":gross,"net":gross-cost_rt,"exit_reason":"opposite"})
                pos={"entry_time":e["time"],"side":e["side"],"entry":e["price"]}

    if pos is not None:
        # day-end mark: last native/detail close strictly before day_end
        mark_src=detail5 if (tf in {"5m","15m"} and detail5 is not None) else native
        q=mark_src[(mark_src.date>=pos["entry_time"])&(mark_src.date<day_end)]
        if not q.empty:
            px=float(q.iloc[-1].close)
            gross=ret(pos["side"],pos["entry"],px)
            trades.append({**pos,"exit_time":day_end,"exit":px,"gross":gross,"net":gross-cost_rt,"exit_reason":"day_end_mark"})
    for t in trades:
        t.update({"symbol":symbol,"timeframe":tf,"strategy_id":sid})
    return trades

def portfolio(candidate_trades, leverage: float, start_equity=10000.0, margin_per_trade=500.0, max_positions=20):
    items=sorted(candidate_trades,key=lambda x:(x["entry_time"],x["symbol"]))
    cash=start_equity
    active=[]
    accepted=[]
    rejected=0
    max_open=0
    def release_until(ts):
        nonlocal cash,active
        remain=[]
        for p in active:
            if p["exit_time"] <= ts:
                pnl=p["margin"]*leverage*p["net"]
                cash += p["margin"] + pnl
                p["pnl"]=pnl
                accepted.append(p)
            else:
                remain.append(p)
        active=remain
    for tr in items:
        release_until(tr["entry_time"])
        if any(p["symbol"]==tr["symbol"] for p in active):
            rejected += 1
            continue
        if len(active)>=max_positions or cash+1e-9 < margin_per_trade:
            rejected += 1
            continue
        cash -= margin_per_trade
        active.append({**tr,"margin":margin_per_trade})
        max_open=max(max_open,len(active))
    release_until(pd.Timestamp.max.tz_localize("UTC"))
    end_equity=cash
    wins=sum(1 for x in accepted if x["pnl"]>0)
    losses=sum(1 for x in accepted if x["pnl"]<0)
    flat=sum(1 for x in accepted if abs(x["pnl"])<1e-12)
    return {
        "leverage":leverage,
        "start_equity":start_equity,
        "end_equity":end_equity,
        "pnl":end_equity-start_equity,
        "return_pct":(end_equity/start_equity-1)*100,
        "accepted_trades":len(accepted),
        "wins":wins,"losses":losses,"flat":flat,
        "rejected_signals":rejected,
        "max_open_positions":max_open,
        "accepted":accepted,
    }


def full_participation(all_trades, leverage: float, start_equity=10000.0):
    # Retrospective capacity-normalized test: no signal rejection.
    # Unit margin is start equity divided by observed peak concurrency.
    # This answers "what if every valid signal was taken?" without a max-position cap.
    events=[]
    for t in all_trades:
        events.append((t["entry_time"], 1))
        events.append((t["exit_time"], -1))
    # Exits before entries at identical timestamps to avoid overstating concurrency.
    events.sort(key=lambda x: (x[0], x[1]))
    open_n=0
    peak=0
    for _,delta in events:
        open_n += delta
        peak=max(peak,open_n)
    unit_margin = start_equity / peak if peak else 0.0
    total_net = sum(float(t["net"]) for t in all_trades)
    pnl = unit_margin * leverage * total_net
    wins=sum(1 for t in all_trades if float(t["net"])>0)
    losses=sum(1 for t in all_trades if float(t["net"])<0)
    flat=len(all_trades)-wins-losses
    return {
        "leverage": leverage,
        "start_equity": start_equity,
        "end_equity": start_equity + pnl,
        "pnl": pnl,
        "return_pct": pnl / start_equity * 100.0,
        "accepted_trades": len(all_trades),
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "rejected_signals": 0,
        "peak_concurrent_positions": peak,
        "unit_margin_usdt": unit_margin,
        "required_peak_margin_if_500_each": peak * 500.0,
        "sum_net_trade_returns": total_net,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",type=Path,required=True)
    ap.add_argument("--date",required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()

    plan=pd.read_csv(args.plan)
    local_start=pd.Timestamp(args.date,tz="Europe/Istanbul")
    day_start=local_start.tz_convert("UTC")
    day_end=(local_start+pd.Timedelta(days=1)).tz_convert("UTC")

    jobs={}
    native={}
    detail={}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for r in plan.itertuples():
            tf=str(r.timeframe); sid=str(r.strategy_id)
            warm=pd.Timedelta(seconds=TF_SECONDS[tf]*WARMUP.get(sid,190))
            start=day_start-warm
            # include through UTC day containing local day-end
            fut=pool.submit(download,str(r.symbol),tf,start,day_end)
            jobs[fut]=("native",str(r.symbol),tf)
            if tf=="15m":
                fut2=pool.submit(download,str(r.symbol),"5m",day_start-pd.Timedelta(minutes=10),day_end)
                jobs[fut2]=("detail",str(r.symbol),"5m")
        for i,f in enumerate(as_completed(jobs),1):
            kind,sym,tf=jobs[f]
            x=f.result()
            if kind=="native":
                native[sym]=x
            else:
                detail[sym]=x
            print(f"[{i}/{len(jobs)}] {kind} {sym} {tf} candles={len(x)}",flush=True)

    all_trades=[]
    errors=[]
    for r in plan.itertuples():
        sym=str(r.symbol)
        x=native.get(sym,pd.DataFrame())
        if x.empty:
            errors.append(f"{sym}:{r.timeframe}:NO_DATA")
            continue
        try:
            ts=simulate_symbol(r,x,detail.get(sym),day_start,day_end)
            all_trades.extend(ts)
        except Exception as e:
            errors.append(f"{sym}:{r.timeframe}:{type(e).__name__}:{e}")

    # Candidate entries must be inside the requested local calendar day.
    all_trades=[t for t in all_trades if day_start <= t["entry_time"] < day_end]

    reports=[portfolio(all_trades,l) for l in (1.0,2.0,3.0)]
    full_reports=[full_participation(all_trades,l) for l in (1.0,2.0,3.0)]
    summary={
        "date_local":args.date,
        "timezone":"Europe/Istanbul",
        "day_start_utc":day_start.isoformat(),
        "day_end_utc":day_end.isoformat(),
        "setup_count":int(len(plan)),
        "symbols_with_candidate_trades":len(set(t["symbol"] for t in all_trades)),
        "candidate_trades":len(all_trades),
        "exit_rule":{"5m_15m":"activate +1.5%, trail 0.75%, opposite fallback","1h_4h_1d":"opposite-signal runner","roundtrip_cost_bps":15.0},
        "portfolio":{"start_usdt":10000.0,"margin_per_trade_usdt":500.0,"max_positions":20,"same_symbol_max":1},
        "reports":[{k:v for k,v in q.items() if k!="accepted"} for q in reports],
        "full_participation_reports":full_reports,
        "errors":errors,
    }
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"SUMMARY.json").write_text(json.dumps(summary,indent=2,default=str),encoding="utf-8")
    rows=[]
    for x in reports[0]["accepted"]:
        rows.append({k:(v.isoformat() if isinstance(v,pd.Timestamp) else v) for k,v in x.items()})
    pd.DataFrame(rows).to_csv(args.out/"TRADES_1X.csv",index=False)
    print(json.dumps(summary,indent=2,default=str))

if __name__=="__main__":
    main()
