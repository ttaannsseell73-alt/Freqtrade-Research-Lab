from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestReport:
    strategy_name: str
    trades: pd.DataFrame
    raw_strategy: dict[str, object]


def _find_report_payload(archive: ZipFile) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    for name in archive.namelist():
        if not name.lower().endswith(".json"):
            continue
        try:
            payload = json.loads(archive.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("strategy"), dict):
            candidates.append(payload)
    if not candidates:
        raise ValueError("Backtest ZIP contains no JSON report with a strategy section")
    if len(candidates) > 1:
        # Prefer the report containing actual strategy trade output.
        candidates.sort(
            key=lambda item: max(
                (
                    len(value.get("trades", []))
                    for value in item["strategy"].values()
                    if isinstance(value, dict)
                ),
                default=0,
            ),
            reverse=True,
        )
    return candidates[0]


def load_backtest_report(
    archive_path: Path,
    *,
    strategy_name: str | None = None,
) -> BacktestReport:
    with ZipFile(archive_path) as archive:
        payload = _find_report_payload(archive)

    strategies = payload["strategy"]
    if not isinstance(strategies, dict) or not strategies:
        raise ValueError("Backtest report contains no strategy results")

    if strategy_name is None:
        if len(strategies) != 1:
            raise ValueError("strategy_name is required when report contains multiple strategies")
        strategy_name = next(iter(strategies))

    if strategy_name not in strategies:
        raise ValueError(f"Strategy not found in backtest report: {strategy_name}")

    raw_strategy = strategies[strategy_name]
    if not isinstance(raw_strategy, dict):
        raise ValueError("Strategy result is not a JSON object")
    trades_raw = raw_strategy.get("trades", [])
    if not isinstance(trades_raw, list):
        raise ValueError("Strategy trades field is not a list")

    trades = pd.DataFrame(trades_raw)
    return BacktestReport(
        strategy_name=strategy_name,
        trades=trades,
        raw_strategy=raw_strategy,
    )


def _trade_duration_minutes(frame: pd.DataFrame) -> pd.Series:
    if "trade_duration" in frame.columns:
        duration = pd.to_numeric(frame["trade_duration"], errors="coerce")
        if duration.notna().any():
            return duration.astype(float)

    if {"open_date", "close_date"}.issubset(frame.columns):
        opened = pd.to_datetime(frame["open_date"], utc=True, errors="coerce")
        closed = pd.to_datetime(frame["close_date"], utc=True, errors="coerce")
        return (closed - opened).dt.total_seconds() / 60.0

    return pd.Series(np.nan, index=frame.index, dtype=float)


def _max_drawdown_from_sequential_returns(returns: pd.Series) -> float:
    values = pd.to_numeric(returns, errors="coerce").dropna().astype(float)
    if values.empty:
        return np.nan
    equity = (1.0 + values).cumprod()
    running_max = equity.cummax()
    return float((equity / running_max - 1.0).min())


def _metrics(group: pd.DataFrame) -> dict[str, object]:
    returns = group["adjusted_profit_ratio"].astype(float)
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    gross_win = float(wins.sum())
    gross_loss = float(-losses.sum())
    profit_factor = gross_win / gross_loss if gross_loss > 0 else np.inf
    funding = (
        pd.to_numeric(group["funding_fees"], errors="coerce").fillna(0.0)
        if "funding_fees" in group.columns
        else pd.Series(0.0, index=group.index)
    )
    duration = _trade_duration_minutes(group)

    return {
        "trades": int(len(group)),
        "wins": int((returns > 0).sum()),
        "draws": int((returns == 0).sum()),
        "losses": int((returns < 0).sum()),
        "win_rate": float((returns > 0).mean()) if len(group) else np.nan,
        "expectancy": float(returns.mean()) if len(group) else np.nan,
        "median_return": float(returns.median()) if len(group) else np.nan,
        "profit_factor": float(profit_factor),
        "sum_adjusted_returns": float(returns.sum()),
        "max_drawdown_compounded": _max_drawdown_from_sequential_returns(returns),
        "avg_duration_minutes": float(duration.mean()) if duration.notna().any() else np.nan,
        "long_trades": int((~group["is_short"].fillna(False).astype(bool)).sum())
        if "is_short" in group.columns
        else np.nan,
        "short_trades": int(group["is_short"].fillna(False).astype(bool).sum())
        if "is_short" in group.columns
        else np.nan,
        "funding_fees_sum": float(funding.sum()),
    }


def summarize_backtest(
    report: BacktestReport,
    *,
    slippage_bps_round_trip: float = 4.0,
) -> tuple[pd.DataFrame, dict[str, object], pd.DataFrame]:
    if slippage_bps_round_trip < 0:
        raise ValueError("slippage_bps_round_trip cannot be negative")
    trades = report.trades.copy()
    if trades.empty:
        pair_metrics = pd.DataFrame(
            columns=[
                "pair",
                "trades",
                "wins",
                "draws",
                "losses",
                "win_rate",
                "expectancy",
                "median_return",
                "profit_factor",
                "sum_adjusted_returns",
                "max_drawdown_compounded",
                "avg_duration_minutes",
                "long_trades",
                "short_trades",
                "funding_fees_sum",
            ]
        )
        overall = {
            "strategy": report.strategy_name,
            "trades": 0,
            "slippage_bps_round_trip": float(slippage_bps_round_trip),
        }
        return pair_metrics, overall, pd.DataFrame(
            columns=["exit_reason", "trades", "expectancy"]
        )

    required = {"pair", "profit_ratio"}
    missing = sorted(required.difference(trades.columns))
    if missing:
        raise ValueError("Backtest trades missing columns: " + ", ".join(missing))

    trades["profit_ratio"] = pd.to_numeric(trades["profit_ratio"], errors="raise")
    trades["adjusted_profit_ratio"] = (
        trades["profit_ratio"] - slippage_bps_round_trip / 10_000.0
    )
    sort_columns = [
        column for column in ("pair", "close_date", "open_date") if column in trades.columns
    ]
    if sort_columns:
        trades = trades.sort_values(sort_columns).reset_index(drop=True)

    rows: list[dict[str, object]] = []
    for pair, group in trades.groupby("pair", sort=True, observed=True):
        rows.append({"pair": str(pair), **_metrics(group)})
    pair_metrics = pd.DataFrame(rows)

    overall_metrics = _metrics(trades)
    overall = {
        "strategy": report.strategy_name,
        "slippage_bps_round_trip": float(slippage_bps_round_trip),
        "drawdown_note": (
            "Overall max drawdown is intentionally omitted because simultaneous "
            "multi-pair trades make a simple sequential equity curve misleading. "
            "Use per-pair drawdown or Freqtrade portfolio drawdown."
        ),
        **{key: value for key, value in overall_metrics.items() if key != "max_drawdown_compounded"},
    }

    if "exit_reason" in trades.columns:
        exit_rows = []
        for reason, group in trades.groupby("exit_reason", sort=True, observed=True):
            exit_rows.append(
                {
                    "exit_reason": str(reason),
                    "trades": int(len(group)),
                    "expectancy": float(group["adjusted_profit_ratio"].mean()),
                }
            )
        exits = pd.DataFrame(exit_rows)
    else:
        exits = pd.DataFrame(columns=["exit_reason", "trades", "expectancy"])

    return pair_metrics, overall, exits


def write_backtest_summary(
    archive_path: Path,
    output_dir: Path,
    *,
    strategy_name: str | None = None,
    slippage_bps_round_trip: float = 4.0,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = load_backtest_report(archive_path, strategy_name=strategy_name)
    pair_metrics, overall, exits = summarize_backtest(
        report,
        slippage_bps_round_trip=slippage_bps_round_trip,
    )
    pair_metrics.to_csv(output_dir / "pair_metrics.csv", index=False)
    exits.to_csv(output_dir / "exit_reason_metrics.csv", index=False)
    (output_dir / "overall_metrics.json").write_text(
        json.dumps(overall, indent=2, allow_nan=True),
        encoding="utf-8",
    )
    return overall
