from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import re

import pandas as pd


_FUTURES_FILE = re.compile(
    r"^(?P<symbol>.+)-(?P<timeframe>[^-]+)-futures\.(?P<extension>feather|parquet|json|json\.gz)$"
)
_TIMEFRAME = re.compile(r"^(?P<count>[1-9][0-9]*)(?P<unit>[mhdw])$")


@dataclass(frozen=True)
class MarketFile:
    path: Path
    pair: str
    timeframe: str
    extension: str


@dataclass(frozen=True)
class CatalogSelection:
    pairs: frozenset[str]
    fingerprint_basis: str
    entries: tuple[tuple[str, str], ...]

    def fingerprint_for_pairs(self, pairs: set[str] | frozenset[str] | None = None) -> str:
        selected = self.pairs if pairs is None else frozenset(pairs)
        unknown = selected.difference(self.pairs)
        if unknown:
            raise ValueError(f"Pairs are not present in catalog selection: {sorted(unknown)[:5]}")
        payload = "\n".join(
            f"{pair}|{value}"
            for pair, value in self.entries
            if pair in selected
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decode_pair(encoded: str) -> str:
    parts = encoded.split("_")
    if len(parts) < 3:
        raise ValueError(f"Unsupported Freqtrade futures filename symbol: {encoded}")
    base = "_".join(parts[:-2])
    quote = parts[-2]
    settlement = parts[-1]
    return f"{base}/{quote}:{settlement}"


def parse_market_file(path: Path) -> MarketFile | None:
    match = _FUTURES_FILE.match(path.name)
    if not match:
        return None
    return MarketFile(
        path=path,
        pair=_decode_pair(match.group("symbol")),
        timeframe=match.group("timeframe"),
        extension=match.group("extension"),
    )


def discover_market_files(data_dir: Path, timeframe: str = "1m") -> list[MarketFile]:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory does not exist: {data_dir}")

    discovered: list[MarketFile] = []
    for path in data_dir.iterdir():
        if not path.is_file():
            continue
        item = parse_market_file(path)
        if item is not None and item.timeframe == timeframe:
            discovered.append(item)
    return sorted(discovered, key=lambda item: item.pair)


def timeframe_delta(timeframe: str) -> timedelta:
    match = _TIMEFRAME.match(timeframe)
    if match is None:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    count = int(match.group("count"))
    unit_seconds = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    return timedelta(seconds=count * unit_seconds[match.group("unit")])


def load_ohlcv(item: MarketFile) -> pd.DataFrame:
    if item.extension == "feather":
        frame = pd.read_feather(item.path)
    elif item.extension == "parquet":
        frame = pd.read_parquet(item.path)
    else:
        frame = pd.read_json(item.path, compression="infer")
        if list(frame.columns) == list(range(6)):
            frame.columns = ["date", "open", "high", "low", "close", "volume"]

    frame.columns = [str(column).lower() for column in frame.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{item.path.name} is missing columns: {', '.join(missing)}")

    frame = frame.loc[:, ["date", "open", "high", "low", "close", "volume"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna().sort_values("date").drop_duplicates("date", keep="last")
    return frame.reset_index(drop=True)


def assess_market_file(
    item: MarketFile,
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    minimum_coverage: float = 0.80,
    minimum_candles: int = 1_000,
    include_source_sha256: bool = True,
) -> dict[str, object]:
    if not 0 < minimum_coverage <= 1:
        raise ValueError("minimum_coverage must be in (0, 1]")
    if minimum_candles <= 0:
        raise ValueError("minimum_candles must be positive")
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    start = start.tz_localize("UTC") if start.tzinfo is None else start.tz_convert("UTC")
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    if end <= start:
        raise ValueError("end must be later than start")

    interval = pd.Timedelta(timeframe_delta(item.timeframe))
    expected_candles = int((end - start) / interval)
    frame = load_ohlcv(item)
    frame = frame.loc[(frame["date"] >= start) & (frame["date"] < end)].copy()
    candles = len(frame)
    coverage_ratio = min(candles / expected_candles, 1.0) if expected_candles else 0.0

    if candles:
        deltas = frame["date"].diff().dropna()
        missing_candles = int(((deltas[deltas > interval] / interval) - 1).sum())
        gap_count = int((deltas > interval).sum())
        invalid_ohlc = int(
            (
                (frame["high"] < frame[["open", "close", "low"]].max(axis=1))
                | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))
                | (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
                | (frame["volume"] < 0)
            ).sum()
        )
        first_candle = frame["date"].iloc[0].isoformat()
        last_candle = frame["date"].iloc[-1].isoformat()
    else:
        missing_candles = expected_candles
        gap_count = 0
        invalid_ohlc = 0
        first_candle = None
        last_candle = None

    ready = (
        candles >= minimum_candles
        and coverage_ratio >= minimum_coverage
        and invalid_ohlc == 0
    )
    if ready:
        status = "ready"
    elif candles == 0:
        status = "empty"
    elif invalid_ohlc:
        status = "invalid_ohlc"
    elif candles < minimum_candles:
        status = "too_short"
    else:
        status = "partial_coverage"

    return {
        "pair": item.pair,
        "timeframe": item.timeframe,
        "file": item.path.name,
        "extension": item.extension,
        "size_bytes": item.path.stat().st_size,
        "source_sha256": sha256_file(item.path) if include_source_sha256 else None,
        "candles": candles,
        "expected_candles": expected_candles,
        "coverage_ratio": coverage_ratio,
        "first_candle": first_candle,
        "last_candle": last_candle,
        "gap_count": gap_count,
        "missing_candles_inside": missing_candles,
        "invalid_ohlc_rows": invalid_ohlc,
        "research_ready": ready,
        "status": status,
    }


def build_dataset_catalog(
    data_dir: Path,
    timeframes: tuple[str, ...],
    start: pd.Timestamp,
    end: pd.Timestamp,
    *,
    minimum_coverage: float = 0.80,
    minimum_candles: int = 1_000,
    include_source_sha256: bool = True,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for timeframe in timeframes:
        for item in discover_market_files(data_dir, timeframe):
            rows.append(
                assess_market_file(
                    item,
                    start,
                    end,
                    minimum_coverage=minimum_coverage,
                    minimum_candles=minimum_candles,
                    include_source_sha256=include_source_sha256,
                )
            )
    return pd.DataFrame(rows)


def _ready_rows(catalog_path: Path, timeframe: str) -> pd.DataFrame:
    catalog = pd.read_csv(catalog_path)
    required = {"pair", "timeframe", "research_ready"}
    missing = required.difference(catalog.columns)
    if missing:
        raise ValueError(
            f"{catalog_path} is missing catalog columns: {', '.join(sorted(missing))}"
        )

    ready_values = catalog["research_ready"]
    if ready_values.dtype != bool:
        ready_values = (
            ready_values.astype(str).str.strip().str.lower().map(
                {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}
            )
        )
    if ready_values.isna().any():
        raise ValueError(f"{catalog_path} contains invalid research_ready values")

    return catalog.loc[
        (catalog["timeframe"].astype(str) == timeframe) & ready_values
    ].copy()


def _row_metadata_fingerprint(row: pd.Series) -> str:
    excluded = {"source_sha256"}
    values = {
        str(column): None if pd.isna(value) else str(value)
        for column, value in row.items()
        if column not in excluded
    }
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_catalog_selection(
    catalog_path: Path,
    timeframe: str,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> CatalogSelection:
    summary_path = catalog_path.with_name("dataset_catalog_summary.json")
    if not summary_path.is_file():
        raise ValueError(
            f"Catalog summary is required for provenance validation: {summary_path}"
        )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    requested_start = pd.Timestamp(start)
    requested_end = pd.Timestamp(end)
    requested_start = (
        requested_start.tz_localize("UTC")
        if requested_start.tzinfo is None
        else requested_start.tz_convert("UTC")
    )
    requested_end = (
        requested_end.tz_localize("UTC")
        if requested_end.tzinfo is None
        else requested_end.tz_convert("UTC")
    )
    catalog_start = pd.Timestamp(summary.get("start_inclusive"))
    catalog_end = pd.Timestamp(summary.get("end_exclusive"))
    catalog_start = (
        catalog_start.tz_localize("UTC")
        if catalog_start.tzinfo is None
        else catalog_start.tz_convert("UTC")
    )
    catalog_end = (
        catalog_end.tz_localize("UTC")
        if catalog_end.tzinfo is None
        else catalog_end.tz_convert("UTC")
    )
    if catalog_start != requested_start or catalog_end != requested_end:
        raise ValueError(
            "Catalog date range does not match requested study range: "
            f"catalog=[{catalog_start.isoformat()}, {catalog_end.isoformat()}) "
            f"requested=[{requested_start.isoformat()}, {requested_end.isoformat()})"
        )
    if timeframe not in {str(value) for value in summary.get("timeframes", [])}:
        raise ValueError(f"Catalog summary does not include timeframe {timeframe}")

    selected = _ready_rows(catalog_path, timeframe).sort_values("pair")
    if selected.empty:
        return CatalogSelection(frozenset(), "empty_catalog_selection_v1", tuple())

    has_source_hash = (
        "source_sha256" in selected.columns
        and selected["source_sha256"].notna().all()
        and selected["source_sha256"].astype(str).str.len().eq(64).all()
    )
    if has_source_hash:
        basis = "source_sha256_v1"
        entries = tuple(
            (str(row["pair"]), str(row["source_sha256"]))
            for _, row in selected.iterrows()
        )
    else:
        basis = "catalog_metadata_fallback_v1"
        entries = tuple(
            (str(row["pair"]), _row_metadata_fingerprint(row))
            for _, row in selected.iterrows()
        )
    return CatalogSelection(
        pairs=frozenset(pair for pair, _ in entries),
        fingerprint_basis=basis,
        entries=entries,
    )


def fingerprint_market_files(market_files: list[MarketFile]) -> str:
    payload = "\n".join(
        f"{item.pair}|{item.path.name}|{item.path.stat().st_size}"
        for item in sorted(market_files, key=lambda value: value.pair)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_ready_pairs_from_catalog(catalog_path: Path, timeframe: str) -> set[str]:
    return set(_ready_rows(catalog_path, timeframe)["pair"].astype(str))
