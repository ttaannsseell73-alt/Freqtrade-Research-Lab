from __future__ import annotations
import argparse,csv,json,subprocess,sys,tempfile
from pathlib import Path
import pandas as pd

TF_BY_KIND={"v0nog":"15m","theta":"1h","cryptobot":"1h","rsi":"15m","bino":"5m"}
DAYS=["2026-09-08","2026-09-25"]

def run(cmd):
    return subprocess.run(cmd,text=True,capture_output=True,check=False)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--kind",required=True,choices=sorted(TF_BY_KIND))
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--plan",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--warmup-days",type=int,default=7)
    a=ap.parse_args()
    a.out.mkdir(parents=True,exist_ok=True)
    plan=pd.read_csv(a.plan)
    symbols=sorted(plan["symbol"].astype(str).unique().tolist())
    tf=TF_BY_KIND[a.kind]
    rows=[]; errors=[]
    for i,sym in enumerate(symbols,1):
        print(f"[{a.kind}] {i}/{len(symbols)} {sym}",flush=True)
        for day in DAYS:
            d=pd.Timestamp(day)
            start=(d-pd.Timedelta(days=a.warmup_days)).strftime("%Y-%m-%d")
            data=a.out/f"_tmp_{sym}_{day}_{tf}.csv"
            result=a.out/f"_tmp_{sym}_{day}.json"
            dl=run([sys.executable,"scripts/download_binance_vision_range.py",
                    "--market","um","--symbol",sym,"--timeframe",tf,
                    "--start",start,"--end",day,"--out",str(data)])
            if dl.returncode!=0 or not data.exists() or data.stat().st_size<100:
                errors.append({"symbol":sym,"day":day,"stage":"download","stderr":dl.stderr[-1000:],"stdout":dl.stdout[-1000:]})
                rows.append({"project_kind":a.kind,"symbol":sym,"day_local":day,"status":"NO_DATA"})
                continue
            br=run([sys.executable,"scripts/external_native_day_benchmark.py",
                    "--kind",a.kind,"--project",str(a.project),"--data",str(data),
                    "--day",day,"--symbol",sym,"--out",str(result)])
            if br.returncode!=0 or not result.exists():
                errors.append({"symbol":sym,"day":day,"stage":"backtest","stderr":br.stderr[-2000:],"stdout":br.stdout[-2000:]})
                rows.append({"project_kind":a.kind,"symbol":sym,"day_local":day,"status":"ERROR"})
            else:
                try:
                    rows.append(json.loads(result.read_text()))
                except Exception as e:
                    errors.append({"symbol":sym,"day":day,"stage":"parse","error":str(e)})
                    rows.append({"project_kind":a.kind,"symbol":sym,"day_local":day,"status":"ERROR"})
            try:data.unlink()
            except:pass
            try:result.unlink()
            except:pass
    df=pd.DataFrame(rows)
    df.to_csv(a.out/"per_symbol.csv",index=False)
    summary={"project_kind":a.kind,"source_plan_rows":int(len(plan)),"unique_symbols":len(symbols),"timeframe":tf,"market_data":"Binance USD-M futures","warmup_days":a.warmup_days,"days":{},"errors":errors}
    for day in DAYS:
        q=df[df["day_local"]==day].copy()
        ok=q[q["status"]=="OK"].copy()
        if len(ok):
            ret=pd.to_numeric(ok["day_return_pct"],errors="coerce")
            ev=pd.to_numeric(ok["day_events"],errors="coerce").fillna(0)
            summary["days"][day]={
                "symbols_total":len(symbols),
                "symbols_ok":int(len(ok)),
                "symbols_no_data":int((q["status"]=="NO_DATA").sum()),
                "symbols_error":int((q["status"]=="ERROR").sum()),
                "symbols_active":int((ev>0).sum()),
                "symbols_positive":int((ret>0).sum()),
                "symbols_negative":int((ret<0).sum()),
                "symbols_flat":int((ret==0).sum()),
                "equal_weight_mean_return_pct":float(ret.mean()),
                "median_return_pct":float(ret.median()),
                "best_return_pct":float(ret.max()),
                "worst_return_pct":float(ret.min()),
                "top10":ok.assign(_r=ret).nlargest(10,"_r")[["symbol","day_return_pct","day_max_drawdown_pct","day_events"]].to_dict("records"),
                "bottom10":ok.assign(_r=ret).nsmallest(10,"_r")[["symbol","day_return_pct","day_max_drawdown_pct","day_events"]].to_dict("records"),
            }
        else:
            summary["days"][day]={"symbols_total":len(symbols),"symbols_ok":0}
    (a.out/"SUMMARY.json").write_text(json.dumps(summary,indent=2,default=str))
    print(json.dumps(summary,indent=2,default=str))
if __name__=="__main__":main()
