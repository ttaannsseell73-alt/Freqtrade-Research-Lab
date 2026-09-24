from __future__ import annotations

from dataclasses import dataclass

from .contracts import Timeframe


@dataclass(frozen=True)
class WindowPolicy:
    discovery_days: int
    robustness_days: tuple[int, ...]


WINDOW_POLICY: dict[Timeframe, WindowPolicy] = {
    Timeframe.M1: WindowPolicy(90, (180,)),
    Timeframe.M5: WindowPolicy(90, (180,)),
    Timeframe.M15: WindowPolicy(180, (365,)),
    Timeframe.H1: WindowPolicy(90, (180, 365)),
    Timeframe.H4: WindowPolicy(365, (730,)),
    Timeframe.D1: WindowPolicy(730, (1095,)),
}

CANONICAL_TIMEFRAMES = tuple(WINDOW_POLICY)
