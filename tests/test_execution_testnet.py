from dataclasses import replace

import pytest

from coin_strategy_lab.binance_testnet import BinanceFuturesTestnet
from coin_strategy_lab.execution import (
    ExchangeOrder,
    ExchangePosition,
    ExchangeSnapshot,
    ExecutionCoordinator,
)
from coin_strategy_lab.intent import StrategySignal, resolve_active_intent
from coin_strategy_lab.runtime import ActiveRouter, ActiveSetup, RuntimePolicy


def setup(symbol="AAAUSDT", direction="BOTH", weight=0.03):
    return ActiveSetup(
        symbol=symbol,
        timeframe="4h",
        strategy_id="squeeze_momentum",
        strategy_class="CSL4hSqueezeMomentum",
        pool_tier="CORE",
        direction=direction,
        paper_weight=weight,
        active_score=80.0,
    )


def router(*setups):
    return ActiveRouter(
        list(setups) or [setup()],
        RuntimePolicy(
            max_gross_exposure=0.70,
            max_open_positions=20,
            daily_loss_limit=0.03,
            portfolio_drawdown_limit=0.10,
        ),
        "forward-active-v1",
    )


class FakeGateway:
    def __init__(self):
        self.snap = ExchangeSnapshot(1000.0, (), ())
        self.orders_by_client = {}
        self.placed = []
        self.cancelled = []
        self.closed = []

    def snapshot(self):
        return self.snap

    def get_order_by_client_id(self, symbol, client_order_id):
        return self.orders_by_client.get((symbol, client_order_id))

    def place_market_entry(self, symbol, side, target_weight, client_order_id):
        order = ExchangeOrder(
            symbol=symbol,
            order_id=str(len(self.placed) + 1),
            client_order_id=client_order_id,
            side="BUY" if side == "LONG" else "SELL",
            status="FILLED",
            orig_qty=1.0,
            executed_qty=1.0,
            reduce_only=False,
            update_time_ms=1000,
        )
        self.placed.append((symbol, side, target_weight, client_order_id))
        self.orders_by_client[(symbol, client_order_id)] = order
        return order

    def cancel_order(self, order):
        self.cancelled.append(order)
        return replace(order, status="CANCELED")

    def close_position_reduce_only(self, position, client_order_id):
        self.closed.append((position, client_order_id))
        return ExchangeOrder(
            symbol=position.symbol,
            order_id="close-1",
            client_order_id=client_order_id,
            side="SELL" if position.quantity > 0 else "BUY",
            status="FILLED",
            orig_qty=abs(position.quantity),
            executed_qty=abs(position.quantity),
            reduce_only=True,
            update_time_ms=2000,
        )


def test_admitted_signal_submits_market_entry_once():
    gateway = FakeGateway()
    engine = ExecutionCoordinator(router(setup()), gateway)
    result = engine.submit_signal(
        signal_id="bar-20260925-1200",
        symbol="AAAUSDT",
        side="LONG",
    )
    assert result.allowed is True
    assert result.status == "FILLED"
    assert result.target_weight == 0.03
    assert result.client_order_id.startswith("csl-ent-")
    assert len(gateway.placed) == 1


def test_same_signal_is_idempotent_on_exchange():
    gateway = FakeGateway()
    engine = ExecutionCoordinator(router(setup()), gateway)
    first = engine.submit_signal(
        signal_id="same-signal",
        symbol="AAAUSDT",
        side="LONG",
    )
    second = engine.submit_signal(
        signal_id="same-signal",
        symbol="AAAUSDT",
        side="LONG",
    )
    assert first.client_order_id == second.client_order_id
    assert second.status == "DUPLICATE_SUPPRESSED"
    assert len(gateway.placed) == 1


def test_wrong_direction_never_reaches_exchange():
    gateway = FakeGateway()
    engine = ExecutionCoordinator(
        router(setup(direction="SHORT_ONLY")),
        gateway,
    )
    result = engine.submit_signal(
        signal_id="wrong-dir",
        symbol="AAAUSDT",
        side="LONG",
    )
    assert result.allowed is False
    assert result.reason == "direction_not_enabled"
    assert gateway.placed == []


def test_unknown_exchange_position_blocks_new_orders_fail_closed():
    gateway = FakeGateway()
    gateway.snap = ExchangeSnapshot(
        1000.0,
        (
            ExchangePosition(
                "NOTOURSUSDT",
                "LONG",
                1.0,
                0.10,
            ),
        ),
        (),
    )
    engine = ExecutionCoordinator(router(setup()), gateway)
    result = engine.submit_signal(
        signal_id="blocked",
        symbol="AAAUSDT",
        side="LONG",
    )
    assert result.status == "NO_TRADE"
    assert result.reason == "exchange_state_invalid"
    assert "unknown_exchange_position:NOTOURSUSDT" in result.actions
    assert gateway.placed == []


def test_partial_fill_is_reported_by_reconcile():
    gateway = FakeGateway()
    gateway.snap = ExchangeSnapshot(
        1000.0,
        (),
        (
            ExchangeOrder(
                "AAAUSDT",
                "7",
                "csl-ent-abc",
                "BUY",
                "PARTIALLY_FILLED",
                10.0,
                4.0,
                False,
                1000,
            ),
        ),
    )
    report = ExecutionCoordinator(router(setup()), gateway).reconcile()
    assert report.status == "READY"
    assert report.partial_fills == ("AAAUSDT:7:4.0/10.0",)


def test_daily_kill_switch_cancels_system_orders_and_flattens_active_positions():
    gateway = FakeGateway()
    gateway.snap = ExchangeSnapshot(
        1000.0,
        (
            ExchangePosition(
                "AAAUSDT",
                "LONG",
                2.0,
                0.03,
            ),
        ),
        (
            ExchangeOrder(
                "AAAUSDT",
                "11",
                "csl-ent-old",
                "BUY",
                "NEW",
                2.0,
                0.0,
                False,
                1000,
            ),
        ),
    )
    engine = ExecutionCoordinator(router(setup()), gateway, now_ms=lambda: 5000)
    result = engine.submit_signal(
        signal_id="kill",
        symbol="AAAUSDT",
        side="LONG",
        daily_pnl=-0.031,
    )
    assert result.status == "KILL_SWITCH"
    assert len(gateway.cancelled) == 1
    assert len(gateway.closed) == 1
    assert any(x == "flatten:AAAUSDT" for x in result.actions)


def test_stale_cleanup_only_touches_system_orders():
    gateway = FakeGateway()
    gateway.snap = ExchangeSnapshot(
        1000.0,
        (),
        (
            ExchangeOrder(
                "AAAUSDT", "1", "csl-ent-a", "BUY",
                "NEW", 1.0, 0.0, False, 1000,
            ),
            ExchangeOrder(
                "AAAUSDT", "2", "manual-order", "BUY",
                "NEW", 1.0, 0.0, False, 1000,
            ),
        ),
    )
    engine = ExecutionCoordinator(router(setup()), gateway, now_ms=lambda: 5000)
    actions = engine.cleanup_stale_orders(max_age_ms=2000)
    assert actions == ("cancel_stale:AAAUSDT:1",)
    assert [x.order_id for x in gateway.cancelled] == ["1"]


def test_binance_adapter_rejects_live_endpoint():
    with pytest.raises(ValueError, match="TESTNET"):
        BinanceFuturesTestnet(
            "key",
            "secret",
            base_url="https://fapi.binance.com",
        )


def test_quantity_rounds_down_to_exchange_step():
    assert BinanceFuturesTestnet._floor_step(1.239, 0.01) == 1.23
    assert BinanceFuturesTestnet._floor_step(0.0099, 0.001) == 0.009


def test_flat_state_verification_detects_active_position_and_system_order():
    gateway = FakeGateway()
    gateway.snap = ExchangeSnapshot(
        1000.0,
        (
            ExchangePosition(
                "AAAUSDT",
                "LONG",
                1.5,
                0.03,
            ),
        ),
        (
            ExchangeOrder(
                "AAAUSDT",
                "42",
                "csl-ent-remains",
                "BUY",
                "NEW",
                1.0,
                0.0,
                False,
                1000,
            ),
            ExchangeOrder(
                "AAAUSDT",
                "43",
                "manual-order",
                "BUY",
                "NEW",
                1.0,
                0.0,
                False,
                1000,
            ),
        ),
    )

    violations = ExecutionCoordinator(router(setup()), gateway).flat_state_violations()

    assert "active_position_not_flat:AAAUSDT:1.5" in violations
    assert "system_open_order_remains:AAAUSDT:42" in violations
    assert not any("43" in item for item in violations)


def test_flat_state_verification_passes_when_no_system_residue():
    gateway = FakeGateway()
    gateway.snap = ExchangeSnapshot(
        1000.0,
        (),
        (
            ExchangeOrder(
                "AAAUSDT",
                "43",
                "manual-order",
                "BUY",
                "NEW",
                1.0,
                0.0,
                False,
                1000,
            ),
        ),
    )

    assert ExecutionCoordinator(router(setup()), gateway).flat_state_violations() == ()



def test_resolved_multi_strategy_intent_reaches_exchange_once():
    gateway = FakeGateway()
    active_router = router(setup())
    intent = resolve_active_intent(
        active_router,
        symbol="AAAUSDT",
        signals=[
            StrategySignal(
                "squeeze_momentum", "4h", "LONG", True, True,
                1000, 30, 1.8, 0.20, 0.50, True,
            ),
            StrategySignal(
                "mavilimw", "1h", "LONG", True, True,
                1100, 25, 1.6, 0.18, 0.40, True,
            ),
        ],
    )
    assert intent.support_count == 2
    engine = ExecutionCoordinator(active_router, gateway)
    result = engine.submit_intent(intent)
    assert result.allowed is True
    assert len(gateway.placed) == 1
    assert gateway.placed[0][0] == "AAAUSDT"


def test_conflicted_intent_never_reaches_exchange():
    gateway = FakeGateway()
    active_router = router(setup())
    intent = resolve_active_intent(
        active_router,
        symbol="AAAUSDT",
        signals=[
            StrategySignal(
                "squeeze_momentum", "4h", "LONG", True, True,
                1000, 30, 1.8, 0.20, 0.50, True,
            ),
            StrategySignal(
                "mavilimw", "1h", "SHORT", True, True,
                1100, 25, 1.6, 0.18, 0.40, True,
            ),
        ],
    )
    result = ExecutionCoordinator(active_router, gateway).submit_intent(intent)
    assert result.allowed is False
    assert result.status == "DIRECTION_CONFLICT"
    assert gateway.placed == []
