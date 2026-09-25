from pathlib import Path

import pandas as pd

from coin_strategy_lab.active_pool import build_active_pool


def _row(**overrides):
    row = {
        "symbol":"AAAUSDT","pair":"AAA/USDT:USDT","timeframe":"4h",
        "strategy_id":"squeeze_momentum","strategy_class":"CSL4hSqueezeMomentum",
        "status":"EXECUTION_PASS","sample_ok":True,"trades":100,
        "expectancy_bps":50.0,"profit_factor":1.6,
        "max_drawdown_compounded":-0.40,"both_directions_positive":True,
        "long_expectancy":0.01,"short_expectancy":0.02,
        "research_confidence_score":80.0,
    }
    row.update(overrides)
    return row


def test_core_and_active_extension_are_distinct():
    frame = pd.DataFrame([
        _row(symbol="COREUSDT", pair="CORE/USDT:USDT"),
        _row(
            symbol="EXTUSDT", pair="EXT/USDT:USDT",
            max_drawdown_compounded=-0.60, profit_factor=1.40,
        ),
    ])
    result = build_active_pool(frame)
    tiers = dict(zip(result["symbol"], result["pool_tier"]))
    assert tiers["COREUSDT"] == "CORE"
    assert tiers["EXTUSDT"] == "ACTIVE"


def test_one_sided_setup_routes_only_positive_side():
    frame = pd.DataFrame([
        _row(
            long_expectancy=-0.01,
            short_expectancy=0.02,
            both_directions_positive=False,
        )
    ])
    result = build_active_pool(frame)
    assert result.iloc[0]["paper_direction"] == "SHORT_ONLY"
    assert float(result.iloc[0]["paper_weight"]) == 0.015


def test_excessive_drawdown_is_excluded():
    frame = pd.DataFrame([
        _row(max_drawdown_compounded=-0.70)
    ])
    assert build_active_pool(frame).empty
