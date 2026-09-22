from pathlib import Path

from freqtrade_research_lab.external_freqtrade_suite import (
    DEFAULT_EXTERNAL_STRATEGIES,
    ExternalSuiteConfig,
    _execution_config,
    git_blob_sha1,
)


def test_git_blob_sha1_matches_git_object_format() -> None:
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_external_specs_are_unique_and_pinned() -> None:
    names = [spec.name for spec in DEFAULT_EXTERNAL_STRATEGIES]
    assert len(names) == len(set(names))
    assert {"Scalp", "SmoothScalp", "ReinforcedSmoothScalp"}.issubset(names)
    assert {"GeneticEngineV1", "EwoMomentumV1"}.issubset(names)

    for spec in DEFAULT_EXTERNAL_STRATEGIES:
        assert len(spec.commit) == 40
        assert len(spec.blob_sha1) == 40
        assert spec.timeframe in {"1m", "5m"}
        assert "main" not in spec.commit


def test_external_execution_uses_native_timeframe_and_1m_detail_for_5m(
    tmp_path: Path,
) -> None:
    config = ExternalSuiteConfig(
        config_path=tmp_path / "config.json",
        data_dir=tmp_path / "data",
        catalog_1m=tmp_path / "1m.csv",
        catalog_5m=tmp_path / "5m.csv",
        output_dir=tmp_path / "out",
        start="2025-09-20",
        end="2026-09-20",
    )
    sources = tmp_path / "sources"

    scalp = next(spec for spec in DEFAULT_EXTERNAL_STRATEGIES if spec.name == "Scalp")
    genetic = next(
        spec for spec in DEFAULT_EXTERNAL_STRATEGIES if spec.name == "GeneticEngineV1"
    )

    run_1m = _execution_config(
        config,
        spec=scalp,
        strategy_path=sources,
        output_dir=tmp_path / "run-1m",
    )
    run_5m = _execution_config(
        config,
        spec=genetic,
        strategy_path=sources,
        output_dir=tmp_path / "run-5m",
    )

    assert run_1m.timeframe == "1m"
    assert run_1m.timeframe_detail is None
    assert run_5m.timeframe == "5m"
    assert run_5m.timeframe_detail == "1m"


def test_external_suite_rejects_invalid_batch_values(tmp_path: Path) -> None:
    kwargs = dict(
        config_path=tmp_path / "config.json",
        data_dir=tmp_path / "data",
        catalog_1m=tmp_path / "1m.csv",
        catalog_5m=tmp_path / "5m.csv",
        output_dir=tmp_path / "out",
        start="2025-09-20",
        end="2026-09-20",
    )

    try:
        ExternalSuiteConfig(**kwargs, max_pairs=0)
    except ValueError as exc:
        assert "max_pairs" in str(exc)
    else:
        raise AssertionError("max_pairs=0 must fail")

    try:
        ExternalSuiteConfig(**kwargs, pair_offset=-1)
    except ValueError as exc:
        assert "pair_offset" in str(exc)
    else:
        raise AssertionError("pair_offset=-1 must fail")
