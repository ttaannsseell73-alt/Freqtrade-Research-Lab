import pandas as pd

from coin_strategy_lab.risk_filter import classify_risk


def _row(**overrides):
    row = {
        "symbol": "AAAUSDT",
        "timeframe": "4h",
        "strategy_id": "squeeze_momentum",
        "status": "EXECUTION_PASS",
        "trades": 100,
        "expectancy_bps": 50.0,
        "profit_factor": 1.8,
        "max_drawdown_compounded": -0.30,
        "both_directions_positive": True,
        "long_expectancy": 0.01,
        "short_expectancy": 0.01,
        "research_confidence_score": 90.0,
    }
    row.update(overrides)
    return row


def test_core_requires_all_hard_gates():
    result = classify_risk(pd.DataFrame([_row()]))
    assert result.iloc[0]["risk_tier"] == "CORE"
    assert result.iloc[0]["paper_direction"] == "BOTH"


def test_single_direction_positive_is_watch_not_core():
    result = classify_risk(pd.DataFrame([
        _row(
            both_directions_positive=False,
            long_expectancy=-0.001,
            short_expectancy=0.02,
        )
    ]))
    assert result.iloc[0]["risk_tier"] == "WATCH"
    assert result.iloc[0]["paper_direction"] == "SHORT_ONLY"


def test_drawdown_above_watch_limit_is_rejected():
    result = classify_risk(pd.DataFrame([
        _row(max_drawdown_compounded=-0.60)
    ]))
    assert result.iloc[0]["risk_tier"] == "REJECT"


def test_core_boundary_is_inclusive():
    result = classify_risk(pd.DataFrame([
        _row(
            max_drawdown_compounded=-0.45,
            profit_factor=1.50,
            expectancy_bps=20.0,
        )
    ]))
    assert result.iloc[0]["risk_tier"] == "CORE"
