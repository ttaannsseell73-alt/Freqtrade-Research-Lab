from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from coin_strategy_lab.binance_testnet import BinanceFuturesTestnet
from coin_strategy_lab.execution import ExecutionCoordinator
from coin_strategy_lab.runtime import ActiveRouter


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--cohort",
        type=Path,
        default=Path("config/active_pool_v1.json"),
    )
    p.add_argument(
        "--runtime",
        type=Path,
        default=Path("config/active_system_v1.json"),
    )
    p.add_argument(
        "--exercise-order",
        action="store_true",
        help="Place one TESTNET market entry and immediately kill-switch flatten it.",
    )
    return p.parse_args()


def main():
    args = parse_args()
    router = ActiveRouter.from_files(args.cohort, args.runtime)
    gateway = BinanceFuturesTestnet.from_env(
        allow_orders=True if args.exercise_order else False
    )
    engine = ExecutionCoordinator(router, gateway)

    one_way = gateway.position_mode_is_one_way()
    print(json.dumps({
        "phase": "account_mode_preflight",
        "one_way_mode": one_way,
        "required": True,
    }, indent=2))
    if not one_way:
        raise SystemExit(
            "Hedge Mode detected; switch Binance Futures TESTNET to One-way Mode."
        )

    report = engine.reconcile()
    print(json.dumps({
        "phase": "reconcile",
        "status": report.status,
        "violations": list(report.violations),
        "partial_fills": list(report.partial_fills),
        "gross_exposure": report.gross_exposure,
    }, indent=2))

    if report.status != "READY":
        raise SystemExit("Exchange state is not clean; refusing smoke execution.")

    if not args.exercise_order:
        print(json.dumps({
            "status": "TESTNET_AUTH_RECONCILE_OK",
            "live_trading": False,
            "orders_armed": False,
        }, indent=2))
        return

    tradable = gateway.tradable_symbols()
    candidate = next(
        (
            x for x in router.setups
            if x.symbol in tradable and x.direction in {"BOTH", "LONG_ONLY"}
        ),
        None,
    )
    if candidate is None:
        raise SystemExit(
            "No frozen active LONG-capable setup is tradable on Binance testnet."
        )

    result = engine.submit_signal(
        signal_id="manual-testnet-smoke-v1",
        symbol=candidate.symbol,
        side="LONG",
    )
    print(json.dumps({
        "phase": "entry",
        "symbol": candidate.symbol,
        "status": result.status,
        "reason": result.reason,
        "client_order_id": result.client_order_id,
    }, indent=2))
    if not result.allowed:
        raise SystemExit("Testnet entry was not admitted.")

    post = engine.reconcile()
    print(json.dumps({
        "phase": "post_entry_reconcile",
        "status": post.status,
        "positions": [x.symbol for x in post.exchange_positions],
        "partial_fills": list(post.partial_fills),
    }, indent=2))

    actions = engine.trigger_kill_switch("testnet_smoke_cleanup")
    print(json.dumps({
        "phase": "cleanup",
        "actions": list(actions),
        "live_trading": False,
    }, indent=2))

    final_violations = ()
    for _ in range(20):
        final_violations = engine.flat_state_violations()
        if not final_violations:
            break
        time.sleep(0.25)

    print(json.dumps({
        "phase": "final_flat_verification",
        "status": "FLAT" if not final_violations else "RESIDUE",
        "violations": list(final_violations),
        "live_trading": False,
    }, indent=2))
    if final_violations:
        raise SystemExit(
            "Testnet cleanup did not reach verified flat state: "
            + ", ".join(final_violations)
        )


if __name__ == "__main__":
    main()
