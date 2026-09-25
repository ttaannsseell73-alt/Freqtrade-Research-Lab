from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


ALLOWED_DIRECTIONS = {"BOTH", "LONG_ONLY", "SHORT_ONLY"}


@dataclass(frozen=True)
class ActiveSetup:
    symbol: str
    timeframe: str
    strategy_id: str
    strategy_class: str
    pool_tier: str
    direction: str
    paper_weight: float
    active_score: float

    @classmethod
    def from_dict(cls, item: dict) -> "ActiveSetup":
        direction = str(item.get("direction", "BOTH"))
        if direction not in ALLOWED_DIRECTIONS:
            raise ValueError(f"Unsupported direction: {direction}")
        weight = float(item["paper_weight"])
        if weight <= 0.0:
            raise ValueError("paper_weight must be positive")
        return cls(
            symbol=str(item["symbol"]),
            timeframe=str(item["timeframe"]),
            strategy_id=str(item["strategy_id"]),
            strategy_class=str(item["freqtrade_strategy_class"]),
            pool_tier=str(item["pool_tier"]),
            direction=direction,
            paper_weight=weight,
            active_score=float(item["active_score"]),
        )


@dataclass(frozen=True)
class RuntimePolicy:
    max_gross_exposure: float = 0.70
    max_open_positions: int = 20
    daily_loss_limit: float = 0.03
    portfolio_drawdown_limit: float = 0.10
    reject_duplicate_symbol: bool = True

    @classmethod
    def from_dict(cls, item: dict) -> "RuntimePolicy":
        return cls(
            max_gross_exposure=float(item.get("max_gross_exposure", 0.70)),
            max_open_positions=int(item.get("max_open_positions", 20)),
            daily_loss_limit=float(item.get("daily_loss_limit", 0.03)),
            portfolio_drawdown_limit=float(
                item.get("portfolio_drawdown_limit", 0.10)
            ),
            reject_duplicate_symbol=bool(
                item.get("reject_duplicate_symbol", True)
            ),
        )


@dataclass(frozen=True)
class PositionState:
    symbol: str
    weight: float


@dataclass(frozen=True)
class AdmissionDecision:
    allowed: bool
    status: str
    reason: str
    symbol: str
    side: str
    weight: float
    timeframe: str | None = None
    strategy_id: str | None = None
    strategy_class: str | None = None
    pool_tier: str | None = None


class ActiveRouter:
    def __init__(
        self,
        setups: Iterable[ActiveSetup],
        policy: RuntimePolicy,
        cohort_id: str,
    ):
        by_symbol: dict[str, ActiveSetup] = {}
        for setup in setups:
            if setup.symbol in by_symbol:
                raise ValueError(f"Duplicate active symbol: {setup.symbol}")
            by_symbol[setup.symbol] = setup
        self._setups = by_symbol
        self.policy = policy
        self.cohort_id = cohort_id

    @classmethod
    def from_files(
        cls,
        cohort_path: Path,
        runtime_policy_path: Path,
    ) -> "ActiveRouter":
        cohort = json.loads(cohort_path.read_text(encoding="utf-8"))
        runtime = json.loads(runtime_policy_path.read_text(encoding="utf-8"))
        setups = [ActiveSetup.from_dict(x) for x in cohort["setups"]]
        policy = RuntimePolicy.from_dict(runtime["policy"])
        router = cls(setups, policy, str(cohort["cohort_id"]))
        router.validate_contract(runtime)
        return router

    def validate_contract(self, runtime_config: dict) -> None:
        expected = int(runtime_config.get("expected_setup_count", 0))
        if expected and len(self._setups) != expected:
            raise ValueError(
                f"Expected {expected} active setups, got {len(self._setups)}"
            )
        expected_core = int(runtime_config.get("expected_core_count", 0))
        if expected_core:
            actual_core = sum(
                1 for x in self._setups.values() if x.pool_tier == "CORE"
            )
            if actual_core != expected_core:
                raise ValueError(
                    f"Expected {expected_core} CORE setups, got {actual_core}"
                )
        configured_weight = sum(x.paper_weight for x in self._setups.values())
        if configured_weight > self.policy.max_gross_exposure + 1e-12:
            raise ValueError(
                "Configured setup weights exceed runtime max_gross_exposure: "
                f"{configured_weight:.6f} > {self.policy.max_gross_exposure:.6f}"
            )

    @property
    def setups(self) -> list[ActiveSetup]:
        return sorted(
            self._setups.values(),
            key=lambda x: (x.pool_tier != "CORE", -x.active_score, x.symbol),
        )

    @property
    def configured_weight(self) -> float:
        return sum(x.paper_weight for x in self._setups.values())

    def get(self, symbol: str) -> ActiveSetup | None:
        return self._setups.get(symbol)

    @staticmethod
    def _side_allowed(direction: str, side: str) -> bool:
        side = side.upper()
        if side not in {"LONG", "SHORT"}:
            return False
        if direction == "BOTH":
            return True
        if direction == "LONG_ONLY":
            return side == "LONG"
        if direction == "SHORT_ONLY":
            return side == "SHORT"
        return False

    def admit(
        self,
        symbol: str,
        side: str,
        open_positions: Iterable[PositionState] = (),
        daily_pnl: float = 0.0,
        portfolio_drawdown: float = 0.0,
    ) -> AdmissionDecision:
        side = side.upper()
        setup = self.get(symbol)
        if setup is None:
            return AdmissionDecision(
                False, "NO_TRADE", "symbol_not_in_active_pool",
                symbol, side, 0.0,
            )

        if not self._side_allowed(setup.direction, side):
            return AdmissionDecision(
                False, "NO_TRADE", "direction_not_enabled",
                symbol, side, 0.0,
                setup.timeframe, setup.strategy_id,
                setup.strategy_class, setup.pool_tier,
            )

        positions = list(open_positions)
        if daily_pnl <= -abs(self.policy.daily_loss_limit):
            return AdmissionDecision(
                False, "KILL_SWITCH", "daily_loss_limit",
                symbol, side, 0.0,
                setup.timeframe, setup.strategy_id,
                setup.strategy_class, setup.pool_tier,
            )

        if portfolio_drawdown <= -abs(self.policy.portfolio_drawdown_limit):
            return AdmissionDecision(
                False, "KILL_SWITCH", "portfolio_drawdown_limit",
                symbol, side, 0.0,
                setup.timeframe, setup.strategy_id,
                setup.strategy_class, setup.pool_tier,
            )

        if len(positions) >= self.policy.max_open_positions:
            return AdmissionDecision(
                False, "NO_TRADE", "max_open_positions",
                symbol, side, 0.0,
                setup.timeframe, setup.strategy_id,
                setup.strategy_class, setup.pool_tier,
            )

        if (
            self.policy.reject_duplicate_symbol
            and any(p.symbol == symbol for p in positions)
        ):
            return AdmissionDecision(
                False, "NO_TRADE", "duplicate_symbol",
                symbol, side, 0.0,
                setup.timeframe, setup.strategy_id,
                setup.strategy_class, setup.pool_tier,
            )

        gross = sum(max(float(p.weight), 0.0) for p in positions)
        if gross + setup.paper_weight > self.policy.max_gross_exposure + 1e-12:
            return AdmissionDecision(
                False, "NO_TRADE", "gross_exposure_limit",
                symbol, side, 0.0,
                setup.timeframe, setup.strategy_id,
                setup.strategy_class, setup.pool_tier,
            )

        return AdmissionDecision(
            True, "TRADE", "admitted",
            symbol, side, setup.paper_weight,
            setup.timeframe, setup.strategy_id,
            setup.strategy_class, setup.pool_tier,
        )
