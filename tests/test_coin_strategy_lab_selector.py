import pandas as pd

from coin_strategy_lab.selector import (
    RobustnessPolicy,
    build_robust_assignments,
    select_best_per_coin,
)


def _matrix(rows):
    return pd.DataFrame(rows)


def test_robust_assignment_requires_repeatable_edge():
    m90 = _matrix([
        {"symbol":"AAAUSDT","strategy_id":"pmax","candidate":True,"oos_floor_bps":20.0,
         "expectancy_full_15bps":12.0,"profit_factor_holdout":1.3,"trades_full":30},
        {"symbol":"AAAUSDT","strategy_id":"mavilimw","candidate":True,"oos_floor_bps":50.0,
         "expectancy_full_15bps":40.0,"profit_factor_holdout":2.0,"trades_full":25},
    ])
    m365 = _matrix([
        {"symbol":"AAAUSDT","strategy_id":"pmax","candidate":True,"oos_floor_bps":15.0,
         "expectancy_full_15bps":9.0,"profit_factor_holdout":1.2,"trades_full":100},
        {"symbol":"AAAUSDT","strategy_id":"mavilimw","candidate":False,"oos_floor_bps":-5.0,
         "expectancy_full_15bps":8.0,"profit_factor_holdout":0.9,"trades_full":90},
    ])

    result = build_robust_assignments([("90d", m90), ("365d", m365)])
    pmax = result[result["strategy_id"] == "pmax"].iloc[0]
    mavi = result[result["strategy_id"] == "mavilimw"].iloc[0]
    assert bool(pmax["robust"])
    assert pmax["windows_passed"] == 2
    assert pmax["worst_oos_floor_bps"] == 15.0
    assert not bool(mavi["robust"])


def test_best_per_coin_uses_worst_case_oos_not_single_window_peak():
    rows = pd.DataFrame([
        {"symbol":"AAAUSDT","strategy_id":"pmax","windows_passed":2,"window_ids":"90d,365d",
         "worst_oos_floor_bps":15.0,"median_oos_floor_bps":40.0,"worst_15bps_expectancy":10.0,
         "worst_holdout_profit_factor":1.2,"total_trades":100,"robust":True},
        {"symbol":"AAAUSDT","strategy_id":"squeeze","windows_passed":2,"window_ids":"90d,365d",
         "worst_oos_floor_bps":25.0,"median_oos_floor_bps":26.0,"worst_15bps_expectancy":8.0,
         "worst_holdout_profit_factor":1.1,"total_trades":120,"robust":True},
    ])
    best = select_best_per_coin(rows)
    assert best.iloc[0]["strategy_id"] == "squeeze"
