import json
from pathlib import Path

import pandas as pd
import pytest

from freqtrade_research_lab.execution_runner import ExecutionRunConfig
from freqtrade_research_lab.scalping_suite import (
    DEFAULT_STRATEGIES,
    ScalpingSuiteConfig,
    _execution_config as build_suite_execution_config,
    _find_reusable_run,
    _status,
)


def _execution_config(tmp_path: Path) -> ExecutionRunConfig:
    strategies = tmp_path / "strategies"
    strategies.mkdir(exist_ok=True)
    (strategies / "Example.py").write_text("class Example:\n    pass\n", encoding="utf-8")
    return ExecutionRunConfig(
        config_path=tmp_path / "config.json",
        data_dir=tmp_path / "data",
        catalog_path=tmp_path / "catalog.csv",
        strategy_path=strategies,
        output_dir=tmp_path / "new-run",
        strategy_name="Example",
        timeframe="1m",
        start="2025-09-20",
        end="2026-09-20",
        max_pairs=2,
        pair_offset=0,
    )


def test_status_is_diagnostic_not_automatic_promotion() -> None:
    assert _status({"trades": 100, "expectancy": 0.001, "profit_factor": 1.2}) == "POSITIVE_SMOKE"
    assert _status({"trades": 100, "expectancy": -0.001, "profit_factor": 1.2}) == "NEGATIVE_SMOKE"
    assert _status({"trades": 0}) == "NO_TRADES"


def test_scalping_suite_config_rejects_bad_batch_values(tmp_path: Path) -> None:
    kwargs = dict(
        config_path=tmp_path / "config.json",
        data_dir=tmp_path / "data",
        catalog_1m=tmp_path / "1m.csv",
        catalog_5m=tmp_path / "5m.csv",
        strategy_path=tmp_path / "strategies",
        output_dir=tmp_path / "out",
        start="2025-09-20",
        end="2026-09-20",
    )
    with pytest.raises(ValueError, match="max_pairs"):
        ScalpingSuiteConfig(**kwargs, max_pairs=0)
    with pytest.raises(ValueError, match="pair_offset"):
        ScalpingSuiteConfig(**kwargs, pair_offset=-1)


def test_reuse_requires_exact_manifest_strategy_hash_and_pair_list(tmp_path: Path) -> None:
    run_config = _execution_config(tmp_path)
    reuse = tmp_path / "reuse" / "exact"
    (reuse / "backtest").mkdir(parents=True)
    (reuse / "backtest" / "result.zip").write_bytes(b"placeholder")
    pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    (reuse / "pairs.txt").write_text("\n".join(pairs) + "\n", encoding="utf-8")

    import hashlib

    strategy_bytes = (run_config.strategy_path / "Example.py").read_bytes()
    strategy_hash = hashlib.sha256(strategy_bytes).hexdigest()
    manifest = {
        "strategy": "Example",
        "strategy_sha256": strategy_hash,
        "timeframe": "1m",
        "start_inclusive": pd.Timestamp("2025-09-20", tz="UTC").isoformat(),
        "end_exclusive": pd.Timestamp("2026-09-20", tz="UTC").isoformat(),
        "pair_count": 2,
        "pair_offset": 0,
        "fee_per_side": 0.0005,
        "post_backtest_slippage_bps_round_trip": 4.0,
    }
    (reuse / "run_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    found = _find_reusable_run(
        tmp_path / "reuse",
        run_config=run_config,
        expected_pairs=pairs,
    )
    assert found is not None
    assert found[0] == reuse

    (reuse / "pairs.txt").write_text("BTC/USDT:USDT\n", encoding="utf-8")
    assert (
        _find_reusable_run(
            tmp_path / "reuse",
            run_config=run_config,
            expected_pairs=pairs,
        )
        is None
    )



def test_default_suite_includes_bos_choch() -> None:
    assert "BosChochScalp" in DEFAULT_STRATEGIES



def test_default_suite_includes_locked_kivanc_batch() -> None:
    expected = {
        "TurtleTradeChannelsScalp",
        "IsolatedPeakBottomScalp",
        "VolumeBasedColouredBarsScalp",
        "FollowLineScalp",
        "SqueezeMomentumV2Scalp",
        "ProgressiveTrendTrackerScalp",
        "TurtleVhfFilteredScalp",
    }
    assert expected.issubset(set(DEFAULT_STRATEGIES))



def test_suite_uses_1m_detail_for_5m_runs(tmp_path: Path) -> None:
    strategies = tmp_path / "strategies"
    strategies.mkdir()
    suite = ScalpingSuiteConfig(
        config_path=tmp_path / "config.json",
        data_dir=tmp_path / "data",
        catalog_1m=tmp_path / "1m.csv",
        catalog_5m=tmp_path / "5m.csv",
        strategy_path=strategies,
        output_dir=tmp_path / "out",
        start="2025-09-20",
        end="2026-09-20",
    )

    one_minute = build_suite_execution_config(
        suite,
        strategy="Example",
        timeframe="1m",
        output_dir=tmp_path / "run-1m",
    )
    five_minute = build_suite_execution_config(
        suite,
        strategy="Example",
        timeframe="5m",
        output_dir=tmp_path / "run-5m",
    )

    assert one_minute.timeframe_detail is None
    assert five_minute.timeframe_detail == "1m"


def test_reuse_rejects_wrong_timeframe_detail(tmp_path: Path) -> None:
    run_config = _execution_config(tmp_path)
    reuse = tmp_path / "reuse-detail" / "candidate"
    (reuse / "backtest").mkdir(parents=True)
    (reuse / "backtest" / "result.zip").write_bytes(b"placeholder")
    pairs = ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    (reuse / "pairs.txt").write_text("\n".join(pairs) + "\n", encoding="utf-8")

    import hashlib

    strategy_hash = hashlib.sha256(
        (run_config.strategy_path / "Example.py").read_bytes()
    ).hexdigest()
    manifest = {
        "strategy": "Example",
        "strategy_sha256": strategy_hash,
        "timeframe": "1m",
        "timeframe_detail": "5m",
        "start_inclusive": pd.Timestamp("2025-09-20", tz="UTC").isoformat(),
        "end_exclusive": pd.Timestamp("2026-09-20", tz="UTC").isoformat(),
        "pair_count": 2,
        "pair_offset": 0,
        "fee_per_side": 0.0005,
        "post_backtest_slippage_bps_round_trip": 4.0,
    }
    (reuse / "run_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    assert (
        _find_reusable_run(
            tmp_path / "reuse-detail",
            run_config=run_config,
            expected_pairs=pairs,
        )
        is None
    )
