from __future__ import annotations
import argparse, copy, json, sys
from pathlib import Path
import yaml
from common_data import load_rows, to_smallfish

p=argparse.ArgumentParser()
p.add_argument("--candidate",type=Path,required=True)
p.add_argument("--output",type=Path,required=True)
args=p.parse_args()
sys.path.insert(0,str(args.candidate/"src"))
import backtest as sf

config=yaml.safe_load((args.candidate/"config"/"default.yaml").read_text())
config["exchange"]="binance"
config["backtest_fees"]=True
# Standardized 14 bps round-trip stress: 5 bps maker entry + 9 bps taker exit.
config["backtest_maker_fee"]=0.0005
config["backtest_taker_fee"]=0.0009
config.setdefault("tick_sizes",{})["BTCUSDT"]=0.1
config.setdefault("min_qty",{})["BTCUSDT"]=0.001
config.setdefault("qty_step",{})["BTCUSDT"]=0.001
rows=to_smallfish(load_rows())
engine=sf.BacktestEngine(copy.deepcopy(config),initial_equity=1000.0,profile="aggressive")
engine.init_symbol("BTCUSDT")
warmup=60
tick=0.1
for i in range(warmup):
    sf.set_sim_time(rows[i]["ts"]+59999)
    prev=rows[i-1] if i else None
    book=sf.build_synthetic_book(rows[i],prev,tick)
    engine.books["BTCUSDT"].on_snapshot([[p,s] for p,s in book.bids],[[p,s] for p,s in book.asks])
    for t in sf.generate_synthetic_trades(rows[i],prev["close"] if prev else rows[i]["open"],"BTCUSDT"):
        engine.tapes["BTCUSDT"].add_trade(t)
last_day=None
for i in range(warmup,len(rows)):
    engine.process_candle("BTCUSDT",rows[i],rows[i-1])
    day=rows[i]["ts"]//86400000
    if day!=last_day:
        engine.equity_curve.append((rows[i]["ts"],engine.state.equity))
        engine.state.reset_daily()
        last_day=day
if engine.state.has_position("BTCUSDT"):
    sf.set_sim_time(rows[-1]["ts"]+59999)
    engine.force_close_all(rows[-1])
sf.clear_sim_time()
report=engine.report()
payload={"project":"azseza/smallfish_","commit":"e9a53c5a43f0dcb88d9ecd1b2130f7dac61b205f","dataset":"BTCUSDT 1m 2026-09-08..2026-09-15","profile":"aggressive","cost_model":"maker 5bps + taker 9bps","metrics":report,"methodology_note":"Native BacktestEngine; kline-derived synthetic book/tape."}
args.output.write_text(json.dumps(payload,indent=2,default=float),encoding="utf-8")
print("SMALLFISH_LOCAL_RESULT",json.dumps(payload,default=float))
