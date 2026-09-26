from __future__ import annotations
import argparse, calendar, json, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import one_shot_yesterday_96 as base

EXPECTED = {
  1: 2154.69, 2: 1169.42, 3: 1420.35, 4: 1491.00,
  5: -514.34, 6: 2329.93, 7: 565.40, 8: 1251.65, 9: 2758.2528912816633,
}

def summarize_side(trades, side):
    q=[t for t in trades if t["side"]==side]
    pnl=sum(float(t["pnl"]) for t in q)
    return {
      "trades":len(q),
      "pnl":pnl,
      "wins":sum(float(t["pnl"])>0 for t in q),
      "losses":sum(float(t["pnl"])<0 for t in q),
      "win_rate":sum(float(t["pnl"])>0 for t in q)/len(q) if q else 0.0,
      "avg_pnl":pnl/len(q) if q else 0.0,
    }

def bench(symbol,start,end):
    x=base.download(symbol,"1h",start,end)
    x=x[(x.date>=start)&(x.date<end)]
    if x.empty:return None
    op=float(x.iloc[0].open); cl=float(x.iloc[-1].close)
    return (cl/op-1)*100.0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--plan",type=Path,required=True)
    ap.add_argument("--metrics",type=Path,required=True)
    ap.add_argument("--month",type=int,required=True)
    ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args()
    plan=pd.read_csv(args.plan)
    _=pd.read_csv(args.metrics)
    orig=base.request_bytes
    @lru_cache(maxsize=None)
    def cached(url:str,attempts:int=4):
        return orig(url,attempts)
    base.request_bytes=cached

    month=args.month
    max_day=25 if month==9 else calendar.monthrange(2026,month)[1]
    month_start_local=pd.Timestamp(f"2026-{month:02d}-01",tz="Europe/Istanbul")
    month_end_local=(pd.Timestamp("2026-09-26",tz="Europe/Istanbul") if month==9
                     else month_start_local+pd.offsets.MonthBegin(1))
    month_start=month_start_local.tz_convert("UTC")
    month_end=month_end_local.tz_convert("UTC")

    accepted_all=[]
    daily=[]
    errors=[]
    for day in range(1,max_day+1):
        date=f"2026-{month:02d}-{day:02d}"
        ls=pd.Timestamp(date,tz="Europe/Istanbul")
        ds=ls.tz_convert("UTC"); de=(ls+pd.Timedelta(days=1)).tz_convert("UTC")
        jobs={}; native={}; detail={}
        with ThreadPoolExecutor(max_workers=16) as pool:
            for r in plan.itertuples():
                tf=str(r.timeframe); sid=str(r.strategy_id); sym=str(r.symbol)
                warm=pd.Timedelta(seconds=base.TF_SECONDS[tf]*base.WARMUP.get(sid,190))
                f=pool.submit(base.download,sym,tf,ds-warm,de)
                jobs[f]=("native",sym,tf)
                if tf=="15m":
                    f2=pool.submit(base.download,sym,"5m",ds-pd.Timedelta(minutes=10),de)
                    jobs[f2]=("detail",sym,"5m")
            for fut in as_completed(jobs):
                kind,sym,tf=jobs[fut]
                x=fut.result()
                if kind=="native": native[sym]=x
                else: detail[sym]=x

        all_trades=[]
        for r in plan.itertuples():
            sym=str(r.symbol); x=native.get(sym,pd.DataFrame())
            if x.empty:
                errors.append(f"{date}:{sym}:{r.timeframe}:NO_DATA"); continue
            try:
                all_trades.extend(base.simulate_symbol(r,x,detail.get(sym),ds,de))
            except Exception as exc:
                errors.append(f"{date}:{sym}:{r.timeframe}:{type(exc).__name__}:{exc}")
        all_trades=[t for t in all_trades if ds<=t["entry_time"]<de]
        p=base.portfolio(all_trades,1.0)
        acc=p["accepted"]
        accepted_all.extend(acc)
        daily.append({
          "date":date,"pnl":float(p["pnl"]),"trades":int(p["accepted_trades"]),
          "long_pnl":sum(float(t["pnl"]) for t in acc if t["side"]=="LONG"),
          "short_pnl":sum(float(t["pnl"]) for t in acc if t["side"]=="SHORT"),
          "long_trades":sum(t["side"]=="LONG" for t in acc),
          "short_trades":sum(t["side"]=="SHORT" for t in acc),
        })
        print("DAY",date,p["pnl"],len(acc),flush=True)

    total=sum(x["pnl"] for x in daily)
    exp=EXPECTED[month]
    matched=abs(total-exp) <= max(0.02,abs(exp)*1e-5)

    by_tf={}
    by_strategy={}
    for t in accepted_all:
        for container,key in ((by_tf,str(t["timeframe"])),(by_strategy,str(t["strategy_id"]))):
            row=container.setdefault(key,{"LONG":[],"SHORT":[]})
            row[t["side"]].append(t)

    def group_report(container):
        out={}
        for k,sides in container.items():
            out[k]={side:summarize_side(q,side) for side,q in sides.items()}
        return out

    report={
      "month":month,
      "period":[month_start.isoformat(),month_end.isoformat()],
      "baseline_expected_pnl":exp,
      "reproduced_pnl":total,
      "baseline_match":matched,
      "long":summarize_side(accepted_all,"LONG"),
      "short":summarize_side(accepted_all,"SHORT"),
      "btc_return_pct":bench("BTCUSDT",month_start,month_end),
      "eth_return_pct":bench("ETHUSDT",month_start,month_end),
      "by_timeframe":group_report(by_tf),
      "by_strategy":group_report(by_strategy),
      "daily":daily,
      "errors":errors,
      "notes":["Same frozen 96 PASS portfolio acceptance and 1x sizing as baseline.","Direction attribution only; no strategy rule changed."]
    }
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"DIRECTION.json").write_text(json.dumps(report,indent=2,default=str),encoding="utf-8")
    print(json.dumps({k:report[k] for k in ["month","baseline_expected_pnl","reproduced_pnl","baseline_match","long","short","btc_return_pct","eth_return_pct"]},indent=2))
    if not matched:
        raise SystemExit(2)

if __name__=="__main__":
    main()
