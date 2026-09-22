from __future__ import annotations
import argparse, glob, json, sys
from pathlib import Path

p=argparse.ArgumentParser()
p.add_argument("--candidate",type=Path,required=True)
p.add_argument("--output",type=Path,required=True)
args=p.parse_args()
sys.path.insert(0,str(args.candidate))

from bot.backtester import Backtester
from bot.signal_engine import CandleData

ROOT=Path(__file__).resolve().parents[2]
DATA=ROOT/"benchmarks"/"native_scalpers_v1"/"data"

def load_tf(tf:str):
    rows=[]
    pattern=DATA/f"BTCUSDT_{tf}_20260816_20260915_part*.json"
    for path in sorted(glob.glob(str(pattern))):
        rows.extend(json.loads(Path(path).read_text())["rows"])
    rows=sorted({int(r[0]):r for r in rows}.values(),key=lambda r:int(r[0]))
    return [CandleData(open=float(r[1]),high=float(r[2]),low=float(r[3]),close=float(r[4]),volume=float(r[5])) for r in rows]

five=load_tf("5m")
four=load_tf("4h")
daily=load_tf("1d")
bt=Backtester(
    symbol="BTC/USDT:USDT",
    five_min_candles=five,
    four_hour_candles=four,
    daily_candles=daily,
    initial_capital=1000.0,
    risk_per_trade=0.01,
    check_fvg=False,
    check_order_block=False,
)
native=bt.run()

# The project backtester omits fees/slippage. Apply the same 14 bps round-trip
# stress to every completed trade and rebuild the equity/PF/DD from raw trades.
equity=1000.0
peak=equity
max_dd=0.0
gross_profit=0.0
gross_loss=0.0
wins=0
losses=0
adjusted_trade_returns=[]
for t in native.trades:
    adj_price_pct=float(t.pnl_pct)-0.14
    sl_pct=abs(float(t.entry_price)-float(t.stop_loss))/float(t.entry_price)*100.0 if t.entry_price else 0.0
    risk_amount=equity*0.01
    pnl_usdt=risk_amount*(adj_price_pct/sl_pct) if sl_pct>0 else equity*(adj_price_pct/100.0)
    equity=max(0.0,equity+pnl_usdt)
    peak=max(peak,equity)
    dd=(peak-equity)/peak if peak>0 else 0.0
    max_dd=max(max_dd,dd)
    adjusted_trade_returns.append(adj_price_pct)
    if pnl_usdt>0:
        wins+=1; gross_profit+=pnl_usdt
    else:
        losses+=1; gross_loss+=-pnl_usdt
pf=gross_profit/gross_loss if gross_loss>0 else (999.0 if gross_profit>0 else 0.0)
payload={
    "project":"kishore446/360-Crypto-Eye-Scalping-",
    "commit":"46d1f154077c14007c4892cb3dc86394c8fd7cb6",
    "dataset":"BTCUSDT 5m+4h+1d 2026-08-16..2026-09-15",
    "native_metrics":{
        "trades":native.total_trades,
        "win_rate":native.win_rate,
        "profit_factor":native.profit_factor,
        "sharpe":native.sharpe_ratio,
        "max_drawdown_pct":native.max_drawdown_pct,
        "final_equity":native.final_equity,
    },
    "cost_adjusted_14bps":{
        "trades":len(native.trades),
        "wins":wins,"losses":losses,
        "win_rate":wins/len(native.trades) if native.trades else 0.0,
        "profit_factor":pf,
        "max_drawdown_pct":max_dd*100.0,
        "final_equity":equity,
        "return_pct":(equity/1000.0-1.0)*100.0,
    },
    "methodology_note":"Native Backtester + real 7-gate confluence engine. Project omits trading costs, so 14bps round-trip stress is applied post-trade and equity/PF/DD are recomputed."
}
args.output.write_text(json.dumps(payload,indent=2),encoding="utf-8")
print("EYE360_LOCAL_RESULT",json.dumps(payload))
