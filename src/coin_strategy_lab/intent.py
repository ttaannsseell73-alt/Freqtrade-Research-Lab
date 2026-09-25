from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from coin_strategy_lab.runtime import ActiveRouter, PositionState


VALID_DIRECTIONS = {"LONG", "SHORT"}
VALID_LIQUIDITY = {
    "STRONG",
    "TRADEABLE",
    "REVIEW",
    "BLOCK",
    "NO_MARKET_SNAPSHOT",
}


@dataclass(frozen=True)
class EvidencePolicy:
    min_trades: int = 10
    max_profit_factor: float = 8.0
    max_drawdown: float = 0.65
    max_net_return: float = 5.0


DEFAULT_EVIDENCE_POLICY = EvidencePolicy()


@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    timeframe: str
    direction: str
    fresh: bool
    closed_candle: bool
    signal_time_ms: int
    trades: int
    profit_factor: float
    max_drawdown: float
    net_return: float = 0.0
    evidence_consistent: bool = True

    def normalized_direction(self) -> str:
        return self.direction.upper()


@dataclass(frozen=True)
class IntentDecision:
    allowed: bool
    status: str
    reason: str
    symbol: str
    side: str
    weight: float
    intent_id: str | None
    support_count: int
    supporting_signals: tuple[str, ...]
    reviewed_supporters: tuple[str, ...]
    evidence_flags: tuple[str, ...]
    liquidity_status: str
    primary_strategy: str | None
    primary_timeframe: str | None
    primary_signal_time_ms: int | None


def evidence_flags(
    signal: StrategySignal,
    policy: EvidencePolicy = DEFAULT_EVIDENCE_POLICY,
) -> tuple[str, ...]:
    flags: list[str] = []
    if int(signal.trades) < policy.min_trades:
        flags.append("THIN_SAMPLE")
    if float(signal.profit_factor) > policy.max_profit_factor:
        flags.append("EXTREME_PF")
    if abs(float(signal.max_drawdown)) > policy.max_drawdown:
        flags.append("HIGH_DD")
    if float(signal.net_return) > policy.max_net_return:
        flags.append("EXTREME_COMPOUNDING")
    if not bool(signal.evidence_consistent):
        flags.append("INCONSISTENT_EVIDENCE")
    return tuple(flags)


def policy_payload(
    policy: EvidencePolicy = DEFAULT_EVIDENCE_POLICY,
) -> dict:
    return {
        "require_fresh_signal": True,
        "require_closed_candle": True,
        "require_frozen_primary_setup_signal": True,
        "same_direction_multi_strategy": "SINGLE_INTENT_WITH_SUPPORT",
        "opposite_direction": "DIRECTION_CONFLICT",
        "weak_liquidity": "OBSERVE_ONLY",
        "evidence_review": {
            "min_trades": policy.min_trades,
            "max_profit_factor": policy.max_profit_factor,
            "max_drawdown": policy.max_drawdown,
            "max_net_return": policy.max_net_return,
            "inconsistent_evidence": "EVIDENCE_REVIEW",
        },
    }


def _result(
    *,
    allowed: bool,
    status: str,
    reason: str,
    symbol: str,
    side: str,
    weight: float = 0.0,
    intent_id: str | None = None,
    support_count: int = 0,
    supporting_signals: tuple[str, ...] = (),
    reviewed_supporters: tuple[str, ...] = (),
    evidence_flags_value: tuple[str, ...] = (),
    liquidity_status: str = "TRADEABLE",
    primary_strategy: str | None = None,
    primary_timeframe: str | None = None,
    primary_signal_time_ms: int | None = None,
) -> IntentDecision:
    return IntentDecision(
        allowed=allowed,
        status=status,
        reason=reason,
        symbol=symbol,
        side=side,
        weight=weight,
        intent_id=intent_id,
        support_count=support_count,
        supporting_signals=supporting_signals,
        reviewed_supporters=reviewed_supporters,
        evidence_flags=evidence_flags_value,
        liquidity_status=liquidity_status,
        primary_strategy=primary_strategy,
        primary_timeframe=primary_timeframe,
        primary_signal_time_ms=primary_signal_time_ms,
    )


def resolve_active_intent(
    router: ActiveRouter,
    *,
    symbol: str,
    signals: Iterable[StrategySignal],
    liquidity_status: str = "TRADEABLE",
    open_positions: Iterable[PositionState] = (),
    daily_pnl: float = 0.0,
    portfolio_drawdown: float = 0.0,
    evidence_policy: EvidencePolicy = DEFAULT_EVIDENCE_POLICY,
) -> IntentDecision:
    symbol = symbol.upper()
    liquidity_status = liquidity_status.upper()
    if liquidity_status not in VALID_LIQUIDITY:
        raise ValueError(f"Unsupported liquidity_status: {liquidity_status}")

    setup = router.get(symbol)
    if setup is None:
        return _result(
            allowed=False,
            status="NO_TRADE",
            reason="symbol_not_in_active_pool",
            symbol=symbol,
            side="NONE",
            liquidity_status=liquidity_status,
        )

    normalized = [
        StrategySignal(
            strategy_id=s.strategy_id,
            timeframe=s.timeframe,
            direction=s.normalized_direction(),
            fresh=bool(s.fresh),
            closed_candle=bool(s.closed_candle),
            signal_time_ms=int(s.signal_time_ms),
            trades=int(s.trades),
            profit_factor=float(s.profit_factor),
            max_drawdown=float(s.max_drawdown),
            net_return=float(s.net_return),
            evidence_consistent=bool(s.evidence_consistent),
        )
        for s in signals
    ]

    fresh_closed = [
        s for s in normalized
        if s.fresh and s.closed_candle and s.direction in VALID_DIRECTIONS
    ]
    if not fresh_closed:
        return _result(
            allowed=False,
            status="NO_TRADE",
            reason="no_fresh_closed_signal",
            symbol=symbol,
            side="NONE",
            liquidity_status=liquidity_status,
            primary_strategy=setup.strategy_id,
            primary_timeframe=setup.timeframe,
        )

    directions = {s.direction for s in fresh_closed}
    if len(directions) > 1:
        supporters = tuple(sorted({
            f"{s.strategy_id}@{s.timeframe}:{s.direction}"
            for s in fresh_closed
        }))
        return _result(
            allowed=False,
            status="DIRECTION_CONFLICT",
            reason="opposing_fresh_directions",
            symbol=symbol,
            side="CONFLICT",
            supporting_signals=supporters,
            liquidity_status=liquidity_status,
            primary_strategy=setup.strategy_id,
            primary_timeframe=setup.timeframe,
        )

    side = next(iter(directions))
    primary_candidates = [
        s for s in fresh_closed
        if s.strategy_id == setup.strategy_id
        and s.timeframe == setup.timeframe
        and s.direction == side
    ]
    if not primary_candidates:
        return _result(
            allowed=False,
            status="NO_TRADE",
            reason="primary_setup_not_fresh",
            symbol=symbol,
            side=side,
            liquidity_status=liquidity_status,
            primary_strategy=setup.strategy_id,
            primary_timeframe=setup.timeframe,
        )

    primary = max(primary_candidates, key=lambda s: s.signal_time_ms)
    primary_flags = evidence_flags(primary, evidence_policy)
    if primary_flags:
        return _result(
            allowed=False,
            status="EVIDENCE_REVIEW",
            reason="primary_evidence_requires_review",
            symbol=symbol,
            side=side,
            evidence_flags_value=primary_flags,
            liquidity_status=liquidity_status,
            primary_strategy=setup.strategy_id,
            primary_timeframe=setup.timeframe,
            primary_signal_time_ms=primary.signal_time_ms,
        )

    if liquidity_status in {"REVIEW", "BLOCK", "NO_MARKET_SNAPSHOT"}:
        return _result(
            allowed=False,
            status="OBSERVE_ONLY",
            reason="liquidity_not_tradeable",
            symbol=symbol,
            side=side,
            liquidity_status=liquidity_status,
            primary_strategy=setup.strategy_id,
            primary_timeframe=setup.timeframe,
            primary_signal_time_ms=primary.signal_time_ms,
        )

    clean: dict[tuple[str, str], StrategySignal] = {}
    reviewed: list[str] = []
    for signal in fresh_closed:
        flags = evidence_flags(signal, evidence_policy)
        key = (signal.strategy_id, signal.timeframe)
        if flags:
            reviewed.append(
                f"{signal.strategy_id}@{signal.timeframe}:"
                + ",".join(flags)
            )
            continue
        previous = clean.get(key)
        if previous is None or signal.signal_time_ms > previous.signal_time_ms:
            clean[key] = signal

    supporters = tuple(sorted(
        f"{strategy_id}@{timeframe}"
        for strategy_id, timeframe in clean
    ))
    support_count = len(supporters)

    admission = router.admit(
        symbol=symbol,
        side=side,
        open_positions=open_positions,
        daily_pnl=daily_pnl,
        portfolio_drawdown=portfolio_drawdown,
    )
    if not admission.allowed:
        return _result(
            allowed=False,
            status=admission.status,
            reason=admission.reason,
            symbol=symbol,
            side=side,
            supporting_signals=supporters,
            reviewed_supporters=tuple(sorted(reviewed)),
            support_count=support_count,
            liquidity_status=liquidity_status,
            primary_strategy=setup.strategy_id,
            primary_timeframe=setup.timeframe,
            primary_signal_time_ms=primary.signal_time_ms,
        )

    intent_id = (
        f"{router.cohort_id}:{symbol}:{side}:"
        f"{setup.strategy_id}:{setup.timeframe}:{primary.signal_time_ms}"
    )
    return _result(
        allowed=True,
        status="TRADE",
        reason="admitted",
        symbol=symbol,
        side=side,
        weight=admission.weight,
        intent_id=intent_id,
        support_count=support_count,
        supporting_signals=supporters,
        reviewed_supporters=tuple(sorted(reviewed)),
        liquidity_status=liquidity_status,
        primary_strategy=setup.strategy_id,
        primary_timeframe=setup.timeframe,
        primary_signal_time_ms=primary.signal_time_ms,
    )
