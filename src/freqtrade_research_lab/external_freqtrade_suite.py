from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

import pandas as pd

from .execution_metrics import load_backtest_report, summarize_directions
from .execution_runner import ExecutionRunConfig, run_execution_benchmark, select_pairs
from .run_io import prepare_fresh_output_dir


@dataclass(frozen=True)
class ExternalStrategySpec:
    name: str
    repository: str
    commit: str
    path: str
    blob_sha1: str
    timeframe: str
    note: str = ""

    @property
    def raw_url(self) -> str:
        return (
            f"https://raw.githubusercontent.com/{self.repository}/"
            f"{self.commit}/{self.path}"
        )


DEFAULT_EXTERNAL_STRATEGIES: tuple[ExternalStrategySpec, ...] = (
    ExternalStrategySpec(
        name="Scalp",
        repository="freqtrade/freqtrade-strategies",
        commit="f3340ce11f5bdf62f598522e64d1f5638eaa13f5",
        path="user_data/strategies/berlinguyinca/Scalp.py",
        blob_sha1="c87b99acaeb70afa02e82f0b2e706be3ef8dd727",
        timeframe="1m",
        note="Official Freqtrade strategy repository; native 1m scalp.",
    ),
    ExternalStrategySpec(
        name="SmoothScalp",
        repository="freqtrade/freqtrade-strategies",
        commit="f3340ce11f5bdf62f598522e64d1f5638eaa13f5",
        path="user_data/strategies/berlinguyinca/SmoothScalp.py",
        blob_sha1="9d0718b04aad27e8bc989b2362a22934485e9520",
        timeframe="1m",
        note="Official Freqtrade strategy repository; native 1m scalp.",
    ),
    ExternalStrategySpec(
        name="ReinforcedSmoothScalp",
        repository="freqtrade/freqtrade-strategies",
        commit="f3340ce11f5bdf62f598522e64d1f5638eaa13f5",
        path="user_data/strategies/berlinguyinca/ReinforcedSmoothScalp.py",
        blob_sha1="8efc3f8c38329784ea9d661de58aac7fd644d872",
        timeframe="1m",
        note="Official Freqtrade strategy repository; native 1m reinforced scalp.",
    ),
    ExternalStrategySpec(
        name="GeneticEngineV1",
        repository="ceyhanmolla/freqtrade-strategies",
        commit="937fefc25ef209120fa2a59d2f520f52d991f26b",
        path="GeneticEngineV1.py",
        blob_sha1="99a11d0b8d53711c75984356c7e59cfd52c8de3b",
        timeframe="5m",
        note="Third-party ready strategy; native 5m.",
    ),
    ExternalStrategySpec(
        name="EwoMomentumV1",
        repository="ceyhanmolla/freqtrade-strategies",
        commit="937fefc25ef209120fa2a59d2f520f52d991f26b",
        path="EwoMomentumV1.py",
        blob_sha1="b1e48738432a1b877c5dbe87b5e36abf4607cfc6",
        timeframe="5m",
        note="Third-party ready strategy; native 5m.",
    ),
)


@dataclass(frozen=True)
class ExternalSuiteConfig:
    config_path: Path
    data_dir: Path
    catalog_1m: Path
    catalog_5m: Path
    output_dir: Path
    start: str
    end: str
    reuse_root: Path | None = None
    fee_per_side: float = 0.0005
    slippage_bps_round_trip: float = 4.0
    stake_amount: float = 1000.0
    max_pairs: int = 5
    pair_offset: int = 0
    strategies: tuple[ExternalStrategySpec, ...] = DEFAULT_EXTERNAL_STRATEGIES

    def __post_init__(self) -> None:
        if self.max_pairs <= 0:
            raise ValueError("max_pairs must be positive")
        if self.pair_offset < 0:
            raise ValueError("pair_offset cannot be negative")
        if not self.strategies:
            raise ValueError("strategies cannot be empty")
        names = [spec.name for spec in self.strategies]
        if len(names) != len(set(names)):
            raise ValueError("external strategy names must be unique")
        unsupported = [spec.timeframe for spec in self.strategies if spec.timeframe not in {"1m", "5m"}]
        if unsupported:
            raise ValueError(f"unsupported native timeframes: {sorted(set(unsupported))}")


def git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content).hexdigest()


def _download(spec: ExternalStrategySpec, target: Path) -> dict[str, str]:
    request = Request(
        spec.raw_url,
        headers={"User-Agent": "Freqtrade-Research-Lab/1.0"},
    )
    with urlopen(request, timeout=45) as response:
        content = response.read()

    actual_blob = git_blob_sha1(content)
    if actual_blob != spec.blob_sha1:
        raise RuntimeError(
            f"Source integrity mismatch for {spec.name}: "
            f"expected {spec.blob_sha1}, got {actual_blob}"
        )

    target.write_bytes(content)
    return {
        "name": spec.name,
        "repository": spec.repository,
        "commit": spec.commit,
        "path": spec.path,
        "blob_sha1": actual_blob,
        "sha256": hashlib.sha256(content).hexdigest(),
        "raw_url": spec.raw_url,
    }


def _catalog(config: ExternalSuiteConfig, timeframe: str) -> Path:
    return config.catalog_1m if timeframe == "1m" else config.catalog_5m


def _execution_config(
    config: ExternalSuiteConfig,
    spec: ExternalStrategySpec,
    strategy_path: Path,
    output_dir: Path,
) -> ExecutionRunConfig:
    return ExecutionRunConfig(
        config_path=config.config_path,
        data_dir=config.data_dir,
        catalog_path=_catalog(config, spec.timeframe),
        strategy_path=strategy_path,
        output_dir=output_dir,
        strategy_name=spec.name,
        timeframe=spec.timeframe,
        timeframe_detail="1m" if spec.timeframe == "5m" else None,
        start=config.start,
        end=config.end,
        fee_per_side=config.fee_per_side,
        slippage_bps_round_trip=config.slippage_bps_round_trip,
        stake_amount=config.stake_amount,
        run_lookahead=False,
        max_pairs=config.max_pairs,
        pair_offset=config.pair_offset,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_matches(
    manifest: dict[str, object],
    run_config: ExecutionRunConfig,
    expected_pairs: list[str],
) -> bool:
    strategy_file = run_config.strategy_path / f"{run_config.strategy_name}.py"
    if not strategy_file.is_file():
        return False
    expected_start = pd.Timestamp(run_config.start, tz="UTC").isoformat()
    expected_end = pd.Timestamp(run_config.end, tz="UTC").isoformat()
    checks = (
        manifest.get("strategy") == run_config.strategy_name,
        manifest.get("strategy_sha256") == _sha256(strategy_file),
        manifest.get("timeframe") == run_config.timeframe,
        manifest.get("timeframe_detail") == run_config.timeframe_detail,
        manifest.get("start_inclusive") == expected_start,
        manifest.get("end_exclusive") == expected_end,
        int(manifest.get("pair_count", -1)) == len(expected_pairs),
        int(manifest.get("pair_offset", 0)) == run_config.pair_offset,
        float(manifest.get("fee_per_side", -1.0)) == run_config.fee_per_side,
        float(manifest.get("post_backtest_slippage_bps_round_trip", -1.0))
        == run_config.slippage_bps_round_trip,
    )
    return all(checks)


def _find_reusable(
    root: Path | None,
    run_config: ExecutionRunConfig,
    expected_pairs: list[str],
) -> tuple[Path, dict[str, object]] | None:
    if root is None or not root.is_dir():
        return None
    for manifest_path in sorted(root.rglob("run_manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(manifest, dict):
            continue
        if not _manifest_matches(manifest, run_config, expected_pairs):
            continue
        pairs_path = manifest_path.parent / "pairs.txt"
        if not pairs_path.is_file():
            continue
        pairs = [
            line.strip()
            for line in pairs_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if pairs != expected_pairs:
            continue
        if not any((manifest_path.parent / "backtest").glob("*.zip")):
            continue
        return manifest_path.parent, manifest
    return None


def _direction_metrics(
    run_dir: Path,
    strategy_name: str,
    slippage_bps_round_trip: float,
) -> dict[str, dict[str, object]]:
    direction_path = run_dir / "metrics" / "direction_metrics.csv"
    if direction_path.is_file():
        frame = pd.read_csv(direction_path)
    else:
        archives = sorted(
            (run_dir / "backtest").glob("*.zip"),
            key=lambda path: path.stat().st_mtime_ns,
        )
        if not archives:
            return {}
        report = load_backtest_report(archives[-1], strategy_name=strategy_name)
        frame = summarize_directions(
            report,
            slippage_bps_round_trip=slippage_bps_round_trip,
        )
    if frame.empty:
        return {}
    aggregate = frame.loc[frame["pair"] == "__ALL__"]
    return {
        str(row["direction"]): row.to_dict()
        for _, row in aggregate.iterrows()
    }


def _row(
    spec: ExternalStrategySpec,
    source: str,
    run_dir: Path,
    manifest: dict[str, object],
) -> dict[str, object]:
    overall_raw = manifest.get("overall_metrics", {})
    overall = overall_raw if isinstance(overall_raw, dict) else {}
    directions = _direction_metrics(
        run_dir,
        strategy_name=spec.name,
        slippage_bps_round_trip=float(
            manifest.get("post_backtest_slippage_bps_round_trip", 4.0)
        ),
    )
    long = directions.get("long", {})
    short = directions.get("short", {})
    expectancy = float(overall.get("expectancy", float("nan")))
    profit_factor = float(overall.get("profit_factor", float("nan")))
    status = (
        "POSITIVE_SMOKE"
        if int(overall.get("trades", 0)) > 0
        and expectancy > 0
        and profit_factor > 1
        else "NEGATIVE_SMOKE"
    )
    return {
        "strategy": spec.name,
        "timeframe": spec.timeframe,
        "repository": spec.repository,
        "commit": spec.commit[:10],
        "source": source,
        "trades": int(overall.get("trades", 0)),
        "win_rate": overall.get("win_rate"),
        "expectancy": overall.get("expectancy"),
        "profit_factor": overall.get("profit_factor"),
        "avg_duration_minutes": overall.get("avg_duration_minutes"),
        "long_trades": int(long.get("trades", 0)) if long else 0,
        "long_expectancy": long.get("expectancy"),
        "long_profit_factor": long.get("profit_factor"),
        "short_trades": int(short.get("trades", 0)) if short else 0,
        "short_expectancy": short.get("expectancy"),
        "short_profit_factor": short.get("profit_factor"),
        "status": status,
        "run_dir": str(run_dir),
    }


def _fmt_num(value: object, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(number):
        return "-"
    return f"{number:.{digits}f}"


def _fmt_pct(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if pd.isna(number):
        return "-"
    return f"{number * 100:.4f}%"


def _write_report(
    frame: pd.DataFrame,
    path: Path,
    config: ExternalSuiteConfig,
    errors: list[dict[str, str]],
) -> None:
    lines = [
        "# External Ready-Scalper Benchmark",
        "",
        "Unmodified third-party strategy sources pinned to immutable GitHub commits.",
        f"Window: {config.start} -> {config.end}",
        f"Pairs per run: {config.max_pairs} (offset {config.pair_offset})",
        (
            f"Costs: {config.fee_per_side * 2 * 10_000:.1f} bps round-trip fee "
            f"+ {config.slippage_bps_round_trip:.1f} bps post-backtest slippage"
        ),
        "5m strategies use 1m detail candles.",
        "",
        "| Strategy | TF | Repo | Trades | PF | Exp/trade | Win | Long PF / Exp | Status |",
        "|---|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    for _, row in frame.iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["strategy"]),
                    str(row["timeframe"]),
                    str(row["repository"]),
                    str(int(row["trades"])),
                    _fmt_num(row["profit_factor"]),
                    _fmt_pct(row["expectancy"]),
                    _fmt_pct(row["win_rate"]),
                    f"{_fmt_num(row['long_profit_factor'])} / {_fmt_pct(row['long_expectancy'])}",
                    str(row["status"]),
                ]
            )
            + " |"
        )
    if errors:
        lines.extend(["", "## Errors", ""])
        for error in errors:
            lines.append(f"- {error['strategy']}: {error['error']}")
    lines.extend(
        [
            "",
            "## Selection rule",
            "",
            "This is a diagnostic smoke benchmark, not a live-trading recommendation.",
            "A candidate is retained only if fee + slippage adjusted expectancy is positive and PF > 1.",
            "Retained candidates must then pass broader-pair, lookahead, walk-forward, and paper validation.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_external_suite(config: ExternalSuiteConfig) -> dict[str, object]:
    prepare_fresh_output_dir(config.output_dir)
    sources = config.output_dir / "sources"
    runs = config.output_dir / "runs"
    sources.mkdir()
    runs.mkdir()

    source_manifest: list[dict[str, str]] = []
    rows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []

    for spec in config.strategies:
        target = sources / f"{spec.name}.py"
        try:
            provenance = _download(spec, target)
            source_manifest.append(provenance)

            run_config = _execution_config(
                config,
                spec=spec,
                strategy_path=sources,
                output_dir=runs / spec.name,
            )
            expected_pairs = select_pairs(run_config)
            reusable = _find_reusable(
                config.reuse_root,
                run_config=run_config,
                expected_pairs=expected_pairs,
            )
            if reusable is None:
                manifest = run_execution_benchmark(run_config)
                run_dir = runs / spec.name
                source = "executed"
            else:
                run_dir, manifest = reusable
                source = "reused"

            rows.append(_row(spec, source, run_dir, manifest))
        except Exception as exc:
            errors.append(
                {
                    "strategy": spec.name,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    frame = pd.DataFrame(rows)
    frame.to_csv(config.output_dir / "external_suite_summary.csv", index=False)
    _write_report(
        frame,
        config.output_dir / "EXTERNAL_SCALPER_REPORT.md",
        config,
        errors,
    )

    manifest = {
        "schema_version": 1,
        "study": "external_ready_scalpers",
        "start": config.start,
        "end": config.end,
        "max_pairs": config.max_pairs,
        "pair_offset": config.pair_offset,
        "fee_per_side": config.fee_per_side,
        "slippage_bps_round_trip": config.slippage_bps_round_trip,
        "timeframe_detail_policy": {"1m": None, "5m": "1m"},
        "sources": source_manifest,
        "completed_runs": len(rows),
        "errors": errors,
    }
    (config.output_dir / "external_suite_manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    return manifest
