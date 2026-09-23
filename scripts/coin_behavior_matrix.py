from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd


BASE_URL = "https://data.binance.vision/data/futures/um"
SEED_UNIVERSE = [
    "ETHUSDT","ZECUSDT","BTCUSDT","XRPUSDT","NEARUSDT","BCHUSDT","SOLUSDT","UNIUSDT",
    "TAKEUSDT","DOGEUSDT","MUBARAKUSDT","1000PEPEUSDT","HYPEUSDT","ARBUSDT","SUIUSDT",
    "BNBUSDT","ENAUSDT","TAOUSDT","NILUSDT","PENGUUSDT","AVAXUSDT","ADAUSDT","AKEUSDT",
    "ONEUSDT","WLDUSDT","龙虾USDT","AAVEUSDT","USELESSUSDT","FILUSDT","LINKUSDT","BRUSDT",
    "LITUSDT","LTCUSDT","TRUMPUSDT","ZROUSDT","PUMPUSDT","XLMUSDT","METUSDT","DASHUSDT",
    "SAGAUSDT","INJUSDT","ALLOUSDT","ONDOUSDT","TIAUSDT","1000BONKUSDT","APTUSDT",
    "XPLUSDT","ASTERUSDT","DOTUSDT","LSKUSDT","RAYSOLUSDT","BTWUSDT","ETCUSDT","OPUSDT",
    "VVVUSDT","HBARUSDT","FARTCOINUSDT","1000SHIBUSDT","ZAMAUSDT","XMRUSDT","FOLKSUSDT",
    "WIFUSDT","B2USDT","SUPERUSDT","FIGHTUSDT","ICPUSDT","VIRTUALUSDT","COTIUSDT",
    "ACEUSDT","FETUSDT",
]
COLS = [
    "open_time","open","high","low","close","volume","close_time","quote_volume",
    "trade_count","taker_buy_base","taker_buy_quote","ignore",
]
STRATEGY_FAMILY = {
    "liquidity_sweep_reclaim":"reversion",
    "breakout_retest":"trend",
    "compression_expansion":"trend",
    "range_mean_reversion":"reversion",
    "vwap_pullback":"trend",
    "structure_pullback":"trend",
    "failed_breakout_reversal":"reversion",
    "rolling_range_breakout":"trend",
    "impulse_pullback":"trend",
    "volatility_exhaustion":"reversion",
}


def _get(url: str, attempts: int = 4) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent":"Freqtrade-Research-Lab/1.0"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(req, timeout=40) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt + 1 == attempts:
                raise
        except Exception:
            if attempt + 1 == attempts:
                raise
        time.sleep(1.5 * (attempt + 1))
    return None


def _zip_frame(payload: bytes) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = [n for n in archive.namelist() if not n.endswith("/")]
        if not names:
            return pd.DataFrame()
        raw = archive.read(names[0])
    frame = pd.read_csv(io.BytesIO(raw), header=None, low_memory=False)
    if frame.empty:
        return frame
    if not str(frame.iloc[0, 0]).lstrip("-").isdigit():
        frame = frame.iloc[1:].reset_index(drop=True)
    if frame.shape[1] < 11:
        raise ValueError("Unexpected Binance kline column count")
    frame = frame.iloc[:, :12]
    frame.columns = COLS[: frame.shape[1]]
    for col in ["open_time","open","high","low","close","volume","quote_volume","trade_count"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open_time","open","high","low","close","volume"])
    unit = "us" if float(frame["open_time"].median()) > 100_000_000_000_000 else "ms"
    frame["time"] = pd.to_datetime(frame["open_time"].astype("int64"), unit=unit, utc=True)
    return frame[["time","open","high","low","close","volume","quote_volume","trade_count"]]


def _archive_urls(symbol: str, start: date, end: date) -> list[str]:
    # end is exclusive. Completed months use monthly archives; the end month uses daily files.
    urls: list[str] = []
    qdir = urllib.parse.quote(symbol, safe="")
    month = date(start.year, start.month, 1)
    end_month = date(end.year, end.month, 1)
    while month < end_month:
        ym = month.strftime("%Y-%m")
        fn = urllib.parse.quote(f"{symbol}-1m-{ym}.zip", safe="-_.")
        urls.append(f"{BASE_URL}/monthly/klines/{qdir}/1m/{fn}")
        month = date(month.year + (month.month == 12), 1 if month.month == 12 else month.month + 1, 1)
    day = end_month
    while day < end:
        ds = day.strftime("%Y-%m-%d")
        fn = urllib.parse.quote(f"{symbol}-1m-{ds}.zip", safe="-_.")
        urls.append(f"{BASE_URL}/daily/klines/{qdir}/1m/{fn}")
        day += timedelta(days=1)
    return urls


def download_symbol(symbol: str, start: pd.Timestamp, end: pd.Timestamp, cache_dir: Path) -> dict:
    frames = []
    missing = []
    for url in _archive_urls(symbol, start.date(), end.date()):
        payload = _get(url)
        if payload is None:
            missing.append(url.rsplit("/", 1)[-1])
            continue
        part = _zip_frame(payload)
        if not part.empty:
            frames.append(part)
    if not frames:
        return {"symbol":symbol,"ok":False,"error":"no_data","missing_archives":len(missing)}
    frame = pd.concat(frames, ignore_index=True)
    frame = frame.drop_duplicates("time").sort_values("time")
    frame = frame[(frame["time"] >= start) & (frame["time"] < end)].reset_index(drop=True)
    expected = int((end - start).total_seconds() // 60)
    coverage = len(frame) / expected if expected else 0.0
    if frame.empty:
        return {"symbol":symbol,"ok":False,"error":"empty_after_slice","missing_archives":len(missing)}
    daily_qv = frame.set_index("time")["quote_volume"].resample("1D").sum(min_count=1)
    median_daily_qv = float(daily_qv.median()) if not daily_qv.empty else 0.0
    path = cache_dir / f"{symbol}.parquet"
    frame.to_parquet(path, index=False)
    return {
        "symbol":symbol,"ok":True,"candles":len(frame),"coverage":coverage,
        "median_daily_quote_volume":median_daily_qv,
        "missing_archives":len(missing),
        "first_time":frame["time"].iloc[0].isoformat(),
        "last_time":frame["time"].iloc[-1].isoformat(),
        "cache_path":str(path),
    }


def resample_5m(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.set_index("time")
    out = x.resample("5min", label="left", closed="left").agg(
        open=("open","first"), high=("high","max"), low=("low","min"), close=("close","last"),
        volume=("volume","sum"), quote_volume=("quote_volume","sum"), trade_count=("trade_count","sum"),
    )
    out = out.dropna(subset=["open","high","low","close"]).reset_index()
    return out


def _base_features(frame: pd.DataFrame) -> dict[str, pd.Series]:
    o,h,l,c,v = [frame[x].astype(float) for x in ["open","high","low","close","volume"]]
    cr = (h-l).replace(0, np.nan)
    clv = ((c-l)/cr).clip(0,1)
    med_rng20 = ((h-l)/c.shift(1)).rolling(20, min_periods=20).median().shift(1)
    med_vol20 = v.rolling(20, min_periods=20).median().shift(1)
    return {"o":o,"h":h,"l":l,"c":c,"v":v,"cr":cr,"clv":clv,"med_rng20":med_rng20,"med_vol20":med_vol20}


def build_signals(frame: pd.DataFrame) -> dict[str, tuple[pd.Series,pd.Series]]:
    f = _base_features(frame)
    o,h,l,c,v,cr,clv = f["o"],f["h"],f["l"],f["c"],f["v"],f["cr"],f["clv"]
    med_rng20, med_vol20 = f["med_rng20"], f["med_vol20"]
    tradable = v > 0

    ph20 = h.rolling(20, min_periods=20).max().shift(1)
    pl20 = l.rolling(20, min_periods=20).min().shift(1)
    ph30 = h.rolling(30, min_periods=30).max().shift(1)
    pl30 = l.rolling(30, min_periods=30).min().shift(1)

    sweep = 0.0002
    s1l = (l <= pl20*(1-sweep)) & (c > pl20) & (clv >= .60) & tradable
    s1s = (h >= ph20*(1+sweep)) & (c < ph20) & (clv <= .40) & tradable

    prior_ph = h.rolling(20, min_periods=20).max().shift(2)
    prior_pl = l.rolling(20, min_periods=20).min().shift(2)
    level_l, level_s = prior_ph, prior_pl
    prev_break_l = c.shift(1) >= level_l*(1.0002)
    prev_break_s = c.shift(1) <= level_s*(0.9998)
    s2l = prev_break_l & (l <= level_l*1.0012) & (l >= level_l*.9988) & (c > level_l) & (clv >= .55)
    s2s = prev_break_s & (h >= level_s*.9988) & (h <= level_s*1.0012) & (c < level_s) & (clv <= .45)

    rh8 = h.rolling(8, min_periods=8).max()
    rl8 = l.rolling(8, min_periods=8).min()
    width8 = (rh8-rl8)/c
    base_width = width8.shift(9).rolling(50, min_periods=30).median()
    compressed = width8.shift(1) <= base_width*.70
    expanding = ((h-l)/c.shift(1)) >= med_rng20*1.40
    s3l = compressed & expanding & (c >= rh8.shift(1)*1.0002) & (clv >= .65) & tradable
    s3s = compressed & expanding & (c <= rl8.shift(1)*.9998) & (clv <= .35) & tradable

    width30 = ph30-pl30
    width30_bps = width30/c*10000
    range_ok = width30_bps.between(20, 500)
    s4l = range_ok & (l <= pl30 + width30*.10) & (c >= pl30 + width30*.25) & (clv >= .55)
    s4s = range_ok & (h >= ph30 - width30*.10) & (c <= ph30 - width30*.25) & (clv <= .45)

    typical = (h+l+c)/3
    pv = typical*v
    vwap30 = pv.rolling(30, min_periods=30).sum()/v.rolling(30, min_periods=30).sum()
    slope = vwap30/vwap30.shift(10)-1
    s5l = (slope > .0010) & (l <= vwap30*1.0010) & (c > vwap30) & (clv >= .55)
    s5s = (slope < -.0010) & (h >= vwap30*.9990) & (c < vwap30) & (clv <= .45)

    mid20 = (ph20+pl20)/2
    higher = (ph20 > ph20.shift(10)*1.0010) & (pl20 > pl20.shift(10)*1.0010)
    lower = (ph20 < ph20.shift(10)*.9990) & (pl20 < pl20.shift(10)*.9990)
    s6l = higher & (l <= mid20) & (c > mid20) & (c > o) & (clv >= .55)
    s6s = lower & (h >= mid20) & (c < mid20) & (c < o) & (clv <= .45)

    prev_inside_high = h.rolling(20, min_periods=20).max().shift(2)
    prev_inside_low = l.rolling(20, min_periods=20).min().shift(2)
    prev_up = c.shift(1) >= prev_inside_high*1.0002
    prev_dn = c.shift(1) <= prev_inside_low*.9998
    s7s = prev_up & (c < prev_inside_high) & (clv <= .45)
    s7l = prev_dn & (c > prev_inside_low) & (clv >= .55)

    vr = v/med_vol20.replace(0,np.nan)
    curr_norm_rng = (h-l)/c.shift(1)
    s8l = (c >= ph30*1.0003) & (clv >= .70) & (curr_norm_rng >= med_rng20*1.20) & (vr >= 1.15)
    s8s = (c <= pl30*.9997) & (clv <= .30) & (curr_norm_rng >= med_rng20*1.20) & (vr >= 1.15)

    prev_rng = (h-l).shift(1)
    prev_med_rng_abs = (h-l).rolling(20, min_periods=20).median().shift(2)
    impulse_up = (prev_rng >= prev_med_rng_abs*1.80) & (c.shift(1)>o.shift(1)) & (clv.shift(1)>=.80)
    impulse_dn = (prev_rng >= prev_med_rng_abs*1.80) & (c.shift(1)<o.shift(1)) & (clv.shift(1)<=.20)
    prev_mid = (o.shift(1)+c.shift(1))/2
    s9l = impulse_up & (l <= prev_mid) & (l >= l.shift(1)) & (c > prev_mid) & (clv >= .55)
    s9s = impulse_dn & (h >= prev_mid) & (h <= h.shift(1)) & (c < prev_mid) & (clv <= .45)

    body_top = np.maximum(o,c)
    body_bot = np.minimum(o,c)
    upper_wick = (h-body_top)/cr
    lower_wick = (body_bot-l)/cr
    ret10 = c.shift(1)/c.shift(11)-1
    extreme = curr_norm_rng >= med_rng20*2.20
    high_vol = vr >= 1.30
    s10l = (ret10 <= -.005) & extreme & high_vol & (lower_wick >= .40) & (clv >= .55)
    s10s = (ret10 >= .005) & extreme & high_vol & (upper_wick >= .40) & (clv <= .45)

    raw = {
        "liquidity_sweep_reclaim":(s1l,s1s),
        "breakout_retest":(s2l,s2s),
        "compression_expansion":(s3l,s3s),
        "range_mean_reversion":(s4l,s4s),
        "vwap_pullback":(s5l,s5s),
        "structure_pullback":(s6l,s6s),
        "failed_breakout_reversal":(s7l,s7s),
        "rolling_range_breakout":(s8l,s8s),
        "impulse_pullback":(s9l,s9s),
        "volatility_exhaustion":(s10l,s10s),
    }
    out = {}
    for name,(lo,sh) in raw.items():
        lo = lo.fillna(False).astype(bool)
        sh = sh.fillna(False).astype(bool)
        both = lo & sh
        out[name] = (lo & ~both, sh & ~both)
    return out


def outcome_arrays(frame: pd.DataFrame, horizon_bars: int) -> dict[str,np.ndarray]:
    entry = frame["open"].shift(-1).astype(float)
    exitp = frame["close"].shift(-horizon_bars).astype(float)
    long_gross = (exitp/entry-1.0)*10000
    short_gross = (entry-exitp)/entry*10000
    highs = pd.concat([frame["high"].shift(-k) for k in range(1,horizon_bars+1)], axis=1).max(axis=1)
    lows = pd.concat([frame["low"].shift(-k) for k in range(1,horizon_bars+1)], axis=1).min(axis=1)
    long_mfe = (highs/entry-1.0)*10000
    long_mae = (lows/entry-1.0)*10000
    short_mfe = (entry-lows)/entry*10000
    short_mae = (entry-highs)/entry*10000
    return {k:v.to_numpy(float) for k,v in {
        "long_gross":long_gross,"short_gross":short_gross,"long_mfe":long_mfe,
        "long_mae":long_mae,"short_mfe":short_mfe,"short_mae":short_mae
    }.items()}


def non_overlap(indices: np.ndarray, horizon_bars: int) -> np.ndarray:
    if len(indices) == 0:
        return indices
    keep = []
    next_allowed = -1
    for idx in indices:
        if idx >= next_allowed:
            keep.append(int(idx))
            next_allowed = int(idx) + horizon_bars + 1
    return np.asarray(keep, dtype=int)


def metrics(values: np.ndarray, mfe: np.ndarray, mae: np.ndarray, cost_bps: float) -> dict:
    ok = np.isfinite(values)
    values,mfe,mae = values[ok],mfe[ok],mae[ok]
    if len(values) == 0:
        return {"trades":0,"expectancy_bps":math.nan,"win_rate":math.nan,"profit_factor":math.nan,
                "net_sum_bps":0.0,"median_bps":math.nan,"max_event_dd_bps":math.nan,
                "avg_mfe_bps":math.nan,"avg_mae_bps":math.nan}
    net = values-cost_bps
    wins = net[net>0]
    losses = net[net<0]
    pf = float(wins.sum()/abs(losses.sum())) if len(losses) and abs(losses.sum())>1e-12 else (math.inf if len(wins) else math.nan)
    curve = np.cumsum(net)
    peak = np.maximum.accumulate(np.r_[0.0,curve])
    dd = peak[1:]-curve
    return {
        "trades":int(len(net)),"expectancy_bps":float(net.mean()),"win_rate":float((net>0).mean()),
        "profit_factor":pf,"net_sum_bps":float(net.sum()),"median_bps":float(np.median(net)),
        "max_event_dd_bps":float(dd.max()) if len(dd) else 0.0,
        "avg_mfe_bps":float(np.nanmean(mfe)),"avg_mae_bps":float(np.nanmean(mae)),
    }


def split_name(ts: pd.Timestamp, train_end: pd.Timestamp, val_end: pd.Timestamp) -> str:
    if ts < train_end:
        return "train"
    if ts < val_end:
        return "validation"
    return "holdout"


def evaluate_symbol(symbol: str, frame1m: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    rows = []
    train_end = start + (end-start)*0.50
    val_end = start + (end-start)*0.75
    for timeframe, frame in [("1m",frame1m),("5m",resample_5m(frame1m))]:
        sigs = build_signals(frame)
        minutes_per_bar = 1 if timeframe=="1m" else 5
        for horizon_min in [5,15,30]:
            horizon_bars = max(1, horizon_min//minutes_per_bar)
            outcomes = outcome_arrays(frame,horizon_bars)
            times = frame["time"].to_numpy()
            for strategy,(lo,sh) in sigs.items():
                li = np.flatnonzero(lo.to_numpy())
                si = np.flatnonzero(sh.to_numpy())
                idx = np.r_[li,si]
                side = np.r_[np.ones(len(li),dtype=int),-np.ones(len(si),dtype=int)]
                if len(idx):
                    order = np.argsort(idx)
                    idx,side = idx[order],side[order]
                    selected = non_overlap(idx,horizon_bars)
                    side_map = {int(i):int(s) for i,s in zip(idx,side)}
                    idx = selected
                    side = np.asarray([side_map[int(i)] for i in idx],dtype=int)
                gross = np.where(side==1,outcomes["long_gross"][idx],outcomes["short_gross"][idx]) if len(idx) else np.array([])
                mfe = np.where(side==1,outcomes["long_mfe"][idx],outcomes["short_mfe"][idx]) if len(idx) else np.array([])
                mae = np.where(side==1,outcomes["long_mae"][idx],outcomes["short_mae"][idx]) if len(idx) else np.array([])
                event_times = pd.to_datetime(times[idx],utc=True) if len(idx) else pd.DatetimeIndex([])
                for split in ["train","validation","holdout"]:
                    mask = np.array([split_name(t,train_end,val_end)==split for t in event_times],dtype=bool)
                    for cost in [6.0,10.0,15.0]:
                        m = metrics(gross[mask],mfe[mask],mae[mask],cost)
                        rows.append({
                            "symbol":symbol,"timeframe":timeframe,"strategy":strategy,
                            "family":STRATEGY_FAMILY[strategy],"horizon_min":horizon_min,
                            "split":split,"cost_bps":cost,**m,
                            "long_events":int(((side==1)&mask).sum()) if len(side) else 0,
                            "short_events":int(((side==-1)&mask).sum()) if len(side) else 0,
                        })
    return rows


def robust_analysis(results: pd.DataFrame) -> tuple[pd.DataFrame,pd.DataFrame]:
    r10 = results[results["cost_bps"]==10.0].copy()
    key = ["symbol","timeframe","strategy","family","horizon_min"]
    wide = r10.pivot_table(index=key,columns="split",values=["trades","expectancy_bps","profit_factor"],aggfunc="first")
    wide.columns = [f"{a}_{b}" for a,b in wide.columns]
    wide = wide.reset_index()
    h15 = results[(results["split"]=="holdout")&(results["cost_bps"]==15.0)][key+["expectancy_bps"]].rename(columns={"expectancy_bps":"holdout_expectancy_15bps"})
    wide = wide.merge(h15,on=key,how="left")
    wide["base_pass"] = (
        (wide["trades_validation"]>=25)&(wide["trades_holdout"]>=25)&
        (wide["expectancy_bps_validation"]>0)&(wide["expectancy_bps_holdout"]>0)&
        (wide["profit_factor_validation"]>=1.05)&(wide["profit_factor_holdout"]>=1.05)&
        (wide["holdout_expectancy_15bps"]>=0)
    )
    stable = wide.groupby(["symbol","timeframe","strategy"])["base_pass"].transform("sum")
    wide["neighbor_horizon_passes"] = stable
    cross = wide[wide["base_pass"]].groupby(["timeframe","strategy"])["symbol"].nunique().rename("cross_coin_support")
    wide = wide.merge(cross,on=["timeframe","strategy"],how="left")
    wide["cross_coin_support"] = wide["cross_coin_support"].fillna(0).astype(int)
    stable_train = (wide["expectancy_bps_train"]>0) & (wide["profit_factor_train"]>=1.0)
    horizon_stable = wide["neighbor_horizon_passes"]>=2
    wide["regime_dependent"] = wide["base_pass"] & horizon_stable & (~stable_train) & (wide["cross_coin_support"]>=3)
    wide["robust"] = wide["base_pass"] & horizon_stable & stable_train
    wide["research_score"] = np.minimum(wide["expectancy_bps_validation"],wide["expectancy_bps_holdout"])
    candidates = wide[wide["robust"]].sort_values(["research_score","cross_coin_support"],ascending=False)
    return wide,candidates


def write_reports(outdir: Path, universe: pd.DataFrame, results: pd.DataFrame) -> None:
    wide,candidates = robust_analysis(results)
    results.to_csv(outdir/"DETAILED_RESULTS.csv",index=False)
    candidates.to_csv(outdir/"ROBUST_CANDIDATES.csv",index=False)

    best_h = wide.sort_values("research_score",ascending=False).drop_duplicates(["symbol","timeframe","strategy"])
    matrix = best_h.pivot(index="symbol",columns=["timeframe","strategy"],values="research_score")
    matrix.columns = [f"{tf}__{st}" for tf,st in matrix.columns]
    matrix.reset_index().to_csv(outdir/"MASTER_MATRIX.csv",index=False)

    top_rows=[]
    for (symbol,tf), grp in best_h.groupby(["symbol","timeframe"]):
        g=grp.sort_values(["robust","research_score"],ascending=[False,False]).head(3)
        for rank,(_,row) in enumerate(g.iterrows(),1):
            top_rows.append({
                "symbol":symbol,"timeframe":tf,"rank":rank,"strategy":row["strategy"],
                "family":row["family"],"horizon_min":int(row["horizon_min"]),
                "research_score_bps":row["research_score"],"robust":bool(row["robust"]),
                "regime_dependent":bool(row["regime_dependent"]),
                "validation_expectancy_bps":row["expectancy_bps_validation"],
                "holdout_expectancy_bps":row["expectancy_bps_holdout"],
                "holdout_15bps_expectancy_bps":row["holdout_expectancy_15bps"],
                "validation_trades":int(row["trades_validation"]),
                "holdout_trades":int(row["trades_holdout"]),
            })
    top3=pd.DataFrame(top_rows)
    top3.to_csv(outdir/"TOP3_BY_COIN.csv",index=False)

    play=[]
    play.append("# Coin Behavior Playbook\n")
    play.append("Discovery only: next-open entries, fixed real-time horizons, non-overlapping events, no leverage. Robust means validation + untouched holdout survive 10 bps, 15 bps holdout stress, sample gates, and neighboring-horizon stability.\n")
    for symbol in universe["symbol"]:
        play.append(f"\n## {symbol}\n")
        for tf in ["1m","5m"]:
            g=top3[(top3.symbol==symbol)&(top3.timeframe==tf)]
            robust_g=g[g.robust]
            if robust_g.empty:
                play.append(f"- **{tf}: NO_STABLE_EDGE** — no tested family cleared the robust discovery gate.\n")
            else:
                fam=robust_g.iloc[0]["family"]
                names=", ".join(f"{r.strategy} ({int(r.horizon_min)}m, {r.research_score_bps:.2f} bps worst-OOS)" for r in robust_g.itertuples())
                play.append(f"- **{tf}: {fam.upper()} bias** — {names}.\n")
    (outdir/"COIN_PLAYBOOK.md").write_text("".join(play),encoding="utf-8")

    failures=[]
    failures.append("# Failure Audit\n\n")
    failures.append("Combinations are rejected rather than rescued by parameter tuning on holdout.\n\n")
    total=len(wide); robust_n=int(wide["robust"].sum())
    failures.append(f"- Tested strategy/timeframe/horizon combinations: {total}\n- Robust discovery combinations: {robust_n}\n- Rejected: {total-robust_n}\n")
    failures.append(f"- Insufficient validation samples (<25): {int((wide['trades_validation']<25).sum())}\n")
    failures.append(f"- Insufficient holdout samples (<25): {int((wide['trades_holdout']<25).sum())}\n")
    failures.append(f"- Validation positive but holdout non-positive: {int(((wide['expectancy_bps_validation']>0)&(wide['expectancy_bps_holdout']<=0)).sum())}\n")
    failures.append(f"- 10 bps holdout positive but 15 bps stress non-positive: {int(((wide['expectancy_bps_holdout']>0)&(wide['holdout_expectancy_15bps']<=0)).sum())}\n")
    (outdir/"FAILURES.md").write_text("".join(failures),encoding="utf-8")

    regime = wide[wide["regime_dependent"]].sort_values(["research_score","cross_coin_support"],ascending=False)
    regime.to_csv(outdir/"REGIME_DEPENDENT.csv",index=False)
    regime_md=["# Regime-Dependent Candidates\n\n",
               "These pass current OOS and friction gates but fail the positive-train stability requirement. They are not stable-edge candidates.\n\n"]
    if regime.empty:
        regime_md.append("**None.**\n")
    else:
        regime_md.append("| Coin | TF | Strategy | Horizon | Train bps | Val bps | Holdout bps | Holdout @15bps |\n|---|---|---|---:|---:|---:|---:|---:|\n")
        for r in regime.head(100).itertuples():
            regime_md.append(f"| {r.symbol} | {r.timeframe} | {r.strategy} | {int(r.horizon_min)}m | {r.expectancy_bps_train:.2f} | {r.expectancy_bps_validation:.2f} | {r.expectancy_bps_holdout:.2f} | {r.holdout_expectancy_15bps:.2f} |\n")
    (outdir/"REGIME_DEPENDENT.md").write_text("".join(regime_md),encoding="utf-8")

    robust_coin_count=int(candidates["symbol"].nunique()) if not candidates.empty else 0
    regime_coin_count=int(regime["symbol"].nunique()) if not regime.empty else 0
    summary={
        "status":"COMPLETE","universe_size":int(len(universe)),"tested_rows":int(len(results)),
        "robust_combinations":int(len(candidates)),"coins_with_robust_setup":robust_coin_count,
        "regime_dependent_combinations":int(len(regime)),"coins_with_regime_dependent_setup":regime_coin_count,
        "coins_without_robust_setup":int(len(universe)-robust_coin_count),
        "selection_note":"Research discovery classification, not live-trading approval.",
    }
    (outdir/"SUMMARY.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")

    md=["# Robust Candidates\n\n"]
    md.append("A stable-edge candidate must be positive in train, validation, and untouched holdout at 10 bps; remain positive on holdout at 15 bps; have >=25 independent events in validation and holdout; and pass at least two neighboring holding horizons. Negative-train setups are reported separately as regime-dependent and are never labeled robust.\n\n")
    if candidates.empty:
        md.append("**No robust candidates.**\n")
    else:
        md.append("| Coin | TF | Strategy | Horizon | Train bps | Val bps | Holdout bps | Holdout @15bps | Cross-coin support |\n|---|---|---|---:|---:|---:|---:|---:|---:|\n")
        for r in candidates.head(100).itertuples():
            md.append(f"| {r.symbol} | {r.timeframe} | {r.strategy} | {int(r.horizon_min)}m | {r.expectancy_bps_train:.2f} | {r.expectancy_bps_validation:.2f} | {r.expectancy_bps_holdout:.2f} | {r.holdout_expectancy_15bps:.2f} | {int(r.cross_coin_support)} |\n")
    (outdir/"ROBUST_CANDIDATES.md").write_text("".join(md),encoding="utf-8")

    for path in outdir.iterdir():
        if path.is_file() and path.name!="SHA256SUMS.txt":
            pass
    hashes=[]
    for path in sorted(outdir.iterdir()):
        if path.is_file() and path.name!="SHA256SUMS.txt":
            hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (outdir/"SHA256SUMS.txt").write_text("\n".join(hashes)+"\n",encoding="utf-8")


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--start",default="2026-06-25")
    p.add_argument("--end",default="2026-09-23")
    p.add_argument("--top-n",type=int,default=50)
    p.add_argument("--seed-limit",type=int,default=70)
    p.add_argument("--download-workers",type=int,default=8)
    p.add_argument("--min-coverage",type=float,default=.95)
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args()

    start=pd.Timestamp(args.start,tz="UTC")
    end=pd.Timestamp(args.end,tz="UTC")
    if end<=start:
        raise SystemExit("end must be after start")
    outdir=args.output_dir
    outdir.mkdir(parents=True,exist_ok=True)
    cache=outdir/"cache"
    cache.mkdir(exist_ok=True)

    seeds=SEED_UNIVERSE[:args.seed_limit]
    audits=[]
    with ThreadPoolExecutor(max_workers=args.download_workers) as pool:
        futures={pool.submit(download_symbol,s,start,end,cache):s for s in seeds}
        for fut in as_completed(futures):
            symbol=futures[fut]
            try:
                row=fut.result()
            except Exception as exc:
                row={"symbol":symbol,"ok":False,"error":repr(exc)}
            audits.append(row)
            print(json.dumps(row,ensure_ascii=False),flush=True)

    audit=pd.DataFrame(audits)
    audit.to_csv(outdir/"DATA_AUDIT.csv",index=False)
    good=audit[(audit["ok"]==True)&(audit["coverage"]>=args.min_coverage)].copy()
    good=good.sort_values(["median_daily_quote_volume","coverage"],ascending=False)
    if len(good)<args.top_n:
        raise SystemExit(f"Only {len(good)} symbols meet coverage gate; need {args.top_n}")
    universe=good.head(args.top_n).reset_index(drop=True)
    universe.insert(0,"rank_90d_liquidity",np.arange(1,len(universe)+1))
    universe.to_csv(outdir/"UNIVERSE_50.csv",index=False)

    all_rows=[]
    for n,row in enumerate(universe.itertuples(),1):
        print(f"[{n}/{len(universe)}] evaluate {row.symbol}",flush=True)
        frame=pd.read_parquet(row.cache_path)
        frame["time"]=pd.to_datetime(frame["time"],utc=True)
        all_rows.extend(evaluate_symbol(row.symbol,frame,start,end))
    results=pd.DataFrame(all_rows)
    write_reports(outdir,universe,results)

    manifest={
        "system_id":"coin-behavior-matrix-v1",
        "created_at":datetime.now(timezone.utc).isoformat(),
        "start":start.isoformat(),"end_exclusive":end.isoformat(),
        "seed_universe":seeds,"selection":"top 50 by 90d median daily quote volume after >=95% 1m coverage",
        "timeframes":["1m","5m"],"strategies":list(STRATEGY_FAMILY),
        "horizons_minutes":[5,15,30],"round_trip_cost_bps":[6,10,15],
        "entry_rule":"signal on closed candle; entry next candle open",
        "event_sampling":"greedy non-overlapping events per strategy and horizon",
        "split":"chronological 50% train / 25% validation / 25% untouched holdout",
        "limitations":[
            "Discovery/event study, not a live execution backtest.",
            "Fixed-horizon exit; stop/TP/order-book queue/funding are not modeled.",
            "15 bps stress is a friction sensitivity test, not a guarantee of realized slippage.",
            "Universe seed is current-activity biased, then stabilized by 90d median quote volume.",
        ],
    }
    (outdir/"RUN_MANIFEST.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False),encoding="utf-8")
    # refresh checksums after manifest
    hashes=[]
    for path in sorted(outdir.iterdir()):
        if path.is_file() and path.name!="SHA256SUMS.txt":
            hashes.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (outdir/"SHA256SUMS.txt").write_text("\n".join(hashes)+"\n",encoding="utf-8")
    print((outdir/"SUMMARY.json").read_text(),flush=True)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
