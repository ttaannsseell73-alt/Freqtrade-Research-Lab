from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import pandas as pd


_FUTURES_FILE = re.compile(
    r"^(?P<symbol>.+)-(?P<timeframe>[^-]+)-futures\.(?P<extension>feather|parquet|json|json\.gz)$"
)


@dataclass(frozen=True)
class MarketFile:
    path: Path
    pair: str
    timeframe: str
    extension: str


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

