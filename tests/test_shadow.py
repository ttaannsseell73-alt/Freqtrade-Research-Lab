import pandas as pd

from coin_strategy_lab.runtime import ActiveRouter, ActiveSetup, RuntimePolicy
from coin_strategy_lab.shadow import (
    SignalEvent,
    SymbolScan,
    apply_shadow_scan,
    empty_shadow_state,
    extract_signal_events,
)


class FakePlugin:
    def prepare(self, candles):
        return candles.copy()

    def long_entries(self, prepared):
        return prepared["close"].eq(3.0)

    def short_entries(self, prepared):
        return prepared["close"].eq(2.0)


class FakeRegistry:
    def get(self, strategy_id):
        return FakePlugin()


def setup(direction="BOTH", weight=0.03):
    return ActiveSetup(
        symbol="AAAUSDT",
        timeframe="1h",
        strategy_id="fake",
        strategy_class="Fake",
        pool_tier="CORE",
        direction=direction,
        paper_weight=weight,
        active_score=80.0,
    )


def router(direction="BOTH", weight=0.03):
    return ActiveRouter(
        [setup(direction, weight)],
        RuntimePolicy(
            max_gross_exposure=0.70,
            max_open_positions=20,
            daily_loss_limit=0.03,
            portfolio_drawdown_limit=0.10,
        ),
        "shadow-test",
    )


def evidence():
    return {
        "trades": 100,
        "profit_factor": 1.8,
        "max_drawdown": 0.20,
        "sample_ok": True,
        "status": "EXECUTION_PASS",
    }


def scan(events, *, liquidity="TRADEABLE", mark=100.0, latest=3000):
    return SymbolScan(
        symbol="AAAUSDT",
        latest_closed_bar_ms=latest,
        events=tuple(events),
        liquidity_status=liquidity,
        mark_price=mark,
        evidence=evidence(),
        market={},
    )


def test_extract_signal_uses_latest_closed_bar_and_next_bar_open():
    candles = pd.DataFrame(
        [
            {"open_time_ms": 1000, "date": pd.Timestamp("2026-01-01T00:00Z"),
             "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.0, "volume": 10.0, "is_closed": True},
            {"open_time_ms": 2000, "date": pd.Timestamp("2026-01-01T01:00Z"),
             "open": 1.0, "high": 2.1, "low": 0.9, "close": 2.0, "volume": 10.0, "is_closed": True},
            {"open_time_ms": 3000, "date": pd.Timestamp("2026-01-01T02:00Z"),
             "open": 2.0, "high": 3.1, "low": 1.9, "close": 3.0, "volume": 10.0, "is_closed": True},
            {"open_time_ms": 4000, "date": pd.Timestamp("2026-01-01T03:00Z"),
             "open": 3.25, "high": 3.3, "low": 3.0, "close": 3.2, "volume": 10.0, "is_closed": False},
        ]
    )
    events, latest = extract_signal_events(
        setup(),
        candles,
        registry=FakeRegistry(),
    )
    assert latest == 3000
    assert len(events) == 1
    assert events[0].direction == "LONG"
    assert events[0].signal_time_ms == 3000
    assert events[0].entry_time_ms == 4000
    assert events[0].entry_price == 3.25


def test_shadow_opens_once_and_suppresses_same_direction_duplicate():
    r = router()
    event = SignalEvent("AAAUSDT", "LONG", 1000, 2000, 100.0, "fake", "1h")
    first = apply_shadow_scan(r, None, [scan([event], mark=101.0)], now_ms=3000)
    assert len(first["positions"]) == 1
    assert first["positions"][0]["side"] == "LONG"

    second = apply_shadow_scan(r, first, [scan([event], mark=102.0)], now_ms=4000)
    assert len(second["positions"]) == 1
    assert len([x for x in second["events"] if x["type"] == "OPEN"]) == 1
    assert second["events"][-1]["type"] == "DUPLICATE_SUPPRESSED"


def test_opposite_signal_closes_and_reverses_when_both_allowed():
    r = router()
    long_event = SignalEvent("AAAUSDT", "LONG", 1000, 2000, 100.0, "fake", "1h")
    state = apply_shadow_scan(r, None, [scan([long_event], mark=101.0)], now_ms=3000)

    short_event = SignalEvent("AAAUSDT", "SHORT", 4000, 5000, 105.0, "fake", "1h")
    state = apply_shadow_scan(r, state, [scan([short_event], mark=104.0, latest=4000)], now_ms=6000)

    assert len(state["closed_trades"]) == 1
    assert state["closed_trades"][0]["exit_reason"] == "OPPOSITE_SIGNAL"
    assert len(state["positions"]) == 1
    assert state["positions"][0]["side"] == "SHORT"


def test_one_sided_setup_exits_on_opposite_but_does_not_reverse():
    r = router(direction="SHORT_ONLY")
    short_event = SignalEvent("AAAUSDT", "SHORT", 1000, 2000, 100.0, "fake", "1h")
    state = apply_shadow_scan(r, None, [scan([short_event], mark=99.0)], now_ms=3000)
    assert state["positions"][0]["side"] == "SHORT"

    long_event = SignalEvent("AAAUSDT", "LONG", 4000, 5000, 95.0, "fake", "1h")
    state = apply_shadow_scan(r, state, [scan([long_event], mark=96.0, latest=4000)], now_ms=6000)
    assert state["positions"] == []
    assert state["closed_trades"][-1]["exit_reason"] == "OPPOSITE_SIGNAL"
    assert state["events"][-1]["reason"] == "direction_not_enabled"


def test_review_liquidity_is_observe_only():
    r = router()
    event = SignalEvent("AAAUSDT", "LONG", 1000, 2000, 100.0, "fake", "1h")
    state = apply_shadow_scan(
        r,
        None,
        [scan([event], liquidity="REVIEW", mark=101.0)],
        now_ms=3000,
    )
    assert state["positions"] == []
    assert state["events"][-1]["type"] == "OBSERVE_ONLY"


def test_mark_to_market_drawdown_triggers_kill_and_flattens():
    r = router(weight=0.50)
    state = empty_shadow_state(r.cohort_id, 1000)
    state["positions"] = [
        {
            "symbol": "AAAUSDT",
            "side": "LONG",
            "weight": 0.50,
            "entry_price": 100.0,
            "entry_time_ms": 1000,
            "signal_time_ms": 500,
            "strategy_id": "fake",
            "timeframe": "1h",
            "pool_tier": "CORE",
            "intent_id": "x",
            "support_count": 1,
            "liquidity_status": "TRADEABLE",
        }
    ]
    state = apply_shadow_scan(
        r,
        state,
        [scan([], mark=70.0)],
        now_ms=2000,
    )
    assert state["kill_switch_latched"] is True
    assert state["kill_reason"] in {"daily_loss_limit", "portfolio_drawdown_limit"}
    assert state["positions"] == []
    assert state["closed_trades"][-1]["exit_reason"].startswith("KILL_SWITCH:")
