from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pandas as pd


PUBLIC_FUTURES_BASE_URL = "https://fapi.binance.com"
TESTNET_FUTURES_BASE_URL = "https://testnet.binancefuture.com"
ALLOWED_PUBLIC_BASE_URLS = {
    PUBLIC_FUTURES_BASE_URL,
    TESTNET_FUTURES_BASE_URL,
}


@dataclass(frozen=True)
class TradabilitySnapshot:
    symbol: str
    quote_volume_24h: float
    spread_bps: float
    bid_depth_10bps: float
    ask_depth_10bps: float
    open_interest_notional: float
    mark_price: float

    @property
    def min_side_depth_10bps(self) -> float:
        return min(self.bid_depth_10bps, self.ask_depth_10bps)


class BinanceFuturesPublicMarket:
    """Read-only Binance USD-M Futures public market-data client."""

    def __init__(
        self,
        *,
        base_url: str = PUBLIC_FUTURES_BASE_URL,
        timeout_seconds: float = 15.0,
        client: httpx.Client | None = None,
    ):
        normalized = base_url.rstrip("/")
        if normalized not in ALLOWED_PUBLIC_BASE_URLS:
            raise ValueError("Unsupported Binance Futures public market endpoint")
        self.base_url = normalized
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self.client.get(f"{self.base_url}{path}", params=params)
        response.raise_for_status()
        return response.json()

    def server_time_ms(self) -> int:
        return int(self._get("/fapi/v1/time")["serverTime"])

    def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = 300,
        server_time_ms: int | None = None,
    ) -> pd.DataFrame:
        raw = self._get(
            "/fapi/v1/klines",
            {"symbol": symbol.upper(), "interval": interval, "limit": int(limit)},
        )
        if server_time_ms is None:
            server_time_ms = self.server_time_ms()
        rows = []
        for item in raw:
            rows.append(
                {
                    "open_time_ms": int(item[0]),
                    "date": pd.to_datetime(int(item[0]), unit="ms", utc=True),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                    "close_time_ms": int(item[6]),
                    "quote_volume": float(item[7]),
                    "is_closed": int(item[6]) < int(server_time_ms),
                }
            )
        return pd.DataFrame(rows)

    def mark_price(self, symbol: str) -> float:
        return float(
            self._get(
                "/fapi/v1/premiumIndex",
                {"symbol": symbol.upper()},
            )["markPrice"]
        )

    def tradability_snapshot(self, symbol: str) -> TradabilitySnapshot:
        symbol = symbol.upper()
        ticker = self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})
        book = self._get("/fapi/v1/depth", {"symbol": symbol, "limit": 100})
        oi = self._get("/fapi/v1/openInterest", {"symbol": symbol})
        mark = self.mark_price(symbol)

        bids = [(float(px), float(qty)) for px, qty in book.get("bids", [])]
        asks = [(float(px), float(qty)) for px, qty in book.get("asks", [])]
        if not bids or not asks:
            raise RuntimeError(f"No order book for {symbol}")
        bid = bids[0][0]
        ask = asks[0][0]
        mid = (bid + ask) / 2.0
        if mid <= 0.0:
            raise RuntimeError(f"Invalid mid price for {symbol}")

        spread_bps = (ask - bid) / mid * 10_000.0
        bid_floor = mid * (1.0 - 0.001)
        ask_ceiling = mid * (1.0 + 0.001)
        bid_depth = sum(px * qty for px, qty in bids if px >= bid_floor)
        ask_depth = sum(px * qty for px, qty in asks if px <= ask_ceiling)
        oi_notional = float(oi.get("openInterest", 0.0)) * mark

        return TradabilitySnapshot(
            symbol=symbol,
            quote_volume_24h=float(ticker.get("quoteVolume", 0.0)),
            spread_bps=spread_bps,
            bid_depth_10bps=bid_depth,
            ask_depth_10bps=ask_depth,
            open_interest_notional=oi_notional,
            mark_price=mark,
        )


def classify_tradability(snapshot: TradabilitySnapshot, policy: dict) -> str:
    hard = policy["hard_block"]
    if (
        snapshot.spread_bps > float(hard["max_spread_bps"])
        or snapshot.quote_volume_24h < float(hard["min_quote_volume_24h"])
        or snapshot.min_side_depth_10bps < float(hard["min_side_depth_10bps"])
        or snapshot.open_interest_notional < float(hard["min_open_interest_notional"])
    ):
        return "BLOCK"

    strong = policy["strong"]
    if (
        snapshot.quote_volume_24h >= float(strong["min_quote_volume_24h"])
        and snapshot.spread_bps <= float(strong["max_spread_bps"])
        and snapshot.min_side_depth_10bps >= float(strong["min_side_depth_10bps"])
        and snapshot.open_interest_notional >= float(strong["min_open_interest_notional"])
    ):
        return "STRONG"

    tradeable = policy["tradeable"]
    if (
        snapshot.quote_volume_24h >= float(tradeable["min_quote_volume_24h"])
        and snapshot.spread_bps <= float(tradeable["max_spread_bps"])
        and snapshot.min_side_depth_10bps >= float(tradeable["min_side_depth_10bps"])
        and snapshot.open_interest_notional >= float(tradeable["min_open_interest_notional"])
    ):
        return "TRADEABLE"

    return "REVIEW"
