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
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"pmax","candidate":True,"oos_floor_bps":20.0,
         "expectancy_full_15bps":12.0,"profit_factor_holdout":1.3,"trades_full":30},
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"mavilimw","candidate":True,"oos_floor_bps":50.0,
         "expectancy_full_15bps":40.0,"profit_factor_holdout":2.0,"trades_full":25},
    ])
    m365 = _matrix([
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"pmax","candidate":True,"oos_floor_bps":15.0,
         "expectancy_full_15bps":9.0,"profit_factor_holdout":1.2,"trades_full":100},
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"mavilimw","candidate":False,"oos_floor_bps":-5.0,
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
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"pmax","windows_passed":2,"window_ids":"90d,365d",
         "worst_oos_floor_bps":15.0,"median_oos_floor_bps":40.0,"worst_15bps_expectancy":10.0,
         "worst_holdout_profit_factor":1.2,"total_trades":100,"robust":True},
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"squeeze","windows_passed":2,"window_ids":"90d,365d",
         "worst_oos_floor_bps":25.0,"median_oos_floor_bps":26.0,"worst_15bps_expectancy":8.0,
         "worst_holdout_profit_factor":1.1,"total_trades":120,"robust":True},
    ])
    best = select_best_per_coin(rows)
    assert best.iloc[0]["strategy_id"] == "squeeze"


def test_different_timeframes_do_not_count_as_repeated_windows():
    m15 = _matrix([
        {"symbol":"AAAUSDT","timeframe":"15m","strategy_id":"pmax","candidate":True,"oos_floor_bps":20.0,
         "expectancy_full_15bps":12.0,"profit_factor_holdout":1.3,"trades_full":30},
    ])
    m4h = _matrix([
        {"symbol":"AAAUSDT","timeframe":"4h","strategy_id":"pmax","candidate":True,"oos_floor_bps":25.0,
         "expectancy_full_15bps":15.0,"profit_factor_holdout":1.4,"trades_full":30},
    ])
    result = build_robust_assignments([("15m-180d", m15), ("4h-365d", m4h)])
    assert len(result) == 2
    assert not result["robust"].any()
    assert set(result["windows_passed"]) == {1}


def test_primary_setup_can_choose_across_timeframes():
    from coin_strategy_lab.selector import select_best_setup_per_coin

    rows = pd.DataFrame([
        {"symbol":"AAAUSDT","timeframe":"15m","strategy_id":"pmax","windows_passed":2,"window_ids":"a,b",
         "worst_oos_floor_bps":12.0,"median_oos_floor_bps":14.0,"worst_15bps_expectancy":8.0,
         "worst_holdout_profit_factor":1.2,"total_trades":120,"robust":True},
        {"symbol":"AAAUSDT","timeframe":"1h","strategy_id":"mavilimw","windows_passed":2,"window_ids":"a,b",
         "worst_oos_floor_bps":30.0,"median_oos_floor_bps":32.0,"worst_15bps_expectancy":18.0,
         "worst_holdout_profit_factor":1.4,"total_trades":80,"robust":True},
    ])
    best = select_best_setup_per_coin(rows)
    assert len(best) == 1
    assert best.iloc[0]["timeframe"] == "1h"
    assert best.iloc[0]["strategy_id"] == "mavilimw"


def test_confidence_score_caps_raw_bps_across_timeframes():
    from coin_strategy_lab.selector import select_best_setup_per_coin

    rows = pd.DataFrame([
        {
            "symbol":"AAAUSDT","timeframe":"1d","strategy_id":"daily_spike",
            "windows_passed":2,"window_ids":"730d,1095d",
            "worst_oos_floor_bps":5000.0,"median_oos_floor_bps":6000.0,
            "worst_15bps_expectancy":4900.0,"worst_holdout_profit_factor":1.05,
            "total_trades":20,"robust":True,
        },
        {
            "symbol":"AAAUSDT","timeframe":"1h","strategy_id":"hourly_stable",
            "windows_passed":3,"window_ids":"90d,180d,365d",
            "worst_oos_floor_bps":80.0,"median_oos_floor_bps":90.0,
            "worst_15bps_expectancy":70.0,"worst_holdout_profit_factor":1.8,
            "total_trades":300,"robust":True,
        },
    ])
    best = select_best_setup_per_coin(rows)
    assert best.iloc[0]["strategy_id"] == "hourly_stable"
    assert 0.0 <= float(best.iloc[0]["confidence_score"]) <= 100.0
