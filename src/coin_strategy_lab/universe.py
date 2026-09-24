from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
import json
from typing import Any
import urllib.error
import urllib.parse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"
VISION_INDEX_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
VISION_DAILY_KLINES = "https://data.binance.vision/data/futures/um/daily/klines"
VISION_SYMBOL_PREFIX = "data/futures/um/daily/klines/"


@dataclass(frozen=True)
class FuturesSymbol:
    symbol: str
    base_asset: str
    quote_asset: str
    contract_type: str
    status: str
    onboard_date: int | None = None


def parse_usdt_perpetuals(payload: dict[str, Any]) -> tuple[FuturesSymbol, ...]:
    rows: list[FuturesSymbol] = []
    for item in payload.get("symbols", []):
        if item.get("status") != "TRADING":
            continue
        if item.get("contractType") != "PERPETUAL":
            continue
        if item.get("quoteAsset") != "USDT":
            continue
        rows.append(
            FuturesSymbol(
                symbol=str(item["symbol"]),
                base_asset=str(item.get("baseAsset", "")),
                quote_asset=str(item.get("quoteAsset", "")),
                contract_type=str(item.get("contractType", "")),
                status=str(item.get("status", "")),
                onboard_date=int(item["onboardDate"]) if item.get("onboardDate") is not None else None,
            )
        )
    return tuple(sorted(rows, key=lambda x: x.symbol))


def fetch_usdt_perpetuals(timeout: float = 20.0) -> tuple[FuturesSymbol, ...]:
    request = Request(
        EXCHANGE_INFO_URL,
        headers={"User-Agent": "CoinStrategyLab/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return parse_usdt_perpetuals(payload)


def _parse_vision_page(xml_payload: bytes) -> tuple[set[str], str | None, bool]:
    root = ET.fromstring(xml_payload)
    symbols: set[str] = set()
    for node in root.findall(".//{*}CommonPrefixes/{*}Prefix"):
        prefix = (node.text or "").rstrip("/")
        symbol = prefix.rsplit("/", 1)[-1]
        if symbol.endswith("USDT"):
            symbols.add(symbol)

    token_node = root.find(".//{*}NextContinuationToken")
    token = (token_node.text or "").strip() if token_node is not None else None
    truncated_node = root.find(".//{*}IsTruncated")
    truncated = (
        truncated_node is not None
        and (truncated_node.text or "").strip().lower() == "true"
    )
    return symbols, token or None, truncated


def parse_vision_symbol_prefixes(xml_payload: bytes) -> tuple[str, ...]:
    symbols, _, _ = _parse_vision_page(xml_payload)
    return tuple(sorted(symbols))


def list_vision_usdt_symbols(timeout: float = 30.0) -> tuple[str, ...]:
    symbols: set[str] = set()
    continuation: str | None = None

    while True:
        params = {
            "list-type": "2",
            "delimiter": "/",
            "prefix": VISION_SYMBOL_PREFIX,
        }
        if continuation:
            params["continuation-token"] = continuation
        url = f"{VISION_INDEX_URL}?{urllib.parse.urlencode(params)}"
        request = Request(url, headers={"User-Agent": "CoinStrategyLab/1.0"})
        with urlopen(request, timeout=timeout) as response:
            payload = response.read()

        page_symbols, continuation, truncated = _parse_vision_page(payload)
        symbols.update(page_symbols)
        if not truncated or not continuation:
            break

    return tuple(sorted(symbols))


def _vision_archive_exists(
    symbol: str,
    reference_date: date,
    *,
    lookback_days: int,
    timeout: float,
) -> bool:
    quoted_symbol = urllib.parse.quote(symbol, safe="")
    for offset in range(max(1, lookback_days)):
        day = reference_date - timedelta(days=offset)
        day_text = day.isoformat()
        filename = urllib.parse.quote(f"{symbol}-1h-{day_text}.zip", safe="-_.")
        url = f"{VISION_DAILY_KLINES}/{quoted_symbol}/1h/{filename}"
        request = Request(url, headers={"User-Agent": "CoinStrategyLab/1.0"})
        try:
            with urlopen(request, timeout=timeout) as response:
                response.read(1)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
    return False


def discover_usdt_perpetuals_from_vision(
    reference_date: date,
    *,
    lookback_days: int = 4,
    timeout: float = 20.0,
    workers: int = 32,
) -> tuple[FuturesSymbol, ...]:
    """Fallback active-universe discovery without Binance Futures REST.

    Binance Vision contains historical and delisted symbols, so the S3 prefix
    list is filtered by the presence of a recent 1h daily archive. This is a
    market-data availability proxy for an actively trading USD-M perpetual.
    """
    candidates = list_vision_usdt_symbols(timeout=max(timeout, 30.0))
    active: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        pending = {
            pool.submit(
                _vision_archive_exists,
                symbol,
                reference_date,
                lookback_days=lookback_days,
                timeout=timeout,
            ): symbol
            for symbol in candidates
        }
        for future in as_completed(pending):
            symbol = pending[future]
            if future.result():
                active.append(symbol)

    return tuple(
        FuturesSymbol(
            symbol=symbol,
            base_asset=symbol[:-4],
            quote_asset="USDT",
            contract_type="PERPETUAL",
            status="TRADING",
            onboard_date=None,
        )
        for symbol in sorted(active)
    )
