from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

BASE = "https://fapi.binance.com"
TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h")
TF_MS = {"1m":60_000,"5m":300_000,"15m":900_000,"1h":3_600_000,"4h":14_400_000}
TF_RANK = {"1m":0,"5m":1,"15m":2,"1h":3,"4h":4}
HIGHER = {
    "1m": ("5m","15m"),
    "5m": ("15m","1h"),
    "15m": ("1h","4h"),
    "1h": ("4h",),
    "4h": (),
}
STRATEGIES = (
    "SR_MTF_CONFLUENCE",
    "SR_ORDERBOOK_REJECTION",
    "SR_ENSEMBLE",
    "SR_STAT_CLUSTER",
    "SR_BREAK_RETEST",
    "SR_ROLE_REVERSAL",
    "SR_MULTI_TOUCH_BOUNCE",
    "SR_PIVOT_REVERSAL",
    "SR_RANGE_EDGE",
    "SR_RANGE_BREAK_RETEST",
)
COST_RT = 0.0015
RR = 1.5

def http_json(url: str, attempts: int = 6):
    req = urllib.request.Request(url, headers={"User-Agent":"SRMonthScan/1.0"})
    for n in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (418,429):
                time.sleep(5*(n+1))
            elif n+1 < attempts:
                time.sleep(1.5*(n+1))
            else:
                raise
    raise RuntimeError(url)

def universe(asof_ms: int) -> list[str]:
    x = http_json(f"{BASE}/fapi/v1/exchangeInfo")
    out=[]
    for s in x.get("symbols",[]):
        if s.get("status")!="TRADING": continue
        if s.get("contractType")!="PERPETUAL": continue
        if s.get("quoteAsset")!="USDT": continue
        onboard=int(s.get("onboardDate") or 0)
        if onboard and onboard >= asof_ms: continue
        out.append(str(s["symbol"]))
    return sorted(out)

def fetch_klines(symbol: str, tf: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows=[]
    cursor=start_ms
    interval=TF_MS[tf]
    while cursor < end_ms:
        q=urllib.parse.urlencode({
            "symbol":symbol,"interval":tf,"startTime":cursor,"endTime":end_ms-1,"limit":1500
        })
        chunk=http_json(f"{BASE}/fapi/v1/klines?{q}")
        if not chunk: break
        rows.extend(chunk)
        last=int(chunk[-1][0])
        nxt=last+interval
        if nxt <= cursor: break
        cursor=nxt
        if len(chunk)<1500: break
        time.sleep(0.025)
    if not rows:
        return pd.DataFrame(columns=["date","open","high","low","close","volume"])
    f=pd.DataFrame(rows,columns=["open_time","open","high","low","close","volume","close_time","qv","n","tb","tq","ignore"])
    f=f.drop_duplicates("open_time").sort_values("open_time")
    f["date"]=pd.to_datetime(f["open_time"].astype("int64"),unit="ms",utc=True)
    for c in ["open","high","low","close","volume"]:
        f[c]=pd.to_numeric(f[c],errors="coerce")
    return f[["date","open","high","low","close","volume"]].dropna().reset_index(drop=True)

def confirmed_pivots(f: pd.DataFrame, left: int=4, right: int=4):
    h=f["high"].to_numpy(float); l=f["low"].to_numpy(float)
    ph=[]; pl=[]
    n=len(f)
    for j in range(left, n-right):
        if h[j] >= np.max(h[j-left:j+right+1]) and h[j] > np.max(np.r_[h[j-left:j], h[j+1:j+right+1]]):
            ph.append((j+right, j, h[j]))  # known_at, pivot_idx, price
        if l[j] <= np.min(l[j-left:j+right+1]) and l[j] < np.min(np.r_[l[j-left:j], l[j+1:j+right+1]]):
            pl.append((j+right, j, l[j]))
    return ph,pl

def price_noise(f: pd.DataFrame) -> np.ndarray:
    c=f["close"].to_numpy(float)
    r=((f["high"]-f["low"])/f["close"]).rolling(20,min_periods=5).median().fillna(0.003).to_numpy(float)
    return np.clip(r*0.45,0.0015,0.012)

def clusters_at(i:int, pivots:list[tuple[int,int,float]], tol:float, now_price:float, min_touches:int=1):
    pts=[(pidx,price) for known,pidx,price in pivots if known <= i]
    if not pts: return []
    pts=sorted(pts,key=lambda x:x[1])
    groups=[]
    cur=[pts[0]]
    for p in pts[1:]:
        center=float(np.mean([x[1] for x in cur]))
        if abs(p[1]-center)/max(center,1e-12) <= tol:
            cur.append(p)
        else:
            groups.append(cur); cur=[p]
    groups.append(cur)
    out=[]
    for g in groups:
        if len(g)<min_touches: continue
        prices=[x[1] for x in g]
        level=float(np.mean(prices))
        recency=max(x[0] for x in g)
        age=max(0,i-recency)
        score=len(g)*20 + 60*math.exp(-age/120)
        out.append({"level":level,"touches":len(g),"recency":recency,"score":score,
                    "dist":abs(now_price-level)/max(now_price,1e-12)})
    return sorted(out,key=lambda x:(-x["score"],x["dist"]))

def nearest_levels(i, f, ph, pl, min_touches=1):
    px=float(f.iloc[i].close); tol=float(price_noise(f)[i])
    rs=clusters_at(i,ph,tol,px,min_touches); ss=clusters_at(i,pl,tol,px,min_touches)
    r_above=[x for x in rs if x["level"]>=px*(1-tol)]
    s_below=[x for x in ss if x["level"]<=px*(1+tol)]
    r=min(r_above,key=lambda x:abs(x["level"]-px),default=None)
    s=min(s_below,key=lambda x:abs(x["level"]-px),default=None)
    return s,r,tol

def rejection(bar, level, side, tol):
    o,h,l,c=map(float,[bar.open,bar.high,bar.low,bar.close])
    rng=max(h-l,1e-12); body=abs(c-o)
    if side=="LONG":
        touched=l <= level*(1+tol)
        close_back=c >= level
        lower=min(o,c)-l
        return touched and close_back and lower/rng >= 0.35 and lower >= body*0.8
    touched=h >= level*(1-tol)
    close_back=c <= level
    upper=h-max(o,c)
    return touched and close_back and upper/rng >= 0.35 and upper >= body*0.8

def simple_reject(bar,level,side,tol):
    if side=="LONG": return float(bar.low)<=level*(1+tol) and float(bar.close)>level
    return float(bar.high)>=level*(1-tol) and float(bar.close)<level

@dataclass
class Signal:
    idx:int
    side:str
    level:float
    reason:str

def sig_pivot(i,f,ph,pl,**_):
    s,r,tol=nearest_levels(i,f,ph,pl,1); b=f.iloc[i]; out=[]
    if s and simple_reject(b,s["level"],"LONG",tol): out.append(Signal(i,"LONG",s["level"],"pivot_support"))
    if r and simple_reject(b,r["level"],"SHORT",tol): out.append(Signal(i,"SHORT",r["level"],"pivot_resistance"))
    return out

def sig_multitouch(i,f,ph,pl,**_):
    s,r,tol=nearest_levels(i,f,ph,pl,3); b=f.iloc[i]; out=[]
    if s and rejection(b,s["level"],"LONG",tol): out.append(Signal(i,"LONG",s["level"],"3touch_support"))
    if r and rejection(b,r["level"],"SHORT",tol): out.append(Signal(i,"SHORT",r["level"],"3touch_resistance"))
    return out

def sig_stat(i,f,ph,pl,**_):
    s,r,tol=nearest_levels(i,f,ph,pl,2); b=f.iloc[i]; out=[]
    if s and s["score"]>=75 and rejection(b,s["level"],"LONG",tol): out.append(Signal(i,"LONG",s["level"],"stat_support"))
    if r and r["score"]>=75 and rejection(b,r["level"],"SHORT",tol): out.append(Signal(i,"SHORT",r["level"],"stat_resistance"))
    return out

def _recent_break(f,i,level,side,tol,look=12):
    c=f["close"].to_numpy(float)
    for k in range(max(1,i-look),i):
        if side=="LONG" and c[k-1] <= level*(1+tol) and c[k] > level*(1+tol): return k
        if side=="SHORT" and c[k-1] >= level*(1-tol) and c[k] < level*(1-tol): return k
    return None

def sig_break_retest(i,f,ph,pl,**_):
    px=float(f.iloc[i].close); tol=float(price_noise(f)[i]); out=[]; b=f.iloc[i]
    rs=clusters_at(i,ph,tol,px,2); ss=clusters_at(i,pl,tol,px,2)
    for x in sorted(rs,key=lambda z:z["dist"])[:4]:
        k=_recent_break(f,i,x["level"],"LONG",tol)
        if k is not None and i>k and simple_reject(b,x["level"],"LONG",tol):
            out.append(Signal(i,"LONG",x["level"],"res_break_retest")); break
    for x in sorted(ss,key=lambda z:z["dist"])[:4]:
        k=_recent_break(f,i,x["level"],"SHORT",tol)
        if k is not None and i>k and simple_reject(b,x["level"],"SHORT",tol):
            out.append(Signal(i,"SHORT",x["level"],"sup_break_retest")); break
    return out

def sig_role_reversal(i,f,ph,pl,**_):
    px=float(f.iloc[i].close); tol=float(price_noise(f)[i]); out=[]; b=f.iloc[i]
    rs=clusters_at(i,ph,tol,px,2); ss=clusters_at(i,pl,tol,px,2)
    for x in sorted(rs,key=lambda z:z["dist"])[:5]:
        k=_recent_break(f,i,x["level"],"LONG",tol,look=24)
        if k is not None and i-k>=2 and rejection(b,x["level"],"LONG",tol):
            out.append(Signal(i,"LONG",x["level"],"res_to_support")); break
    for x in sorted(ss,key=lambda z:z["dist"])[:5]:
        k=_recent_break(f,i,x["level"],"SHORT",tol,look=24)
        if k is not None and i-k>=2 and rejection(b,x["level"],"SHORT",tol):
            out.append(Signal(i,"SHORT",x["level"],"sup_to_res")); break
    return out

def range_bounds(i,f,ph,pl):
    px=float(f.iloc[i].close); tol=float(price_noise(f)[i])
    rs=clusters_at(i,ph,tol,px,2); ss=clusters_at(i,pl,tol,px,2)
    lows=[x for x in ss if x["level"]<px]; highs=[x for x in rs if x["level"]>px]
    if not lows or not highs:return None
    s=max(lows,key=lambda x:x["level"]); r=min(highs,key=lambda x:x["level"])
    width=(r["level"]-s["level"])/max(px,1e-12)
    if width<0.008 or width>0.15:return None
    return s,r,tol

def sig_range_edge(i,f,ph,pl,**_):
    q=range_bounds(i,f,ph,pl); out=[]
    if not q:return out
    s,r,tol=q; b=f.iloc[i]
    if rejection(b,s["level"],"LONG",tol):out.append(Signal(i,"LONG",s["level"],"range_support"))
    if rejection(b,r["level"],"SHORT",tol):out.append(Signal(i,"SHORT",r["level"],"range_resistance"))
    return out

def sig_range_break(i,f,ph,pl,**_):
    q=range_bounds(max(10,i-1),f,ph,pl)
    if not q:return []
    s,r,tol=q; out=[]; b=f.iloc[i]
    k=_recent_break(f,i,r["level"],"LONG",tol,12)
    if k is not None and i>k and simple_reject(b,r["level"],"LONG",tol):
        out.append(Signal(i,"LONG",r["level"],"range_break_up_retest"))
    k=_recent_break(f,i,s["level"],"SHORT",tol,12)
    if k is not None and i>k and simple_reject(b,s["level"],"SHORT",tol):
        out.append(Signal(i,"SHORT",s["level"],"range_break_down_retest"))
    return out

def sig_orderbook_proxy(i,f,ph,pl,**_):
    # OHLCV wick-defense proxy only. Historical L2 validation is a separate pass.
    s,r,tol=nearest_levels(i,f,ph,pl,2); b=f.iloc[i]; out=[]
    if s and rejection(b,s["level"],"LONG",tol*0.8):out.append(Signal(i,"LONG",s["level"],"wick_defense_support"))
    if r and rejection(b,r["level"],"SHORT",tol*0.8):out.append(Signal(i,"SHORT",r["level"],"wick_defense_resistance"))
    return out

def level_snapshot(i,f,ph,pl,min_touches=2):
    s,r,tol=nearest_levels(i,f,ph,pl,min_touches)
    return s,r,tol

def sig_mtf(i,f,ph,pl,frames=None,tf=None,**_):
    if not frames or not tf:return []
    b=f.iloc[i]; t=pd.Timestamp(b.date); s,r,tol=level_snapshot(i,f,ph,pl,2); out=[]
    for side,base_level in (("LONG",s),("SHORT",r)):
        if not base_level:continue
        agrees=1
        for htf in HIGHER.get(tf,()):
            hf=frames.get(htf)
            if hf is None or hf.empty:continue
            hi=int(hf["date"].searchsorted(t,side="right")-1)
            if hi<10:continue
            hph,hpl=hf.attrs.get("pivots",(None,None))
            if hph is None:continue
            hs,hr,htol=level_snapshot(hi,hf,hph,hpl,1)
            q=hs if side=="LONG" else hr
            if q and abs(q["level"]-base_level["level"])/base_level["level"] <= max(tol,htol)*1.5:
                agrees+=1
        if agrees>=2 and rejection(b,base_level["level"],side,tol):
            out.append(Signal(i,side,base_level["level"],f"mtf_{agrees}"))
    return out

BASE_FUNCS={
    "SR_STAT_CLUSTER":sig_stat,
    "SR_BREAK_RETEST":sig_break_retest,
    "SR_ROLE_REVERSAL":sig_role_reversal,
    "SR_MULTI_TOUCH_BOUNCE":sig_multitouch,
    "SR_PIVOT_REVERSAL":sig_pivot,
    "SR_RANGE_EDGE":sig_range_edge,
    "SR_RANGE_BREAK_RETEST":sig_range_break,
    "SR_ORDERBOOK_REJECTION":sig_orderbook_proxy,
    "SR_MTF_CONFLUENCE":sig_mtf,
}

def sig_ensemble(i,f,ph,pl,frames=None,tf=None,**_):
    votes=[]
    for name in ("SR_STAT_CLUSTER","SR_MTF_CONFLUENCE","SR_ORDERBOOK_REJECTION","SR_BREAK_RETEST"):
        q=BASE_FUNCS[name](i,f,ph,pl,frames=frames,tf=tf)
        votes.extend(q)
    out=[]
    for side in ("LONG","SHORT"):
        same=[x for x in votes if x.side==side]
        if len(same)>=2:
            level=float(np.median([x.level for x in same]))
            out.append(Signal(i,side,level,f"ensemble_{len(same)}"))
    return out

FUNCS={**BASE_FUNCS,"SR_ENSEMBLE":sig_ensemble}

def structural_trade(f:pd.DataFrame,s:Signal,tf:str,end_idx:int):
    entry_idx=s.idx+1
    if entry_idx>=end_idx:return None
    entry=float(f.iloc[entry_idx].open)
    tol=float(price_noise(f)[s.idx])
    if s.side=="LONG":
        stop=s.level*(1-max(tol,0.0015))
        risk=entry-stop
    else:
        stop=s.level*(1+max(tol,0.0015))
        risk=stop-entry
    risk_pct=risk/max(entry,1e-12)
    if risk<=0 or risk_pct<0.001 or risk_pct>0.03:return None
    target=entry + RR*risk if s.side=="LONG" else entry-RR*risk
    max_hold={"1m":180,"5m":96,"15m":64,"1h":36,"4h":18}[tf]
    last=min(end_idx-1,entry_idx+max_hold)
    exit_px=float(f.iloc[last].close); outcome="timeout"; exit_idx=last
    for j in range(entry_idx,last+1):
        h=float(f.iloc[j].high); l=float(f.iloc[j].low)
        if s.side=="LONG":
            hit_stop=l<=stop; hit_target=h>=target
        else:
            hit_stop=h>=stop; hit_target=l<=target
        # conservative if both touched in same candle
        if hit_stop:
            exit_px=stop; outcome="stop"; exit_idx=j; break
        if hit_target:
            exit_px=target; outcome="target"; exit_idx=j; break
    gross=(exit_px/entry-1) if s.side=="LONG" else (entry-exit_px)/entry
    net=gross-COST_RT
    return {"net":net,"gross":gross,"outcome":outcome,"hold_bars":exit_idx-entry_idx+1,
            "entry_idx":entry_idx,"exit_idx":exit_idx,"risk_pct":risk_pct}

def summarize(strategy,symbol,tf,trades):
    if not trades:return None
    rets=np.array([x["net"] for x in trades],float)
    wins=rets[rets>0]; losses=rets[rets<0]
    pf=float(wins.sum()/(-losses.sum())) if losses.size and -losses.sum()>0 else (999.0 if wins.size else 0.0)
    eq=np.cumprod(1+rets)
    peak=np.maximum.accumulate(eq)
    dd=(eq/peak)-1
    reaction_hits=sum(x["outcome"]=="target" for x in trades)
    return {
        "strategy":strategy,"symbol":symbol,"timeframe":tf,
        "trades":len(trades),"wins":int((rets>0).sum()),"losses":int((rets<0).sum()),
        "win_rate":float((rets>0).mean()),"target_hit_rate":reaction_hits/len(trades),
        "profit_factor":pf,"expectancy_bps":float(rets.mean()*10000),
        "net_return_sum_pct":float(rets.sum()*100),
        "compounded_return_pct":float((np.prod(1+rets)-1)*100),
        "max_drawdown_pct":float(dd.min()*100 if len(dd) else 0),
        "avg_hold_bars":float(np.mean([x["hold_bars"] for x in trades])),
        "avg_risk_pct":float(np.mean([x["risk_pct"] for x in trades])*100),
    }

def scan_symbol(symbol:str,frames:dict[str,pd.DataFrame],start:pd.Timestamp,end:pd.Timestamp):
    rows=[]
    for tf,f in frames.items():
        if f.empty or len(f)<30:continue
        ph,pl=confirmed_pivots(f)
        f.attrs["pivots"]=(ph,pl)
    for tf,f in frames.items():
        if f.empty or len(f)<50:continue
        ph,pl=f.attrs["pivots"]
        start_idx=int(f["date"].searchsorted(start,side="left"))
        end_idx=int(f["date"].searchsorted(end,side="left"))
        if end_idx-start_idx<10:continue
        for st in STRATEGIES:
            fn=FUNCS[st]; trades=[]; busy_until=-1
            for i in range(max(12,start_idx-1),end_idx-1):
                if i<busy_until:continue
                sigs=fn(i,f,ph,pl,frames=frames,tf=tf)
                if not sigs:continue
                # conflict => no trade
                sides={s.side for s in sigs}
                if len(sides)!=1:continue
                tr=structural_trade(f,sigs[0],tf,end_idx)
                if tr is None:continue
                trades.append(tr); busy_until=tr["exit_idx"]+1
            q=summarize(st,symbol,tf,trades)
            if q:rows.append(q)
    return rows

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--start",default="2026-09-01T00:00:00+03:00")
    ap.add_argument("--end",default="2026-09-26T00:00:00+03:00")
    ap.add_argument("--shard",type=int,default=0)
    ap.add_argument("--shards",type=int,default=12)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    start=pd.Timestamp(args.start).tz_convert("UTC"); end=pd.Timestamp(args.end).tz_convert("UTC")
    asof_ms=int(end.timestamp()*1000)
    syms=universe(asof_ms)
    selected=[s for idx,s in enumerate(syms) if idx%args.shards==args.shard]
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"UNIVERSE.json").write_text(json.dumps({"total":len(syms),"selected":selected},indent=2),encoding="utf-8")
    rows=[]; errors=[]
    for n,sym in enumerate(selected,1):
        print(f"[{n}/{len(selected)}] {sym}",flush=True)
        frames={}
        try:
            for tf in TIMEFRAMES:
                warm_bars=260
                warm_ms=warm_bars*TF_MS[tf]
                frames[tf]=fetch_klines(sym,tf,int(start.timestamp()*1000)-warm_ms,int(end.timestamp()*1000))
            rows.extend(scan_symbol(sym,frames,start,end))
        except Exception as exc:
            errors.append({"symbol":sym,"error":f"{type(exc).__name__}: {exc}"})
            print("ERROR",sym,errors[-1]["error"],flush=True)
    df=pd.DataFrame(rows)
    if not df.empty:
        df=df.sort_values(["strategy","timeframe","net_return_sum_pct"],ascending=[True,True,False])
    df.to_csv(args.out/"RESULTS.csv",index=False)
    (args.out/"ERRORS.json").write_text(json.dumps(errors,indent=2),encoding="utf-8")
    summary={"shard":args.shard,"shards":args.shards,"symbols":len(selected),"rows":len(rows),"errors":len(errors),
             "period":[start.isoformat(),end.isoformat()],"cost_rt_bps":COST_RT*10000,"rr":RR,
             "orderbook_note":"SR_ORDERBOOK_REJECTION is OHLCV wick-defense proxy; historical L2 variant requires separate depth-data pass."}
    (args.out/"SUMMARY.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(summary,indent=2))

if __name__=="__main__":
    main()
