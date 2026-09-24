from __future__ import annotations

from collections.abc import Iterable

from .contracts import StrategyPlugin


class StrategyRegistry:
    def __init__(self, plugins: Iterable[StrategyPlugin] = ()) -> None:
        self._plugins: dict[str, StrategyPlugin] = {}
        for plugin in plugins:
            self.register(plugin)

    def register(self, plugin: StrategyPlugin) -> None:
        key = plugin.spec.strategy_id
        if key in self._plugins:
            old = self._plugins[key]
            raise ValueError(
                f"Strategy id {key!r} already registered "
                f"(existing={old.spec.version}, new={plugin.spec.version})"
            )
        self._plugins[key] = plugin

    def get(self, strategy_id: str) -> StrategyPlugin:
        try:
            return self._plugins[strategy_id]
        except KeyError as exc:
            raise KeyError(f"Unknown strategy: {strategy_id}") from exc

    def all(self) -> tuple[StrategyPlugin, ...]:
        return tuple(self._plugins[k] for k in sorted(self._plugins))

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))
