from __future__ import annotations

import argparse
import json
from pathlib import Path

from coin_strategy_lab import StrategyRegistry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/STRATEGY_REGISTRY.json"))
    args = parser.parse_args()

    registry = StrategyRegistry.discover_builtins()
    rows = []
    for plugin in registry.all():
        spec = plugin.spec
        rows.append(
            {
                "strategy_id": spec.strategy_id,
                "name": spec.name,
                "version": spec.version,
                "supported_timeframes": [tf.value for tf in spec.supported_timeframes],
                "warmup_bars": spec.warmup_bars,
                "supports_long": spec.supports_long,
                "supports_short": spec.supports_short,
                "default_parameters": dict(spec.default_parameters),
                "source_url": spec.source_url,
                "module": plugin.__class__.__module__,
                "class": plugin.__class__.__name__,
            }
        )

    payload = {"strategy_count": len(rows), "strategies": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
