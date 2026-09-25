import json

from coin_strategy_lab.runtime import (
    ActiveRouter,
    ActiveSetup,
    PositionState,
    RuntimePolicy,
)


def setup(symbol="AAAUSDT", direction="BOTH", weight=0.03, tier="CORE"):
    return ActiveSetup(
        symbol=symbol,
        timeframe="4h",
        strategy_id="squeeze_momentum",
        strategy_class="CSL4hSqueezeMomentum",
        pool_tier=tier,
        direction=direction,
        paper_weight=weight,
        active_score=80.0,
    )


def test_admits_active_symbol_and_weight():
    router = ActiveRouter(
        [setup()],
        RuntimePolicy(max_gross_exposure=0.70),
        "test",
    )
    decision = router.admit("AAAUSDT", "LONG")
    assert decision.allowed
    assert decision.status == "TRADE"
    assert decision.weight == 0.03


def test_rejects_wrong_direction():
    router = ActiveRouter(
        [setup(direction="SHORT_ONLY")],
        RuntimePolicy(),
        "test",
    )
    decision = router.admit("AAAUSDT", "LONG")
    assert not decision.allowed
    assert decision.reason == "direction_not_enabled"


def test_rejects_duplicate_symbol():
    router = ActiveRouter([setup()], RuntimePolicy(), "test")
    decision = router.admit(
        "AAAUSDT",
        "LONG",
        [PositionState("AAAUSDT", 0.03)],
    )
    assert not decision.allowed
    assert decision.reason == "duplicate_symbol"


def test_daily_loss_kill_switch():
    router = ActiveRouter([setup()], RuntimePolicy(daily_loss_limit=0.03), "test")
    decision = router.admit("AAAUSDT", "LONG", daily_pnl=-0.031)
    assert not decision.allowed
    assert decision.status == "KILL_SWITCH"
    assert decision.reason == "daily_loss_limit"


def test_portfolio_drawdown_kill_switch():
    router = ActiveRouter(
        [setup()],
        RuntimePolicy(portfolio_drawdown_limit=0.10),
        "test",
    )
    decision = router.admit(
        "AAAUSDT",
        "LONG",
        portfolio_drawdown=-0.101,
    )
    assert not decision.allowed
    assert decision.reason == "portfolio_drawdown_limit"


def test_gross_exposure_limit():
    router = ActiveRouter(
        [setup(weight=0.03)],
        RuntimePolicy(max_gross_exposure=0.70),
        "test",
    )
    positions = [
        PositionState(f"P{i}USDT", 0.05) for i in range(14)
    ]
    decision = router.admit("AAAUSDT", "LONG", positions)
    assert not decision.allowed
    assert decision.reason == "gross_exposure_limit"
