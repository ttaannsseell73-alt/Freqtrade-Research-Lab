from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import pandas as pd

from coin_strategy_lab.intent import StrategySignal, resolve_active_intent
from coin_strategy_lab.registry import StrategyRegistry
from coin_strategy_lab.runtime import ActiveRouter, ActiveSetup, PositionState


DEFAULT_ROUND_TRIP_COST = 0.0015


@dataclass(frozen=True)
class SignalEvent:
    symbol: str
    direction: str
    signal_time_ms: int
    entry_time_ms: int
    entry_price: float
    strategy_id: str
    timeframe: str


@dataclass(frozen=True)
class SymbolScan:
    symbol: str
    latest_closed_bar_ms: int | None
    events: tuple[SignalEvent, ...]
    liquidity_status: str
    mark_price: float | None
    evidence: dict
    market: dict


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()


def _gross_return(side: str, entry: float, exit_price: float) -> float:
    if entry <= 0.0 or exit_price <= 0.0:
        return 0.0
    if side == "LONG":
        return exit_price / entry - 1.0
    if side == "SHORT":
        return entry / exit_price - 1.0
    return 0.0


def extract_signal_events(
    setup: ActiveSetup,
    candles: pd.DataFrame,
    *,
    last_closed_bar_ms: int | None = None,
    registry: StrategyRegistry | None = None,
) -> tuple[tuple[SignalEvent, ...], int | None]:
    if candles.empty:
        return (), last_closed_bar_ms

    required = {
        "open_time_ms", "open", "high", "low", "close", "volume", "is_closed"
    }
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Candles missing columns: {sorted(missing)}")

    closed_indices = [
        i for i, value in enumerate(candles["is_closed"].astype(bool).tolist())
        if value
    ]
    if not closed_indices:
        return (), last_closed_bar_ms

    latest_idx = closed_indices[-1]
    latest_closed = int(candles.iloc[latest_idx]["open_time_ms"])
    if last_closed_bar_ms is None:
        candidate_indices = [latest_idx]
    else:
        candidate_indices = [
            i
            for i in closed_indices
            if int(candles.iloc[i]["open_time_ms"]) > int(last_closed_bar_ms)
        ]

    registry = registry or StrategyRegistry.discover_builtins()
    plugin = registry.get(setup.strategy_id)
    signal_frame = candles[
        ["date", "open", "high", "low", "close", "volume"]
    ].copy()
    prepared = plugin.prepare(signal_frame)
    longs = plugin.long_entries(prepared).fillna(False).astype(bool)
    shorts = plugin.short_entries(prepared).fillna(False).astype(bool)

    events: list[SignalEvent] = []
    for i in candidate_indices:
        is_long = bool(longs.iloc[i])
        is_short = bool(shorts.iloc[i])
        if not is_long and not is_short:
            continue
        if i + 1 >= len(candles):
            continue
        direction = "CONFLICT" if is_long and is_short else "LONG" if is_long else "SHORT"
        entry = candles.iloc[i + 1]
        events.append(
            SignalEvent(
                symbol=setup.symbol,
                direction=direction,
                signal_time_ms=int(candles.iloc[i]["open_time_ms"]),
                entry_time_ms=int(entry["open_time_ms"]),
                entry_price=float(entry["open"]),
                strategy_id=setup.strategy_id,
                timeframe=setup.timeframe,
            )
        )
    return tuple(events), latest_closed


def empty_shadow_state(cohort_id: str, now_ms: int) -> dict:
    return {
        "schema_version": 1,
        "cohort_id": cohort_id,
        "created_at_ms": int(now_ms),
        "updated_at_ms": int(now_ms),
        "snapshot_at_ms": int(now_ms),
        "round_trip_cost": DEFAULT_ROUND_TRIP_COST,
        "checkpoints": {},
        "positions": [],
        "closed_trades": [],
        "events": [],
        "realized_return": 0.0,
        "equity": {
            "current": 1.0,
            "peak": 1.0,
            "day_key": _utc_day(now_ms),
            "day_start": 1.0,
            "daily_pnl": 0.0,
            "drawdown": 0.0,
        },
        "kill_switch_latched": False,
        "kill_reason": None,
    }


def _positions_for_router(positions: list[dict]) -> list[PositionState]:
    return [
        PositionState(str(p["symbol"]), float(p["weight"]))
        for p in positions
    ]


def _mark_metrics(
    state: dict,
    scans: dict[str, SymbolScan],
    *,
    now_ms: int,
) -> dict:
    realized = float(state.get("realized_return", 0.0))
    cost = float(state.get("round_trip_cost", DEFAULT_ROUND_TRIP_COST))
    unrealized = 0.0
    for position in state.get("positions", []):
        scan = scans.get(str(position["symbol"]))
        mark = (
            float(scan.mark_price)
            if scan is not None and scan.mark_price is not None
            else float(position.get("mark_price", position["entry_price"]))
        )
        position["mark_price"] = mark
        gross = _gross_return(
            str(position["side"]),
            float(position["entry_price"]),
            mark,
        )
        contribution = float(position["weight"]) * (gross - cost)
        position["unrealized_contribution"] = contribution
        unrealized += contribution

    current = 1.0 + realized + unrealized
    previous_equity = state.get("equity", {})
    previous_current = float(previous_equity.get("current", 1.0))
    previous_peak = float(previous_equity.get("peak", 1.0))
    day_key = _utc_day(now_ms)
    previous_day = str(previous_equity.get("day_key", day_key))
    if previous_day != day_key:
        day_start = previous_current
    else:
        day_start = float(previous_equity.get("day_start", 1.0))
    peak = max(previous_peak, current)
    daily = current / day_start - 1.0 if day_start > 0.0 else 0.0
    drawdown = current / peak - 1.0 if peak > 0.0 else 0.0
    return {
        "current": current,
        "peak": peak,
        "day_key": day_key,
        "day_start": day_start,
        "daily_pnl": daily,
        "drawdown": drawdown,
        "unrealized_return": unrealized,
    }


def _close_position(
    state: dict,
    position: dict,
    *,
    price: float,
    at_ms: int,
    reason: str,
) -> None:
    cost = float(state.get("round_trip_cost", DEFAULT_ROUND_TRIP_COST))
    gross = _gross_return(
        str(position["side"]),
        float(position["entry_price"]),
        float(price),
    )
    net = gross - cost
    contribution = float(position["weight"]) * net
    state["realized_return"] = float(state.get("realized_return", 0.0)) + contribution
    closed = dict(position)
    closed.update(
        {
            "exit_price": float(price),
            "exit_time_ms": int(at_ms),
            "exit_reason": reason,
            "gross_return": gross,
            "net_return": net,
            "portfolio_contribution": contribution,
        }
    )
    closed.pop("unrealized_contribution", None)
    state.setdefault("closed_trades", []).append(closed)
    state.setdefault("events", []).append(
        {
            "type": "CLOSE",
            "symbol": position["symbol"],
            "side": position["side"],
            "at_ms": int(at_ms),
            "price": float(price),
            "reason": reason,
            "portfolio_contribution": contribution,
        }
    )


def _find_position(positions: list[dict], symbol: str) -> dict | None:
    return next((p for p in positions if p["symbol"] == symbol), None)


def _kill_switch_if_needed(
    router: ActiveRouter,
    state: dict,
    scans: dict[str, SymbolScan],
    *,
    now_ms: int,
) -> bool:
    metrics = _mark_metrics(state, scans, now_ms=now_ms)
    state["equity"] = metrics
    reason = None
    if metrics["daily_pnl"] <= -abs(router.policy.daily_loss_limit):
        reason = "daily_loss_limit"
    elif metrics["drawdown"] <= -abs(router.policy.portfolio_drawdown_limit):
        reason = "portfolio_drawdown_limit"
    if reason is None:
        return False

    for position in list(state.get("positions", [])):
        scan = scans.get(str(position["symbol"]))
        if scan is None or scan.mark_price is None:
            continue
        _close_position(
            state,
            position,
            price=float(scan.mark_price),
            at_ms=now_ms,
            reason=f"KILL_SWITCH:{reason}",
        )
        state["positions"].remove(position)
    state["kill_switch_latched"] = True
    state["kill_reason"] = reason
    state.setdefault("events", []).append(
        {"type": "KILL_SWITCH", "at_ms": now_ms, "reason": reason}
    )
    state["equity"] = _mark_metrics(state, scans, now_ms=now_ms)
    return True


def apply_shadow_scan(
    router: ActiveRouter,
    previous_state: dict | None,
    scans: Iterable[SymbolScan],
    *,
    now_ms: int,
) -> dict:
    state = (
        empty_shadow_state(router.cohort_id, now_ms)
        if previous_state is None
        else {
            **previous_state,
            "positions": [dict(x) for x in previous_state.get("positions", [])],
            "closed_trades": [dict(x) for x in previous_state.get("closed_trades", [])],
            "events": [dict(x) for x in previous_state.get("events", [])],
            "checkpoints": dict(previous_state.get("checkpoints", {})),
            "equity": dict(previous_state.get("equity", {})),
        }
    )
    if state.get("cohort_id") != router.cohort_id:
        raise ValueError("Shadow state cohort does not match active router")

    state["updated_at_ms"] = int(now_ms)
    state["snapshot_at_ms"] = int(now_ms)
    scans_by_symbol = {scan.symbol: scan for scan in scans}

    previous_day = str(state.get("equity", {}).get("day_key", _utc_day(now_ms)))
    current_day = _utc_day(now_ms)
    if (
        previous_day != current_day
        and state.get("kill_reason") == "daily_loss_limit"
    ):
        state["kill_switch_latched"] = False
        state["kill_reason"] = None
        state.setdefault("events", []).append(
            {"type": "DAILY_KILL_RESET", "at_ms": now_ms}
        )

    for scan in scans_by_symbol.values():
        if scan.latest_closed_bar_ms is not None:
            state["checkpoints"][scan.symbol] = int(scan.latest_closed_bar_ms)

    # Hard tradability block is an exit condition even without a strategy signal.
    for position in list(state.get("positions", [])):
        scan = scans_by_symbol.get(str(position["symbol"]))
        if (
            scan is not None
            and scan.liquidity_status == "BLOCK"
            and scan.mark_price is not None
        ):
            _close_position(
                state,
                position,
                price=float(scan.mark_price),
                at_ms=now_ms,
                reason="TRADABILITY_BLOCK",
            )
            state["positions"].remove(position)

    if _kill_switch_if_needed(router, state, scans_by_symbol, now_ms=now_ms):
        return state

    ordered_events: list[tuple[SymbolScan, SignalEvent]] = []
    for scan in scans_by_symbol.values():
        for event in scan.events:
            ordered_events.append((scan, event))
    ordered_events.sort(
        key=lambda item: (
            item[1].entry_time_ms,
            router.get(item[1].symbol).pool_tier != "CORE",
            -router.get(item[1].symbol).active_score,
            item[1].symbol,
        )
    )

    for scan, event in ordered_events:
        setup = router.get(event.symbol)
        if setup is None:
            continue
        if state.get("kill_switch_latched"):
            state["events"].append(
                {
                    "type": "NO_TRADE",
                    "symbol": event.symbol,
                    "at_ms": event.entry_time_ms,
                    "reason": "kill_switch_latched",
                }
            )
            continue
        if event.direction == "CONFLICT":
            state["events"].append(
                {
                    "type": "DIRECTION_CONFLICT",
                    "symbol": event.symbol,
                    "at_ms": event.entry_time_ms,
                }
            )
            continue

        position = _find_position(state["positions"], event.symbol)
        if position is not None and position["side"] == event.direction:
            state["events"].append(
                {
                    "type": "DUPLICATE_SUPPRESSED",
                    "symbol": event.symbol,
                    "side": event.direction,
                    "at_ms": event.entry_time_ms,
                }
            )
            continue

        if position is not None and position["side"] != event.direction:
            _close_position(
                state,
                position,
                price=event.entry_price,
                at_ms=event.entry_time_ms,
                reason="OPPOSITE_SIGNAL",
            )
            state["positions"].remove(position)

        evidence = scan.evidence
        signal = StrategySignal(
            strategy_id=event.strategy_id,
            timeframe=event.timeframe,
            direction=event.direction,
            fresh=True,
            closed_candle=True,
            signal_time_ms=event.signal_time_ms,
            trades=int(evidence.get("trades", 0)),
            profit_factor=float(evidence.get("profit_factor", 0.0)),
            max_drawdown=float(evidence.get("max_drawdown", 1.0)),
            net_return=0.0,
            evidence_consistent=(
                bool(evidence.get("sample_ok", False))
                and str(evidence.get("status", "")) == "EXECUTION_PASS"
            ),
        )
        metrics = _mark_metrics(state, scans_by_symbol, now_ms=now_ms)
        decision = resolve_active_intent(
            router,
            symbol=event.symbol,
            signals=[signal],
            liquidity_status=scan.liquidity_status,
            open_positions=_positions_for_router(state["positions"]),
            daily_pnl=float(metrics["daily_pnl"]),
            portfolio_drawdown=float(metrics["drawdown"]),
        )
        if not decision.allowed:
            state["events"].append(
                {
                    "type": decision.status,
                    "symbol": event.symbol,
                    "side": event.direction,
                    "at_ms": event.entry_time_ms,
                    "reason": decision.reason,
                    "evidence_flags": list(decision.evidence_flags),
                    "liquidity_status": scan.liquidity_status,
                }
            )
            if decision.status == "KILL_SWITCH":
                state["kill_switch_latched"] = True
                state["kill_reason"] = decision.reason
            continue

        state["positions"].append(
            {
                "symbol": event.symbol,
                "side": event.direction,
                "weight": decision.weight,
                "entry_price": event.entry_price,
                "entry_time_ms": event.entry_time_ms,
                "signal_time_ms": event.signal_time_ms,
                "strategy_id": event.strategy_id,
                "timeframe": event.timeframe,
                "pool_tier": setup.pool_tier,
                "intent_id": decision.intent_id,
                "support_count": decision.support_count,
                "liquidity_status": scan.liquidity_status,
            }
        )
        state["events"].append(
            {
                "type": "OPEN",
                "symbol": event.symbol,
                "side": event.direction,
                "at_ms": event.entry_time_ms,
                "price": event.entry_price,
                "weight": decision.weight,
                "intent_id": decision.intent_id,
                "support_count": decision.support_count,
            }
        )

    _kill_switch_if_needed(router, state, scans_by_symbol, now_ms=now_ms)
    state["equity"] = _mark_metrics(state, scans_by_symbol, now_ms=now_ms)
    state["closed_trades"] = state.get("closed_trades", [])[-2000:]
    state["events"] = state.get("events", [])[-4000:]
    return state
