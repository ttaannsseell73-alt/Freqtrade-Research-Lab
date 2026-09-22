from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from common_data import load_rows, to_frame

p=argparse.ArgumentParser()
p.add_argument("--candidate",type=Path,required=True)
p.add_argument("--output",type=Path,required=True)
args=p.parse_args()
sys.path.insert(0,str(args.candidate))
from config.settings import BotConfig
from core.backtester import Backtester

cfg=BotConfig()
cfg.exchange.name="binance"
cfg.scalping.symbols=["BTC/USDT:USDT"]
cfg.scalping.timeframe="1m"
cfg.scalping.initial_capital=1000.0
cfg.scalping.leverage=1
cfg.strategy.active_strategy="combined"
cfg.backtest.initial_balance=1000.0
# Engine charges commission on entry + exit and slippage on exit.
cfg.backtest.commission_pct=0.0005
cfg.backtest.slippage_pct=0.0004
bt=Backtester(cfg)
metrics=bt.run(to_frame(load_rows()),symbol="BTC/USDT:USDT")
payload={"project":"ROMEROPS7/ultra-scalping-bot","commit":"b47351ff8963d8e9f13aa91be91f8cbaf28025b9","dataset":"BTCUSDT 1m 2026-09-08..2026-09-15","strategy":"combined","leverage":1,"cost_model":"5bps entry commission + 5bps exit commission + 4bps exit slippage","metrics":metrics,"methodology_note":"Native core Backtester; bypasses broken CLI DataDownloader constructor only."}
args.output.write_text(json.dumps(payload,indent=2,default=float),encoding="utf-8")
print("ULTRA_LOCAL_RESULT",json.dumps(payload,default=float))
