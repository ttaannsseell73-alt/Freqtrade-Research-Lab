from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

import one_shot_yesterday_96 as base
from coin_strategy_lab import StrategyRegistry

WINDOWS = {
    "5m": [90, 180],
    "15m": [180, 365],
    "1h": [90, 180, 365],
    "4h": [365, 730],
    "1d": [730, 1095],
}
TF_SECONDS = base.TF_SECONDS


def reversal_trades(frame: pd.DataFrame, strategy_id: str) -> pd.DataFrame:
    plugin = StrategyRegistry.discover_builtins().get(strategy_id)
    p = plugin.prepare(frame)
    ls = plugin.long_entries(p).fillna(False).astype(bool)
    ss = plugin.short_entries(p).fillna(False).astype(bool)
    rows = []
    pos = 0
    entry = None
    entry_time = None
    for i in range(len(p) - 1):
        l = bool(ls.iloc[i]); s = bool(ss.iloc[i])
        if l == s:
            continue
        desired = 1 if l else -1
        px = float(p["open"].iloc[i + 1])
        ts = pd.Timestamp(p["date"].iloc[i + 1])
        if pos == 0:
            pos, entry, entry_time = desired, px, ts
            continue
        if desired == pos:
            continue
        gross_bps = (px / float(entry) - 1.0) * 10000.0 * pos
        rows.append({
            "entry_time": entry_time,
            "exit_time": ts,
            "direction": "LONG" if pos == 1 else "SHORT",
            "gross_bps": gross_bps,
        })
        pos, entry, entry_time = desired, px, ts
    if pos and entry is not None and len(p):
        px = float(p["close"].iloc[-1])
        ts = pd.Timestamp(p["date"].iloc[-1])
        gross_bps = (px / float(entry) - 1.0) * 10000.0 * pos
        rows.append({
            "entry_time": entry_time,
            "exit_time": ts,
            "direction": "LONG" if pos == 1 else "SHORT",
            "gross_bps": gross_bps,
        })
    return pd.DataFrame(rows)


def metric(trades: pd.DataFrame, cost_bps: float) -> dict:
    if trades.empty:
        return {"trades": 0, "expectancy_bps": math.nan, "profit_factor": math.nan}
    net = trades["gross_bps"].to_numpy(float) - cost_bps
    wins = net[net > 0]
    losses = net[net < 0]
    pf = float(wins.sum() / abs(losses.sum())) if len(losses) and abs(losses.sum()) > 1e-12 else (math.inf if len(wins) else math.nan)
    return {
        "trades": int(len(net)),
        "expectancy_bps": float(net.mean()),
        "profit_factor": pf,
    }


def split_trades(trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, part: str):
    t1 = start + (end - start) * 0.50
    t2 = start + (end - start) * 0.75
    if trades.empty or part == "full":
        return trades
    if part == "train":
        return trades[trades.entry_time < t1]
    if part == "validation":
        return trades[(trades.entry_time >= t1) & (trades.entry_time < t2)]
    if part == "holdout":
        return trades[trades.entry_time >= t2]
    raise ValueError(part)


def caplog(v: float, cap: float) -> float:
    return min(math.log1p(max(float(v), 0.0)) / math.log1p(cap), 1.0)


def confidence_score(worst_oos: float, worst15: float, worst_pf: float, total_trades: int, windows_passed: int, expected_windows: int) -> float:
    edge = caplog(worst_oos, 100.0)
    stress = caplog(worst15, 100.0)
    pf = min(max((float(worst_pf) - 1.0) / 1.0, 0.0), 1.0)
    sample = caplog(total_trades, 300.0)
    windows = min(max(windows_passed / expected_windows, 0.0), 1.0)
    return 100.0 * (0.35*edge + 0.25*stress + 0.15*pf + 0.15*sample + 0.10*windows)


def quality_for_setup(symbol: str, timeframe: str, strategy_id: str, frame: pd.DataFrame, cutoff: pd.Timestamp) -> dict:
    days_list = WINDOWS[timeframe]
    passed = []
    max_days = max(days_list)
    exec_start = cutoff - pd.Timedelta(days=max_days)
    exec_frame = frame[(frame.date >= exec_start) & (frame.date < cutoff)].copy()
    exec_trades = reversal_trades(exec_frame, strategy_id)
    exec_m = metric(exec_trades, 15.0)
    direction = {}
    for side in ("LONG", "SHORT"):
        d = exec_trades[exec_trades.direction == side] if not exec_trades.empty else exec_trades
        direction[side] = metric(d, 15.0)["expectancy_bps"] / 10000.0 if not d.empty else math.nan

    for days in days_list:
        start = cutoff - pd.Timedelta(days=days)
        sub = frame[(frame.date >= start) & (frame.date < cutoff)].copy()
        expected = int(pd.Timedelta(days=days).total_seconds() // TF_SECONDS[timeframe])
        coverage = len(sub) / expected if expected else 0.0
        if coverage < 0.95:
            continue
        tr = reversal_trades(sub, strategy_id)
        full10 = metric(tr, 10.0)
        train10 = metric(split_trades(tr,start,cutoff,"train"), 10.0)
        val10 = metric(split_trades(tr,start,cutoff,"validation"), 10.0)
        hold10 = metric(split_trades(tr,start,cutoff,"holdout"), 10.0)
        full15 = metric(tr, 15.0)
        vals = [train10["expectancy_bps"], val10["expectancy_bps"], hold10["expectancy_bps"], val10["profit_factor"], hold10["profit_factor"], full15["expectancy_bps"]]
        finite = all(not (pd.isna(v)) for v in vals)
        candidate = (
            finite
            and full10["trades"] >= 20
            and val10["trades"] >= 5
            and hold10["trades"] >= 5
            and train10["expectancy_bps"] > 0
            and val10["expectancy_bps"] > 0
            and hold10["expectancy_bps"] > 0
            and val10["profit_factor"] > 1.0
            and hold10["profit_factor"] > 1.0
            and full15["expectancy_bps"] > 0
        )
        if candidate:
            passed.append({
                "days":days,
                "oos_floor":min(val10["expectancy_bps"],hold10["expectancy_bps"]),
                "stress15":full15["expectancy_bps"],
                "holdout_pf":hold10["profit_factor"],
                "trades":full10["trades"],
            })

    expected_windows = len(days_list)
    if passed:
        worst_oos = min(x["oos_floor"] for x in passed)
        worst15 = min(x["stress15"] for x in passed)
        worst_pf = min(x["holdout_pf"] for x in passed)
        total_trades = sum(x["trades"] for x in passed)
        conf = confidence_score(worst_oos,worst15,worst_pf,total_trades,len(passed),expected_windows)
    else:
        worst_oos = worst15 = -math.inf
        worst_pf = 0.0
        total_trades = 0
        conf = 0.0

    return {
        "symbol":symbol,
        "timeframe":timeframe,
        "strategy_id":strategy_id,
        "windows_passed":len(passed),
        "expected_windows":expected_windows,
        "research_confidence_score":conf,
        "research_worst_oos_floor_bps":worst_oos,
        "research_worst_15bps_expectancy":worst15,
        "research_worst_holdout_profit_factor":worst_pf,
        "research_total_trades":total_trades,
        "profit_factor":exec_m["profit_factor"],
        "long_expectancy":direction["LONG"],
        "short_expectancy":direction["SHORT"],
        "execution_trades":exec_m["trades"],
    }


def classify(trade: dict, m: dict):
    side = trade["side"].lower()
    de = m[f"{side}_expectancy"]
    conf = m["research_confidence_score"]
    pf = m["profit_factor"]
    hpf = m["research_worst_holdout_profit_factor"]
    w15 = m["research_worst_15bps_expectancy"]
    if pd.isna(de) or de <= 0 or conf < 65 or pd.isna(pf) or pf < 1.20 or hpf < 1.15:
        return "WEAK", 0
    if conf >= 85 and pf >= 1.40 and hpf >= 1.30 and w15 >= 50.0:
        return "STRONG", 2
    return "MEDIUM", 1


def weighted_portfolio(trades, quality_map, leverage: float, start_equity=10000.0):
    enriched=[]
    weak=0
    for t in trades:
        key=(t["symbol"],t["timeframe"],t["strategy_id"])
        m=quality_map[key]
        q,w=classify(t,m)
        if q=="WEAK":
            weak += 1
            continue
        enriched.append({**t,"quality":q,"weight":w})

    accepted=[]
    active={}
    overlap=0
    for t in sorted(enriched,key=lambda x:(x["entry_time"],-x["weight"],x["symbol"])):
        prev=active.get(t["symbol"])
        if prev is not None and prev["exit_time"] > t["entry_time"]:
            overlap += 1
            continue
        accepted.append(t)
        active[t["symbol"]]=t

    events=[]
    for t in accepted:
        events.append((t["entry_time"],1,t["weight"]))
        events.append((t["exit_time"],-1,t["weight"]))
    events.sort(key=lambda x:(x[0],x[1]))
    ow=op=peakw=peakp=0
    for _,kind,w in events:
        if kind==-1:
            ow-=w; op-=1
        else:
            ow+=w; op+=1; peakw=max(peakw,ow); peakp=max(peakp,op)
    unit=start_equity/peakw if peakw else 0.0
    pnl=0.0; wins=losses=0
    for t in accepted:
        p=unit*t["weight"]*leverage*float(t["net"])
        pnl+=p
        if p>0:wins+=1
        elif p<0:losses+=1
    return {
        "leverage":leverage,
        "start_equity":start_equity,
        "end_equity":start_equity+pnl,
        "pnl":pnl,
        "return_pct":pnl/start_equity*100,
        "candidate_trades":len(trades),
        "weak_filtered":weak,
        "accepted_trades":len(accepted),
        "medium_trades":sum(1 for t in accepted if t["quality"]=="MEDIUM"),
        "strong_trades":sum(1 for t in accepted if t["quality"]=="STRONG"),
        "wins":wins,"losses":losses,
        "same_symbol_overlap_skipped":overlap,
        "peak_concurrent_positions":peakp,
        "peak_weight_units":peakw,
        "unit_margin_usdt":unit,
        "medium_margin_usdt":unit,
        "strong_margin_usdt":unit*2,
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

    native={}; detail={}; jobs={}
    with ThreadPoolExecutor(max_workers=16) as pool:
        for r in plan.itertuples():
            tf=str(r.timeframe); sid=str(r.strategy_id)
            warm=pd.Timedelta(seconds=TF_SECONDS[tf]*base.WARMUP.get(sid,190))
            fut=pool.submit(base.download,str(r.symbol),tf,day_start-warm,day_end)
            jobs[fut]=("native",str(r.symbol),tf)
            if tf=="15m":
                fut2=pool.submit(base.download,str(r.symbol),"5m",day_start-pd.Timedelta(minutes=10),day_end)
                jobs[fut2]=("detail",str(r.symbol),"5m")
        for fut in as_completed(jobs):
            kind,sym,tf=jobs[fut]
            x=fut.result()
            if kind=="native": native[sym]=x
            else: detail[sym]=x

    all_trades=[]
    for r in plan.itertuples():
        x=native.get(str(r.symbol),pd.DataFrame())
        if x.empty: continue
        all_trades.extend(base.simulate_symbol(r,x,detail.get(str(r.symbol)),day_start,day_end))
    all_trades=[t for t in all_trades if day_start <= t["entry_time"] < day_end]

    keys=sorted(set((t["symbol"],t["timeframe"],t["strategy_id"]) for t in all_trades))
    plan_lookup={(str(r.symbol),str(r.timeframe),str(r.strategy_id)):r for r in plan.itertuples()}

    hist={}; hjobs={}
    with ThreadPoolExecutor(max_workers=12) as pool:
        for key in keys:
            sym,tf,sid=key
            max_days=max(WINDOWS[tf])
            warm=pd.Timedelta(seconds=TF_SECONDS[tf]*base.WARMUP.get(sid,190))
            fut=pool.submit(base.download,sym,tf,day_start-pd.Timedelta(days=max_days)-warm,day_start)
            hjobs[fut]=key
        for i,fut in enumerate(as_completed(hjobs),1):
            key=hjobs[fut]
            hist[key]=fut.result()
            print(f"[quality {i}/{len(hjobs)}] {key[0]} {key[1]} candles={len(hist[key])}",flush=True)

    quality={}
    for key in keys:
        sym,tf,sid=key
        quality[key]=quality_for_setup(sym,tf,sid,hist[key],day_start)

    reports=[weighted_portfolio(all_trades,quality,l) for l in (1.0,2.0,3.0)]
    out={
        "date_local":args.date,
        "timezone":"Europe/Istanbul",
        "quality_cutoff_utc":day_start.isoformat(),
        "quality_lookahead":False,
        "setup_count":int(len(plan)),
        "candidate_trades":len(all_trades),
        "candidate_setups":len(keys),
        "quality_rule":{
            "weak":"direction expectancy <= 0 OR confidence < 65 OR PF < 1.20 OR worst holdout PF < 1.15",
            "strong":"confidence >= 85 AND PF >= 1.40 AND worst holdout PF >= 1.30 AND worst 15bps expectancy >= 50bps",
            "medium":"passes weak filter but not strong",
            "weights":{"WEAK":0,"MEDIUM":1,"STRONG":2},
        },
        "exit_rule":{"5m_15m":"activate +1.5%, trail 0.75%, opposite fallback","1h_4h_1d":"opposite-signal runner","roundtrip_cost_bps":15.0},
        "reports":reports,
        "quality_metrics":list(quality.values()),
    }
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"SUMMARY.json").write_text(json.dumps(out,indent=2,default=str,allow_nan=True),encoding="utf-8")
    print(json.dumps({k:v for k,v in out.items() if k!="quality_metrics"},indent=2,default=str))

if __name__=="__main__":
    main()
