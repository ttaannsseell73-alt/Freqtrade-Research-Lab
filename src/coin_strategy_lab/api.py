from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from coin_strategy_lab.paper_state import evaluate_paper_portfolio
from coin_strategy_lab.runtime import ActiveRouter, PositionState


DEFAULT_COHORT = Path("config/active_pool_v1.json")
DEFAULT_RUNTIME = Path("config/active_system_v1.json")


class PositionInput(BaseModel):
    symbol: str
    weight: float = Field(ge=0.0)


class AdmitRequest(BaseModel):
    symbol: str
    side: str
    open_positions: list[PositionInput] = []
    daily_pnl: float = 0.0
    portfolio_drawdown: float = 0.0


class PortfolioRequest(BaseModel):
    open_positions: list[PositionInput] = []
    daily_pnl: float = 0.0
    portfolio_drawdown: float = 0.0


def _setup_payload(setup) -> dict:
    return {
        "symbol": setup.symbol,
        "timeframe": setup.timeframe,
        "strategy_id": setup.strategy_id,
        "strategy_class": setup.strategy_class,
        "pool_tier": setup.pool_tier,
        "direction": setup.direction,
        "paper_weight": setup.paper_weight,
        "active_score": setup.active_score,
    }


def create_app(
    cohort_path: Path = DEFAULT_COHORT,
    runtime_config_path: Path = DEFAULT_RUNTIME,
) -> FastAPI:
    router = ActiveRouter.from_files(cohort_path, runtime_config_path)

    app = FastAPI(
        title="CoinStrategyLab Active Runtime API",
        version="1.0.0",
        description="Read-only routing and paper admission API for the frozen active pool.",
    )
    app.state.router = router

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "mode": "paper_only",
            "cohort_id": router.cohort_id,
            "setup_count": len(router.setups),
        }

    @app.get("/v1/system")
    def system() -> dict:
        return {
            "cohort_id": router.cohort_id,
            "mode": "paper_only",
            "default": "NO_TRADE",
            "setup_count": len(router.setups),
            "core_count": sum(x.pool_tier == "CORE" for x in router.setups),
            "active_count": sum(x.pool_tier == "ACTIVE" for x in router.setups),
            "configured_weight": router.configured_weight,
            "policy": {
                "max_gross_exposure": router.policy.max_gross_exposure,
                "max_open_positions": router.policy.max_open_positions,
                "daily_loss_limit": router.policy.daily_loss_limit,
                "portfolio_drawdown_limit": router.policy.portfolio_drawdown_limit,
                "reject_duplicate_symbol": router.policy.reject_duplicate_symbol,
            },
        }

    @app.get("/v1/routes")
    def routes() -> dict:
        return {
            "count": len(router.setups),
            "routes": [_setup_payload(x) for x in router.setups],
        }

    @app.get("/v1/routes/{symbol}")
    def route(symbol: str) -> dict:
        setup = router.get(symbol.upper())
        if setup is None:
            raise HTTPException(status_code=404, detail="symbol_not_in_active_pool")
        return _setup_payload(setup)

    @app.post("/v1/portfolio/evaluate")
    def portfolio_evaluate(request: PortfolioRequest) -> dict:
        positions = [
            PositionState(symbol=x.symbol.upper(), weight=x.weight)
            for x in request.open_positions
        ]
        state = evaluate_paper_portfolio(
            router,
            positions=positions,
            daily_pnl=request.daily_pnl,
            portfolio_drawdown=request.portfolio_drawdown,
        )
        return {
            "status": state.status,
            "kill_switch": state.kill_switch,
            "kill_reason": state.kill_reason,
            "open_positions": state.open_positions,
            "gross_exposure": state.gross_exposure,
            "remaining_exposure": state.remaining_exposure,
            "max_gross_exposure": state.max_gross_exposure,
            "max_open_positions": state.max_open_positions,
            "daily_pnl": state.daily_pnl,
            "daily_loss_limit": state.daily_loss_limit,
            "portfolio_drawdown": state.portfolio_drawdown,
            "portfolio_drawdown_limit": state.portfolio_drawdown_limit,
            "violations": list(state.violations),
        }

    @app.post("/v1/admit")
    def admit(request: AdmitRequest) -> dict:
        positions = [
            PositionState(symbol=x.symbol.upper(), weight=x.weight)
            for x in request.open_positions
        ]
        decision = router.admit(
            symbol=request.symbol.upper(),
            side=request.side.upper(),
            open_positions=positions,
            daily_pnl=request.daily_pnl,
            portfolio_drawdown=request.portfolio_drawdown,
        )
        return {
            "allowed": decision.allowed,
            "status": decision.status,
            "reason": decision.reason,
            "symbol": decision.symbol,
            "side": decision.side,
            "weight": decision.weight,
            "timeframe": decision.timeframe,
            "strategy_id": decision.strategy_id,
            "strategy_class": decision.strategy_class,
            "pool_tier": decision.pool_tier,
        }

    return app


app = create_app()
