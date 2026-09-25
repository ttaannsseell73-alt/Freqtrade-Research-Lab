from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import time
from typing import Protocol

from coin_strategy_lab.paper_state import evaluate_paper_portfolio
from coin_strategy_lab.runtime import ActiveRouter, PositionState


SYSTEM_CLIENT_PREFIX = "csl-"


@dataclass(frozen=True)
class ExchangePosition:
    symbol: str
    side: str
    quantity: float
    weight: float
    entry_price: float = 0.0
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0


@dataclass(frozen=True)
class ExchangeOrder:
    symbol: str
    order_id: str
    client_order_id: str
    side: str
    status: str
    orig_qty: float
    executed_qty: float
    reduce_only: bool
    update_time_ms: int


@dataclass(frozen=True)
class ExchangeSnapshot:
    equity_usdt: float
    positions: tuple[ExchangePosition, ...] = ()
    open_orders: tuple[ExchangeOrder, ...] = ()


@dataclass(frozen=True)
class ReconcileReport:
    status: str
    positions: tuple[PositionState, ...]
    exchange_positions: tuple[ExchangePosition, ...]
    open_orders: tuple[ExchangeOrder, ...]
    violations: tuple[str, ...]
    partial_fills: tuple[str, ...]
    gross_exposure: float


@dataclass(frozen=True)
class ExecutionResult:
    allowed: bool
    status: str
    reason: str
    symbol: str
    side: str
    target_weight: float
    client_order_id: str | None = None
    order: ExchangeOrder | None = None
    actions: tuple[str, ...] = field(default_factory=tuple)


class FuturesExecutionGateway(Protocol):
    def snapshot(self) -> ExchangeSnapshot: ...

    def get_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None: ...

    def place_market_entry(
        self,
        symbol: str,
        side: str,
        target_weight: float,
        client_order_id: str,
    ) -> ExchangeOrder: ...

    def cancel_order(self, order: ExchangeOrder) -> ExchangeOrder: ...

    def close_position_reduce_only(
        self,
        position: ExchangePosition,
        client_order_id: str,
    ) -> ExchangeOrder | None: ...


class ExecutionCoordinator:
    def __init__(
        self,
        router: ActiveRouter,
        gateway: FuturesExecutionGateway,
        *,
        now_ms=None,
    ):
        self.router = router
        self.gateway = gateway
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))

    @property
    def active_symbols(self) -> set[str]:
        return {x.symbol for x in self.router.setups}

    def _client_order_id(
        self,
        purpose: str,
        symbol: str,
        side: str,
        signal_id: str,
    ) -> str:
        raw = (
            f"{self.router.cohort_id}|{purpose}|{symbol}|{side}|{signal_id}"
        ).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()[:20]
        return f"{SYSTEM_CLIENT_PREFIX}{purpose[:3]}-{digest}"

    def reconcile(self) -> ReconcileReport:
        snap = self.gateway.snapshot()
        violations: list[str] = []
        partials: list[str] = []
        positions: list[PositionState] = []
        seen: set[str] = set()

        for p in snap.positions:
            symbol = p.symbol.upper()
            if p.quantity == 0.0:
                continue
            if symbol not in self.active_symbols:
                violations.append(f"unknown_exchange_position:{symbol}")
                continue
            if symbol in seen:
                violations.append(f"duplicate_exchange_position:{symbol}")
            seen.add(symbol)
            positions.append(PositionState(symbol, max(float(p.weight), 0.0)))

        for order in snap.open_orders:
            symbol = order.symbol.upper()
            if not order.client_order_id.startswith(SYSTEM_CLIENT_PREFIX):
                violations.append(
                    f"foreign_open_order:{symbol}:{order.order_id}"
                )
                continue
            if symbol not in self.active_symbols:
                violations.append(
                    f"system_order_outside_active_pool:{symbol}:{order.order_id}"
                )
            if order.status.upper() == "PARTIALLY_FILLED":
                partials.append(
                    f"{symbol}:{order.order_id}:"
                    f"{order.executed_qty}/{order.orig_qty}"
                )

        paper = evaluate_paper_portfolio(
            self.router,
            positions=positions,
            daily_pnl=0.0,
            portfolio_drawdown=0.0,
        )
        violations.extend(paper.violations)
        status = "READY" if not violations else "INVALID_STATE"
        return ReconcileReport(
            status=status,
            positions=tuple(positions),
            exchange_positions=tuple(snap.positions),
            open_orders=tuple(snap.open_orders),
            violations=tuple(violations),
            partial_fills=tuple(partials),
            gross_exposure=paper.gross_exposure,
        )

    def flat_state_violations(self) -> tuple[str, ...]:
        """Return exchange residue that prevents a verified flat state."""
        snap = self.gateway.snapshot()
        violations: list[str] = []

        for position in snap.positions:
            symbol = position.symbol.upper()
            if symbol in self.active_symbols and position.quantity != 0.0:
                violations.append(
                    f"active_position_not_flat:{symbol}:{position.quantity}"
                )

        for order in snap.open_orders:
            if order.client_order_id.startswith(SYSTEM_CLIENT_PREFIX):
                violations.append(
                    f"system_open_order_remains:{order.symbol}:{order.order_id}"
                )

        return tuple(violations)

    def submit_signal(
        self,
        *,
        signal_id: str,
        symbol: str,
        side: str,
        daily_pnl: float = 0.0,
        portfolio_drawdown: float = 0.0,
    ) -> ExecutionResult:
        symbol = symbol.upper()
        side = side.upper()

        report = self.reconcile()
        if report.status != "READY":
            return ExecutionResult(
                False,
                "NO_TRADE",
                "exchange_state_invalid",
                symbol,
                side,
                0.0,
                actions=report.violations,
            )

        decision = self.router.admit(
            symbol=symbol,
            side=side,
            open_positions=report.positions,
            daily_pnl=daily_pnl,
            portfolio_drawdown=portfolio_drawdown,
        )
        if decision.status == "KILL_SWITCH":
            actions = self.trigger_kill_switch(decision.reason)
            return ExecutionResult(
                False,
                "KILL_SWITCH",
                decision.reason,
                symbol,
                side,
                0.0,
                actions=actions,
            )
        if not decision.allowed:
            return ExecutionResult(
                False,
                decision.status,
                decision.reason,
                symbol,
                side,
                0.0,
            )

        client_order_id = self._client_order_id(
            "entry", symbol, side, signal_id
        )
        existing = self.gateway.get_order_by_client_id(
            symbol, client_order_id
        )
        if existing is not None:
            return ExecutionResult(
                True,
                "DUPLICATE_SUPPRESSED",
                "existing_exchange_order",
                symbol,
                side,
                decision.weight,
                client_order_id=client_order_id,
                order=existing,
            )

        order = self.gateway.place_market_entry(
            symbol=symbol,
            side=side,
            target_weight=decision.weight,
            client_order_id=client_order_id,
        )
        return ExecutionResult(
            True,
            order.status.upper(),
            "submitted",
            symbol,
            side,
            decision.weight,
            client_order_id=client_order_id,
            order=order,
        )

    def cleanup_stale_orders(
        self,
        *,
        max_age_ms: int = 120_000,
    ) -> tuple[str, ...]:
        now = int(self._now_ms())
        snap = self.gateway.snapshot()
        actions: list[str] = []
        for order in snap.open_orders:
            if not order.client_order_id.startswith(SYSTEM_CLIENT_PREFIX):
                continue
            if now - int(order.update_time_ms) < max_age_ms:
                continue
            self.gateway.cancel_order(order)
            actions.append(f"cancel_stale:{order.symbol}:{order.order_id}")
        return tuple(actions)

    def trigger_kill_switch(self, reason: str) -> tuple[str, ...]:
        snap = self.gateway.snapshot()
        actions: list[str] = []

        for order in snap.open_orders:
            if not order.client_order_id.startswith(SYSTEM_CLIENT_PREFIX):
                continue
            self.gateway.cancel_order(order)
            actions.append(f"cancel:{order.symbol}:{order.order_id}")

        for position in snap.positions:
            symbol = position.symbol.upper()
            if symbol not in self.active_symbols or position.quantity == 0.0:
                continue
            close_id = self._client_order_id(
                "close",
                symbol,
                "FLAT",
                f"{reason}:{int(self._now_ms())}",
            )
            existing = self.gateway.get_order_by_client_id(symbol, close_id)
            if existing is None:
                self.gateway.close_position_reduce_only(position, close_id)
            actions.append(f"flatten:{symbol}")

        actions.append(f"kill_reason:{reason}")
        return tuple(actions)
