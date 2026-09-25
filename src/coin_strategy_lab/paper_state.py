from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from coin_strategy_lab.runtime import ActiveRouter, PositionState


@dataclass(frozen=True)
class PaperPortfolioState:
    status: str
    kill_switch: bool
    kill_reason: str | None
    open_positions: int
    gross_exposure: float
    remaining_exposure: float
    max_gross_exposure: float
    max_open_positions: int
    daily_pnl: float
    daily_loss_limit: float
    portfolio_drawdown: float
    portfolio_drawdown_limit: float
    violations: tuple[str, ...]


def evaluate_paper_portfolio(
    router: ActiveRouter,
    positions: Iterable[PositionState] = (),
    daily_pnl: float = 0.0,
    portfolio_drawdown: float = 0.0,
) -> PaperPortfolioState:
    positions = list(positions)
    gross = sum(max(float(p.weight), 0.0) for p in positions)
    remaining = max(router.policy.max_gross_exposure - gross, 0.0)

    violations: list[str] = []
    seen: set[str] = set()

    for p in positions:
        if p.symbol in seen:
            violations.append(f"duplicate_symbol:{p.symbol}")
        seen.add(p.symbol)

        setup = router.get(p.symbol)
        if setup is None:
            violations.append(f"unknown_symbol:{p.symbol}")
            continue
        if float(p.weight) > setup.paper_weight + 1e-12:
            violations.append(f"overweight_symbol:{p.symbol}")

    if len(positions) > router.policy.max_open_positions:
        violations.append("max_open_positions_exceeded")

    if gross > router.policy.max_gross_exposure + 1e-12:
        violations.append("max_gross_exposure_exceeded")

    kill_reason = None
    if daily_pnl <= -abs(router.policy.daily_loss_limit):
        kill_reason = "daily_loss_limit"
    elif portfolio_drawdown <= -abs(router.policy.portfolio_drawdown_limit):
        kill_reason = "portfolio_drawdown_limit"

    if kill_reason is not None:
        status = "KILL_SWITCH"
    elif violations:
        status = "INVALID_STATE"
    else:
        status = "READY"

    return PaperPortfolioState(
        status=status,
        kill_switch=kill_reason is not None,
        kill_reason=kill_reason,
        open_positions=len(positions),
        gross_exposure=gross,
        remaining_exposure=remaining,
        max_gross_exposure=router.policy.max_gross_exposure,
        max_open_positions=router.policy.max_open_positions,
        daily_pnl=float(daily_pnl),
        daily_loss_limit=router.policy.daily_loss_limit,
        portfolio_drawdown=float(portfolio_drawdown),
        portfolio_drawdown_limit=router.policy.portfolio_drawdown_limit,
        violations=tuple(violations),
    )
