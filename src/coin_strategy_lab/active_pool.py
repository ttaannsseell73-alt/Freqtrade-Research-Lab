from __future__ import annotations

import io
import json
import re
from pathlib import Path
from zipfile import ZipFile

import pandas as pd


ACTIVE_MAX_DRAWDOWN = 0.65
ACTIVE_MIN_PROFIT_FACTOR = 1.35
ACTIVE_MIN_EXPECTANCY_BPS = 10.0

CORE_MAX_DRAWDOWN = 0.45
CORE_MIN_PROFIT_FACTOR = 1.50
CORE_MIN_EXPECTANCY_BPS = 20.0

COMMON_START = pd.Timestamp("2025-09-24", tz="UTC")
COMMON_END = pd.Timestamp("2026-09-24", tz="UTC")
EXTRA_SLIPPAGE_ROUND_TRIP = 0.0005


def build_active_pool(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "symbol","pair","timeframe","strategy_id","strategy_class","status",
        "sample_ok","trades","expectancy_bps","profit_factor",
        "max_drawdown_compounded","both_directions_positive",
        "long_expectancy","short_expectancy","research_confidence_score",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    out = frame.copy()
    out["drawdown_abs"] = out["max_drawdown_compounded"].abs()
    positive_direction = (
        (out["long_expectancy"] > 0.0) | (out["short_expectancy"] > 0.0)
    )
    active = out[
        out["status"].eq("EXECUTION_PASS")
        & out["sample_ok"].astype(bool)
        & (out["drawdown_abs"] <= ACTIVE_MAX_DRAWDOWN)
        & (out["profit_factor"] >= ACTIVE_MIN_PROFIT_FACTOR)
        & (out["expectancy_bps"] >= ACTIVE_MIN_EXPECTANCY_BPS)
        & positive_direction
    ].copy()

    core = (
        (active["drawdown_abs"] <= CORE_MAX_DRAWDOWN)
        & (active["profit_factor"] >= CORE_MIN_PROFIT_FACTOR)
        & (active["expectancy_bps"] >= CORE_MIN_EXPECTANCY_BPS)
        & active["both_directions_positive"].astype(bool)
    )
    active["pool_tier"] = "ACTIVE"
    active.loc[core, "pool_tier"] = "CORE"

    both = (active["long_expectancy"] > 0.0) & (active["short_expectancy"] > 0.0)
    long_only = (active["long_expectancy"] > 0.0) & ~both
    short_only = (active["short_expectancy"] > 0.0) & ~both
    active["paper_direction"] = "NONE"
    active.loc[both, "paper_direction"] = "BOTH"
    active.loc[long_only, "paper_direction"] = "LONG_ONLY"
    active.loc[short_only, "paper_direction"] = "SHORT_ONLY"

    active["paper_weight"] = 0.02
    active.loc[active["pool_tier"].eq("CORE"), "paper_weight"] = 0.03
    active.loc[~active["paper_direction"].eq("BOTH"), "paper_weight"] = 0.015

    dd_component = (1.0 - active["drawdown_abs"] / ACTIVE_MAX_DRAWDOWN).clip(0.0, 1.0)
    pf_component = ((active["profit_factor"] - 1.0) / 1.5).clip(0.0, 1.0)
    exp_component = (active["expectancy_bps"] / 250.0).clip(0.0, 1.0)
    research_component = (active["research_confidence_score"] / 100.0).clip(0.0, 1.0)
    active["active_score"] = 100.0 * (
        0.40 * dd_component
        + 0.25 * pf_component
        + 0.20 * exp_component
        + 0.15 * research_component
    )

    tier_rank = active["pool_tier"].map({"CORE": 2, "ACTIVE": 1})
    active["_rank"] = tier_rank
    return (
        active.sort_values(
            ["_rank","active_score","profit_factor","expectancy_bps"],
            ascending=[False,False,False,False],
        )
        .drop(columns=["_rank"])
        .reset_index(drop=True)
    )


def _load_backtest_trades(artifact_root: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for path in artifact_root.rglob("*.zip"):
        if "backtest" not in path.parts:
            continue
        try:
            with ZipFile(path) as archive:
                result_names = [
                    n for n in archive.namelist()
                    if n.endswith(".json") and "_config" not in n
                ]
                if not result_names:
                    continue
                payload = json.loads(archive.read(result_names[0]))
                if "strategy" not in payload or not payload["strategy"]:
                    continue
                strategy_class = next(iter(payload["strategy"]))
                for trade in payload["strategy"][strategy_class].get("trades", []):
                    row = dict(trade)
                    row["strategy_class"] = strategy_class
                    rows.append(row)
        except Exception:
            continue
    return pd.DataFrame(rows)


def simulate_historical_portfolio(
    active: pd.DataFrame,
    artifact_root: Path,
    start: pd.Timestamp = COMMON_START,
    end: pd.Timestamp = COMMON_END,
) -> tuple[pd.DataFrame, dict]:
    trades = _load_backtest_trades(artifact_root)
    if trades.empty:
        raise RuntimeError("No Freqtrade backtest trades found")

    lookup = active.set_index(["pair","strategy_class"])
    selected: list[dict] = []

    for trade in trades.itertuples(index=False):
        key = (str(trade.pair), str(trade.strategy_class))
        if key not in lookup.index:
            continue
        setup = lookup.loc[key]
        opened = pd.Timestamp(trade.open_date)
        closed = pd.Timestamp(trade.close_date)
        if opened < start or closed >= end:
            continue

        direction = str(setup.paper_direction)
        is_short = bool(trade.is_short)
        if direction == "LONG_ONLY" and is_short:
            continue
        if direction == "SHORT_ONLY" and not is_short:
            continue

        adjusted_return = float(trade.profit_ratio) - EXTRA_SLIPPAGE_ROUND_TRIP
        selected.append({
            "symbol": str(setup.symbol),
            "pair": str(trade.pair),
            "strategy_id": str(setup.strategy_id),
            "strategy_class": str(trade.strategy_class),
            "timeframe": str(setup.timeframe),
            "pool_tier": str(setup.pool_tier),
            "paper_direction": direction,
            "paper_weight": float(setup.paper_weight),
            "open": opened,
            "close": closed,
            "adjusted_return": adjusted_return,
            "portfolio_return_contribution": adjusted_return * float(setup.paper_weight),
            "is_short": is_short,
        })

    result = pd.DataFrame(selected)
    if result.empty:
        raise RuntimeError("No active-pool trades in common historical window")

    close_events = (
        result.groupby("close")["portfolio_return_contribution"]
        .sum()
        .sort_index()
    )
    equity = (1.0 + close_events).cumprod()
    drawdown = equity / equity.cummax() - 1.0

    exposure_events: list[tuple[pd.Timestamp, float]] = []
    for row in result.itertuples(index=False):
        exposure_events.append((row.open, float(row.paper_weight)))
        exposure_events.append((row.close, -float(row.paper_weight)))
    exposure_events.sort(key=lambda x: (x[0], x[1]))
    exposure = 0.0
    peak_exposure = 0.0
    for _, delta in exposure_events:
        exposure += delta
        peak_exposure = max(peak_exposure, exposure)

    summary = {
        "schema_version": 1,
        "label": "HISTORICAL_DIAGNOSTIC_NOT_FORWARD_PROOF",
        "common_start": start.date().isoformat(),
        "common_end": end.date().isoformat(),
        "setup_count": int(active["symbol"].nunique()),
        "core_count": int(active["pool_tier"].eq("CORE").sum()),
        "active_extension_count": int(active["pool_tier"].eq("ACTIVE").sum()),
        "trade_count": int(len(result)),
        "historical_total_return": float(equity.iloc[-1] - 1.0),
        "historical_max_drawdown": float(drawdown.min()),
        "peak_theoretical_exposure": float(peak_exposure),
        "positive_trade_rate": float((result["adjusted_return"] > 0.0).mean()),
        "important_limitation": (
            "The same historical sample contributed to setup selection. "
            "Use forward-paper results for genuine unseen-data evidence."
        ),
    }
    return result, summary
