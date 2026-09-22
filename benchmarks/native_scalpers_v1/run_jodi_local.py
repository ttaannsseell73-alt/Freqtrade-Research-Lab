from __future__ import annotations
import argparse, json, os, random, sys
from pathlib import Path
import numpy as np
from common_data import load_rows, slice_days, resample

p=argparse.ArgumentParser()
p.add_argument("--candidate",type=Path,required=True)
p.add_argument("--output",type=Path,required=True)
args=p.parse_args()
args.candidate=args.candidate.resolve()
os.chdir(args.candidate)
sys.path.insert(0,str(args.candidate))
random.seed(1337); np.random.seed(1337)
try:
    import torch
    torch.manual_seed(1337)
except Exception:
    pass

# python-binance pings in Client.__init__; backtest itself does not need network once
# its kline fetcher is replaced with the immutable benchmark fixture.
from binance.client import Client
_orig_init=Client.__init__
def _no_ping_init(self,*a,**kw):
    kw["ping"]=False
    return _orig_init(self,*a,**kw)
Client.__init__=_no_ping_init

import config
config.INITIAL_BALANCE=1000.0
config.LEVERAGE=1
config.BACKTEST_LEVERAGE=1
# JODI applies this as round-trip cost at 1x. Stress to 14 bps total.
config.FEE_TAKER=0.0014
import main as app
from modes.backtest import BacktestRunner

all_rows=load_rows()
train_rows=slice_days(all_rows,0,5)
valid_rows=slice_days(all_rows,5,2)
active={"rows":train_rows}

def local_fetch(self, interval, start_ms, end_ms):
    mins={"1m":1,"5m":5,"15m":15}[interval]
    return resample(active["rows"],mins)
BacktestRunner._fetch_klines=local_fetch

# Clean any checked-in or previously generated training state.
for rel in ["ml/model/agent.pt","ml/model/replay.pkl","ml/model/params.json"]:
    path=args.candidate/rel
    if path.exists():
        path.unlink()

train_bot=app.TradingBot("BACKTEST")
BacktestRunner(train_bot).run(5)
train_stats=train_bot.sim_mode.stats()

active["rows"]=valid_rows
valid_bot=app.TradingBot("BACKTEST")
valid_bot._nn_agent.set_training(False)
BacktestRunner(valid_bot).run(2)
valid_stats=valid_bot.sim_mode.stats()
payload={"project":"JODI96/Trader","commit":"4384b050464741c588fdcd0579e1c0b7d43863a4","dataset":"BTCUSDT 1m 2026-09-08..2026-09-15","train_window":"first 5 days","validation_window":"last 2 days","leverage":1,"cost_model":"14bps round-trip stress at 1x","train_metrics":train_stats,"validation_metrics":valid_stats,"methodology_note":"Native BacktestRunner and bot pipeline; chronological train/validation split; Binance network client disabled only for data access."}
args.output.write_text(json.dumps(payload,indent=2,default=float),encoding="utf-8")
print("JODI_LOCAL_RESULT",json.dumps(payload,default=float))
