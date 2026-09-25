from coin_strategy_lab.intent import StrategySignal, resolve_active_intent
from coin_strategy_lab.runtime import (
    ActiveRouter,
    ActiveSetup,
    PositionState,
    RuntimePolicy,
)


def setup(direction="BOTH"):
    return ActiveSetup(
        symbol="AAAUSDT",
        timeframe="4h",
        strategy_id="squeeze_momentum",
        strategy_class="CSL4hSqueezeMomentum",
        pool_tier="CORE",
        direction=direction,
        paper_weight=0.03,
        active_score=80.0,
    )


def router(direction="BOTH"):
    return ActiveRouter(
        [setup(direction)],
        RuntimePolicy(
            max_gross_exposure=0.70,
            max_open_positions=20,
            daily_loss_limit=0.03,
            portfolio_drawdown_limit=0.10,
        ),
        "active-pool-test",
    )


def sig(
    strategy_id="squeeze_momentum",
    timeframe="4h",
    direction="LONG",
    *,
    fresh=True,
    closed_candle=True,
    signal_time_ms=1000,
    trades=30,
    profit_factor=1.8,
    max_drawdown=0.20,
    net_return=0.50,
    evidence_consistent=True,
):
    return StrategySignal(
        strategy_id=strategy_id,
        timeframe=timeframe,
        direction=direction,
        fresh=fresh,
        closed_candle=closed_candle,
        signal_time_ms=signal_time_ms,
        trades=trades,
        profit_factor=profit_factor,
        max_drawdown=max_drawdown,
        net_return=net_return,
        evidence_consistent=evidence_consistent,
    )


def test_same_direction_multi_strategy_creates_one_supported_intent():
    decision = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[
            sig(),
            sig("mavilimw", "1h", signal_time_ms=2000),
        ],
    )
    assert decision.allowed is True
    assert decision.status == "TRADE"
    assert decision.side == "LONG"
    assert decision.support_count == 2
    assert decision.weight == 0.03
    assert decision.intent_id is not None
    assert "squeeze_momentum@4h" in decision.supporting_signals
    assert "mavilimw@1h" in decision.supporting_signals


def test_duplicate_support_signal_does_not_inflate_support_count():
    decision = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[
            sig(),
            sig("mavilimw", "1h", signal_time_ms=2000),
            sig("mavilimw", "1h", signal_time_ms=2000),
        ],
    )
    assert decision.allowed is True
    assert decision.support_count == 2


def test_opposite_fresh_directions_fail_closed():
    decision = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[
            sig(direction="LONG"),
            sig("mavilimw", "1h", direction="SHORT"),
        ],
    )
    assert decision.allowed is False
    assert decision.status == "DIRECTION_CONFLICT"
    assert decision.reason == "opposing_fresh_directions"


def test_old_or_open_candle_signal_cannot_create_intent():
    stale = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(fresh=False)],
    )
    open_bar = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(closed_candle=False)],
    )
    assert stale.reason == "no_fresh_closed_signal"
    assert open_bar.reason == "no_fresh_closed_signal"


def test_support_strategy_cannot_replace_frozen_primary_setup():
    decision = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig("mavilimw", "1h")],
    )
    assert decision.allowed is False
    assert decision.reason == "primary_setup_not_fresh"


def test_extreme_pf_or_thin_primary_is_evidence_review():
    pf = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(profit_factor=9.0)],
    )
    thin = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(trades=5)],
    )
    assert pf.status == "EVIDENCE_REVIEW"
    assert "EXTREME_PF" in pf.evidence_flags
    assert thin.status == "EVIDENCE_REVIEW"
    assert "THIN_SAMPLE" in thin.evidence_flags


def test_inconsistent_primary_evidence_is_review_only():
    decision = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(evidence_consistent=False)],
    )
    assert decision.status == "EVIDENCE_REVIEW"
    assert "INCONSISTENT_EVIDENCE" in decision.evidence_flags


def test_weak_liquidity_is_observe_only():
    decision = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig()],
        liquidity_status="REVIEW",
    )
    assert decision.allowed is False
    assert decision.status == "OBSERVE_ONLY"
    assert decision.reason == "liquidity_not_tradeable"


def test_frozen_direction_and_risk_rules_still_apply_after_signal_gate():
    wrong = resolve_active_intent(
        router("SHORT_ONLY"),
        symbol="AAAUSDT",
        signals=[sig(direction="LONG")],
    )
    killed = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(direction="LONG")],
        daily_pnl=-0.031,
    )
    duplicate = resolve_active_intent(
        router(),
        symbol="AAAUSDT",
        signals=[sig(direction="LONG")],
        open_positions=[PositionState("AAAUSDT", 0.03)],
    )
    assert wrong.reason == "direction_not_enabled"
    assert killed.status == "KILL_SWITCH"
    assert duplicate.reason == "duplicate_symbol"
