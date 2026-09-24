from __future__ import annotations

import importlib
import inspect
import pkgutil

from ..contracts import StrategyPlugin


def discover_builtin_strategies() -> tuple[StrategyPlugin, ...]:
    discovered: list[StrategyPlugin] = []
    prefix = __name__ + "."
    for module_info in pkgutil.iter_modules(__path__, prefix):
        if module_info.name.endswith(".__pycache__"):
            continue
        module = importlib.import_module(module_info.name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls is StrategyPlugin or not issubclass(cls, StrategyPlugin):
                continue
            if cls.__module__ != module.__name__:
                continue
            discovered.append(cls())
    return tuple(sorted(discovered, key=lambda s: s.spec.strategy_id))


__all__ = ["discover_builtin_strategies"]
