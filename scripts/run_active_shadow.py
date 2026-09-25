from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from coin_strategy_lab.public_market import (
    BinanceFuturesPublicMarket,
    classify_tradability,
)
from coin_strategy_lab.runtime import ActiveRouter
from coin_strategy_lab.shadow import (
    SymbolScan,
    apply_shadow_scan,
    extract_signal_events,
)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--cohort", type=Path, default=Path("config/active_pool_v1.json"))
    p.add_argument("--runtime", type=Path, default=Path("config/active_system_v1.json"))
    p.add_argument("--evidence", type=Path, default=Path("config/active_evidence_v1.json"))
    p.add_argument("--tradability", type=Path, default=Path("config/tradability_policy_v1.json"))
    p.add_argument("--previous-state", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("shadow-paper"))
    return p.parse_args()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evidence_map(payload: dict) -> dict[str, dict]:
    return {str(x["symbol"]): dict(x) for x in payload["evidence"]}


def render_markdown(state: dict, report: dict) -> str:
    eq = state["equity"]
    lines = [
        "# Active Shadow Paper",
        "",
        f"Cohort: {state['cohort_id']}",
        f"Snapshot ms: {state['snapshot_at_ms']}",
        f"Open positions: {len(state['positions'])}",
        f"Closed trades: {len(state['closed_trades'])}",
        f"Current equity: {eq['current']:.6f}",
        f"Daily PnL: {eq['daily_pnl']:.4%}",
        f"Drawdown: {eq['drawdown']:.4%}",
        f"Kill switch: {state.get('kill_switch_latched', False)}",
        "",
        "## Open positions",
        "",
        "| Symbol | Side | TF | Strategy | Weight | Entry | Mark |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for p in state["positions"]:
        lines.append(
            f"| {p['symbol']} | {p['side']} | {p['timeframe']} | "
            f"{p['strategy_id']} | {p['weight']:.3f} | "
            f"{p['entry_price']:.8g} | {p.get('mark_price', 0):.8g} |"
        )
    lines += [
        "",
        "## Current scan",
        "",
        "| Symbol | TF | Strategy | New signals | Liquidity | Market status |",
        "|---|---|---|---:|---|---|",
    ]
    for row in report["symbols"]:
        lines.append(
            f"| {row['symbol']} | {row['timeframe']} | {row['strategy_id']} | "
            f"{row['event_count']} | {row['liquidity_status']} | {row['market_status']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    router = ActiveRouter.from_files(args.cohort, args.runtime)
    evidence = evidence_map(load_json(args.evidence))
    policy = load_json(args.tradability)

    previous = None
    if args.previous_state and args.previous_state.exists():
        previous = load_json(args.previous_state)

    market = BinanceFuturesPublicMarket()
    try:
        now_ms = market.server_time_ms()
        clock_source = "binance_public"
    except Exception:
        now_ms = int(time.time() * 1000)
        clock_source = "local_fallback"

    previous_positions = {
        str(x["symbol"]) for x in (previous or {}).get("positions", [])
    }
    checkpoints = (previous or {}).get("checkpoints", {})
    scans: list[SymbolScan] = []
    report_rows: list[dict] = []

    for setup in router.setups:
        symbol = setup.symbol
        market_status = "OK"
        error = None
        frame = None
        events = ()
        latest_closed = checkpoints.get(symbol)
        mark_price = None
        liquidity_status = "NO_MARKET_SNAPSHOT"
        market_payload: dict = {}

        try:
            frame = market.klines(
                symbol,
                setup.timeframe,
                limit=300,
                server_time_ms=now_ms,
            )
            events, latest_closed = extract_signal_events(
                setup,
                frame,
                last_closed_bar_ms=checkpoints.get(symbol),
            )
            if not frame.empty:
                mark_price = float(frame.iloc[-1]["close"])
        except Exception as exc:
            market_status = "KLINES_ERROR"
            error = f"{type(exc).__name__}: {exc}"

        needs_execution_snapshot = bool(events) or symbol in previous_positions
        if needs_execution_snapshot and market_status == "OK":
            try:
                snap = market.tradability_snapshot(symbol)
                liquidity_status = classify_tradability(snap, policy)
                mark_price = snap.mark_price
                market_payload = {
                    "quote_volume_24h": snap.quote_volume_24h,
                    "spread_bps": snap.spread_bps,
                    "bid_depth_10bps": snap.bid_depth_10bps,
                    "ask_depth_10bps": snap.ask_depth_10bps,
                    "min_side_depth_10bps": snap.min_side_depth_10bps,
                    "open_interest_notional": snap.open_interest_notional,
                    "mark_price": snap.mark_price,
                }
            except Exception as exc:
                liquidity_status = "NO_MARKET_SNAPSHOT"
                market_status = "TRADABILITY_ERROR"
                error = f"{type(exc).__name__}: {exc}"
        elif market_status == "OK":
            liquidity_status = "TRADEABLE"

        ev = evidence.get(symbol)
        if ev is None:
            raise RuntimeError(f"Missing frozen evidence for {symbol}")

        scans.append(
            SymbolScan(
                symbol=symbol,
                latest_closed_bar_ms=(
                    int(latest_closed) if latest_closed is not None else None
                ),
                events=tuple(events),
                liquidity_status=liquidity_status,
                mark_price=mark_price,
                evidence=ev,
                market=market_payload,
            )
        )
        report_rows.append(
            {
                "symbol": symbol,
                "timeframe": setup.timeframe,
                "strategy_id": setup.strategy_id,
                "pool_tier": setup.pool_tier,
                "event_count": len(events),
                "events": [
                    {
                        "direction": x.direction,
                        "signal_time_ms": x.signal_time_ms,
                        "entry_time_ms": x.entry_time_ms,
                        "entry_price": x.entry_price,
                    }
                    for x in events
                ],
                "liquidity_status": liquidity_status,
                "mark_price": mark_price,
                "market_status": market_status,
                "error": error,
            }
        )

    state = apply_shadow_scan(
        router,
        previous,
        scans,
        now_ms=now_ms,
    )
    report = {
        "schema_version": 1,
        "cohort_id": router.cohort_id,
        "snapshot_at_ms": now_ms,
        "clock_source": clock_source,
        "setup_count": len(router.setups),
        "fresh_event_count": sum(x["event_count"] for x in report_rows),
        "open_positions": len(state["positions"]),
        "closed_trades": len(state["closed_trades"]),
        "kill_switch_latched": state.get("kill_switch_latched", False),
        "kill_reason": state.get("kill_reason"),
        "equity": state["equity"],
        "symbols": report_rows,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "SHADOW_STATE.json").write_text(
        json.dumps(state, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "SHADOW_REPORT.json").write_text(
        json.dumps(report, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "SHADOW_REPORT.md").write_text(
        render_markdown(state, report),
        encoding="utf-8",
    )
    print(json.dumps({
        "snapshot_at_ms": now_ms,
        "fresh_event_count": report["fresh_event_count"],
        "open_positions": report["open_positions"],
        "closed_trades": report["closed_trades"],
        "daily_pnl": state["equity"]["daily_pnl"],
        "drawdown": state["equity"]["drawdown"],
        "kill_switch": state.get("kill_switch_latched", False),
        "market_errors": sum(
            1 for x in report_rows if x["market_status"] != "OK"
        ),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
