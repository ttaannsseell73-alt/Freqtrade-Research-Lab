from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from coin_strategy_lab.intent import (
    StrategySignal,
    policy_payload,
    resolve_active_intent,
)
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


class StrategySignalInput(BaseModel):
    strategy_id: str
    timeframe: str
    direction: str
    fresh: bool
    closed_candle: bool
    signal_time_ms: int = Field(gt=0)
    trades: int = Field(ge=0)
    profit_factor: float = Field(ge=0.0)
    max_drawdown: float
    net_return: float = 0.0
    evidence_consistent: bool = True


class IntentRequest(BaseModel):
    symbol: str
    signals: list[StrategySignalInput] = Field(min_length=1)
    liquidity_status: str = "TRADEABLE"
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


def _execution_capabilities() -> dict:
    key_present = bool(os.getenv("BINANCE_TESTNET_API_KEY", "").strip())
    secret_present = bool(os.getenv("BINANCE_TESTNET_API_SECRET", "").strip())
    credentials_present = key_present and secret_present
    order_arm = os.getenv("CSL_ALLOW_TESTNET_ORDERS", "").upper() == "YES"
    return {
        "adapter": "binance_usdm_testnet",
        "testnet_only": True,
        "live_endpoint_blocked": True,
        "one_way_mode_required": True,
        "credentials_present": credentials_present,
        "orders_armed": credentials_present and order_arm,
        "authenticated_reconcile_verified": False,
        "full_order_smoke_verified": False,
        "status": (
            "ARMED_NOT_VERIFIED"
            if credentials_present and order_arm
            else "CREDENTIALS_READY_NOT_VERIFIED"
            if credentials_present
            else "WAITING_FOR_TESTNET_CREDENTIALS"
        ),
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

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
            "intent_gate": policy_payload(),
        }

    @app.get("/v1/intent/policy")
    def intent_policy() -> dict:
        return policy_payload()

    @app.post("/v1/intent/admit")
    def intent_admit(request: IntentRequest) -> dict:
        positions = [
            PositionState(symbol=x.symbol.upper(), weight=x.weight)
            for x in request.open_positions
        ]
        signals = [
            StrategySignal(
                strategy_id=x.strategy_id,
                timeframe=x.timeframe,
                direction=x.direction,
                fresh=x.fresh,
                closed_candle=x.closed_candle,
                signal_time_ms=x.signal_time_ms,
                trades=x.trades,
                profit_factor=x.profit_factor,
                max_drawdown=x.max_drawdown,
                net_return=x.net_return,
                evidence_consistent=x.evidence_consistent,
            )
            for x in request.signals
        ]
        try:
            decision = resolve_active_intent(
                router,
                symbol=request.symbol,
                signals=signals,
                liquidity_status=request.liquidity_status,
                open_positions=positions,
                daily_pnl=request.daily_pnl,
                portfolio_drawdown=request.portfolio_drawdown,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "allowed": decision.allowed,
            "status": decision.status,
            "reason": decision.reason,
            "symbol": decision.symbol,
            "side": decision.side,
            "weight": decision.weight,
            "intent_id": decision.intent_id,
            "support_count": decision.support_count,
            "supporting_signals": list(decision.supporting_signals),
            "reviewed_supporters": list(decision.reviewed_supporters),
            "evidence_flags": list(decision.evidence_flags),
            "liquidity_status": decision.liquidity_status,
            "primary_strategy": decision.primary_strategy,
            "primary_timeframe": decision.primary_timeframe,
            "primary_signal_time_ms": decision.primary_signal_time_ms,
        }

    @app.get("/v1/execution/capabilities")
    def execution_capabilities() -> dict:
        return _execution_capabilities()

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
