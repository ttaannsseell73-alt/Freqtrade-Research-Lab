from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.request import Request, urlopen


EXCHANGE_INFO_URL = "https://fapi.binance.com/fapi/v1/exchangeInfo"


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
