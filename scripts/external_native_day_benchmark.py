from __future__ import annotations
import argparse, json, math, sys
from pathlib import Path
import pandas as pd

def bounds(day):
    s=pd.Timestamp(day,tz="Europe/Istanbul").tz_convert("UTC")
    return s, s+pd.Timedelta(days=1)

def stats_from_series(series, day, native_start, event_count=0, extra=None):
    s,e=bounds(day)
    q=series.sort_index()
    if q.index.tz is None:
        q.index=q.index.tz_localize("UTC")
    else:
        q.index=q.index.tz_convert("UTC")
    pre=q[q.index < s]
    intra=q[(q.index >= s)&(q.index < e)]
    if pre.empty or intra.empty:
        return {"status":"NO_DAY_EQUITY","points_before":len(pre),"points_day":len(intra)}
    a=float(pre.iloc[-1]); b=float(intra.iloc[-1])
    vals=pd.concat([pd.Series([a],index=[s-pd.Timedelta(microseconds=1)]),intra])
    peak=vals.cummax()
    dd=((vals/peak)-1.0).min()
    out={
      "status":"OK","native_start_equity":float(native_start),
      "day_start_equity":a,"day_end_equity":b,
      "day_return_pct":(b/a-1.0)*100.0,
      "normalized_10k_pnl":10000.0*(b/a-1.0),
      "day_max_drawdown_pct":float(dd)*100.0,
      "day_events":int(event_count),
      "day_equity_points":int(len(intra)),
    }
    if extra: out.update(extra)
    return out

def run_v0nog(project,data,day,symbol_override=None):
    sys.path.insert(0,str(project))
    from src.data.loader import DataLoader
    from src.strategy.compression_breakout import CompressionBreakoutStrategy
    from src.backtest.engine import BacktestEngine
    from src.risk.manager import RiskManager
    candles=DataLoader(strict=True).load(data)
    st=CompressionBreakoutStrategy(
      compression_lookback=8,structure_lookback=24,
      compression_ratio_threshold=0.60,compression_max_atr_multiple=2.2,
      breakout_buffer_atr=0.05,breakout_min_range_atr=0.90,
      breakout_close_location_min=0.65,atr_expansion_lookback=5,
      atr_expansion_min_ratio=1.00,atr_stop_mult=2.0,r_multiple=2.5,atr_min_pct=0.003)
    eng=BacktestEngine(initial_equity=10000.0,fee_rate=0.001,slippage_pct=0.0005)
    risk=RiskManager(risk_pct_per_trade=0.01,max_risk_pct_per_trade=0.02,max_daily_loss_pct=0.05,cooldown_bars=5)
    r=eng.run(candles,st,risk)
    idx=pd.DatetimeIndex([c.timestamp for c in candles])
    ser=pd.Series(r.equity_curve,index=idx)
    s,e=bounds(day)
    trs=[t for t in r.trades if s <= pd.Timestamp(t.entry_time).tz_convert("UTC") < e]
    return stats_from_series(ser,day,10000.0,len(trs),{
      "native_total_trades":len(r.trades),
      "day_trade_wins":sum(1 for t in trs if t.net_pnl>0),
      "day_trade_losses":sum(1 for t in trs if t.net_pnl<=0),
      "strategy":"CompressionBreakoutStrategy","symbol":symbol_override or "BTCUSDT","timeframe":"15m"
    })

def run_theta(project,data,day,symbol_override=None):
    sys.path.insert(0,str(project))
    from spot_bot.backtest.backtest_spot import run_strategy_backtests
    from spot_bot.strategies.mean_reversion import MeanReversionStrategy
    df=pd.read_csv(data)
    df["timestamp"]=pd.to_datetime(df["timestamp"],utc=True)
    df=df.set_index("timestamp")
    res=run_strategy_backtests(df,MeanReversionStrategy(),fee_rate=0.0005,max_exposure=1.0,initial_equity=1000.0,slippage_bps=0.5)
    r=res["gated"]
    s,e=bounds(day)
    pos=r.positions.sort_index()
    if pos.index.tz is None: pos.index=pos.index.tz_localize("UTC")
    ch=pos.diff().abs().fillna(pos.abs())
    events=int(((ch>1e-12)&(ch.index>=s)&(ch.index<e)).sum())
    return stats_from_series(r.equity_curve,day,1000.0,events,{
      "strategy":"MeanReversionStrategy(gated)","symbol":symbol_override or "BTCUSDT","timeframe":"1h"
    })

def run_cryptobot(project,data,day,symbol_override=None):
    sys.path.insert(0,str(project/"src"))
    from cryptobot.config import load_settings
    from cryptobot.app.run_backtest import load_bars_from_csv
    from cryptobot.execution.backtest_broker import BacktestBroker
    from cryptobot.execution.fees import FeeModel
    from cryptobot.risk.manager import RiskManager
    from cryptobot.risk.rules import KillSwitchFile,MaxDailyLoss,MaxGrossExposurePct,MaxOrdersPerMinute,MaxPositionSizePct,OneOrderPerSymbolInFlight,RequireStopLoss,SymbolAllowList
    from cryptobot.strategy.registry import get_strategy
    from cryptobot.backtest.engine import BacktestEngine
    settings=load_settings(project/"config/backtest.yaml")
    if symbol_override:
        fmt = symbol_override[:-4] + "/USDT" if symbol_override.endswith("USDT") else symbol_override
        settings.run.market.symbols=[fmt]
        settings.run.risk.symbol_allow_list=[fmt]
    rc,fc,sc=settings.run.risk,settings.run.fees,settings.run.strategy
    symbol=settings.run.market.symbols[0]; tf=settings.run.market.timeframe
    bars=load_bars_from_csv(data,symbol,tf)
    fees=FeeModel(taker_bps=fc.taker_bps,maker_bps=fc.maker_bps,slippage_bps=fc.slippage_bps)
    broker=BacktestBroker(starting_cash=10000.0,fees=fees)
    rules=[SymbolAllowList(rc.symbol_allow_list),MaxOrdersPerMinute(rc.max_orders_per_minute),OneOrderPerSymbolInFlight()]
    if rc.require_stop_loss: rules.append(RequireStopLoss())
    rules += [MaxDailyLoss(max_loss_pct=rc.max_daily_loss_pct),MaxPositionSizePct(rc.max_position_pct),MaxGrossExposurePct(rc.max_gross_exposure_pct),KillSwitchFile(settings.env.kill_switch_file)]
    risk=RiskManager(rules)
    strategy=get_strategy(sc.name)(params=sc.params)
    r=BacktestEngine(strategy=strategy,broker=broker,risk=risk,bars=bars).run("bench")
    # curve[0] is pre-loop, curve[i+1] is after each bar, final item is synthetic final settlement
    idx=[bars[0].ts_open-pd.Timedelta(microseconds=1)]+[b.ts_open for b in bars]+[bars[-1].ts_open+pd.Timedelta(microseconds=1)]
    ser=pd.Series(r.equity_curve,index=pd.DatetimeIndex(idx))
    s,e=bounds(day)
    events=sum(1 for f in r.fills if s <= pd.Timestamp(f.ts).tz_convert("UTC") < e)
    return stats_from_series(ser,day,10000.0,events,{
      "native_total_trades":int(r.metrics.n_trades),"strategy":sc.name,"symbol":symbol,"timeframe":tf
    })

def run_rsi(project,data,day,symbol_override=None):
    sys.path.insert(0,str(project))
    import yaml
    from app.backtest.config_builder import build_backtest_config
    from app.backtest.engine.backtest_engine import BacktestEngine
    from app.trading.strategy.loader import STRATEGY_MAP
    base=yaml.safe_load((project/"config.yaml").read_text())
    strategy_name=base.get("strategy","rsi_wma_retest")
    symbol=(symbol_override[:-4] + "/USDT" if symbol_override and symbol_override.endswith("USDT") else (symbol_override or "PYTH/USDT")); tf="15m"
    cfg=build_backtest_config(symbol=symbol,timeframe=tf,strategy_name=strategy_name,initial_balance=float(base.get("backtest",{}).get("initial_balance",10000)))
    class TrackingEngine(BacktestEngine):
        def __init__(self,*a,**k):
            super().__init__(*a,**k); self.track=[]
        def _handle_candle_close(self,event):
            super()._handle_candle_close(event)
            ci=event.current_index
            if ci is None: return
            ts=self._full_df.index[ci]
            total=float(self.exchange.fetch_balance().get("total",{}).get("USDT",0))
            upnl=sum(float(p.get("unrealizedPnl",0)) for p in self.exchange.fetch_positions())
            self.track.append((pd.Timestamp(ts),total+upnl))
    eng=TrackingEngine(str(data),STRATEGY_MAP[strategy_name],cfg)
    r=eng.run()
    ser=pd.Series([v for _,v in eng.track],index=pd.DatetimeIndex([t for t,_ in eng.track]))
    s,e=bounds(day)
    events=0
    for tr in eng.exchange.trade_history:
        ts=tr.get("timestamp") or tr.get("time")
        if ts is None: continue
        t=pd.Timestamp(ts)
        if t.tzinfo is None: t=t.tz_localize("UTC")
        else: t=t.tz_convert("UTC")
        if s<=t<e: events+=1
    return stats_from_series(ser,day,float(cfg.get("backtest",{}).get("initial_balance",10000)),events,{
      "native_round_trips":len(r.get("round_trips",[])),"strategy":strategy_name,"symbol":symbol,"timeframe":tf
    })

def run_bino(project,data,day,symbol_override=None):
    sys.path.insert(0,str(project))
    from src.utils.config_loader import ConfigLoader
    from src.backtest.data_loader import DataLoader
    from src.backtest.engine import BacktestEngine
    cfg=ConfigLoader(str(project/"config/ZECUSDT_TEMPLATE.yaml"))
    cfg_all=cfg.get_all()
    active_symbol=symbol_override or cfg.get_symbol()
    if symbol_override:
        cfg_all["pair"]["symbol"]=active_symbol
        cfg_all["pair"]["base"]=active_symbol[:-4] if active_symbol.endswith("USDT") else active_symbol
        cfg_all["pair"]["quote"]="USDT"
    df=DataLoader(active_symbol,cfg.get_kline_interval()).load_from_csv(str(data))
    r=BacktestEngine(cfg_all).run(df)
    eq=r.equity_curve["equity"]
    s,e=bounds(day)
    events=0
    for t in r.trades:
        ts=pd.Timestamp(t.timestamp)
        if ts.tzinfo is None: ts=ts.tz_localize("UTC")
        else: ts=ts.tz_convert("UTC")
        if s<=ts<e: events+=1
    start=float(cfg_all.get("starting_cash_usdt",3000))
    return stats_from_series(eq,day,start,events,{
      "native_total_trade_records":len(r.trades),"strategy":"native score/risk engine","symbol":active_symbol,"timeframe":cfg.get_kline_interval()
    })

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--kind",required=True,choices=["v0nog","theta","cryptobot","rsi","bino"])
    ap.add_argument("--project",type=Path,required=True)
    ap.add_argument("--data",type=Path,required=True)
    ap.add_argument("--day",required=True)
    ap.add_argument("--symbol")
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    fn={"v0nog":run_v0nog,"theta":run_theta,"cryptobot":run_cryptobot,"rsi":run_rsi,"bino":run_bino}[a.kind]
    out=fn(a.project.resolve(),a.data.resolve(),a.day,a.symbol)
    out.update({"project_kind":a.kind,"day_local":a.day,"timezone":"Europe/Istanbul"})
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(out,indent=2,default=str),encoding="utf-8")
    print(json.dumps(out,indent=2,default=str))
if __name__=="__main__": main()
