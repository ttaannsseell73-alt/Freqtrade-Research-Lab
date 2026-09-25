from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
import hashlib
import hmac
import os
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from coin_strategy_lab.execution import (
    ExchangeOrder,
    ExchangePosition,
    ExchangeSnapshot,
)


TESTNET_BASE_URL = "https://testnet.binancefuture.com"


class BinanceApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class BinanceFuturesTestnet:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        base_url: str = TESTNET_BASE_URL,
        allow_orders: bool = False,
        timeout_seconds: float = 10.0,
        recv_window_ms: int = 5000,
        client: httpx.Client | None = None,
    ):
        normalized = base_url.rstrip("/")
        if normalized != TESTNET_BASE_URL:
            raise ValueError(
                "Only Binance USD-M Futures TESTNET is permitted; "
                "live endpoints are blocked."
            )
        if not api_key or not api_secret:
            raise ValueError("Binance testnet API credentials are required")
        self.api_key = api_key
        self.api_secret = api_secret.encode("utf-8")
        self.base_url = normalized
        self.allow_orders = bool(allow_orders)
        self.recv_window_ms = int(recv_window_ms)
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            headers={"X-MBX-APIKEY": api_key},
        )
        self._exchange_info: dict[str, Any] | None = None

    @classmethod
    def from_env(cls, *, allow_orders: bool | None = None):
        key = os.getenv("BINANCE_TESTNET_API_KEY", "")
        secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
        env_arm = os.getenv("CSL_ALLOW_TESTNET_ORDERS", "").upper() == "YES"
        if allow_orders is None:
            allow_orders = env_arm
        elif allow_orders and not env_arm:
            raise RuntimeError(
                "Set CSL_ALLOW_TESTNET_ORDERS=YES to arm testnet orders"
            )
        return cls(key, secret, allow_orders=allow_orders)

    def _signed_query(self, params: dict[str, Any] | None = None) -> str:
        payload = dict(params or {})
        payload.setdefault("recvWindow", self.recv_window_ms)
        payload["timestamp"] = int(time.time() * 1000)
        encoded = urlencode(payload)
        signature = hmac.new(
            self.api_secret,
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{encoded}&signature={signature}"

    @staticmethod
    def _error_from_response(response: httpx.Response) -> BinanceApiError:
        code = None
        message = response.text
        try:
            payload = response.json()
            code = payload.get("code")
            message = payload.get("msg") or message
        except Exception:
            pass
        return BinanceApiError(
            message,
            status_code=response.status_code,
            code=code,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> Any:
        method = method.upper()
        url = f"{self.base_url}{path}"
        if signed:
            query = self._signed_query(params)
            url = f"{url}?{query}"
            response = self.client.request(method, url)
        else:
            response = self.client.request(method, url, params=params)
        if response.status_code >= 400:
            raise self._error_from_response(response)
        return response.json()

    def _require_order_arm(self) -> None:
        if not self.allow_orders:
            raise RuntimeError(
                "Testnet order submission is disarmed. "
                "Set CSL_ALLOW_TESTNET_ORDERS=YES explicitly."
            )

    def position_mode_is_one_way(self) -> bool:
        payload = self._request(
            "GET",
            "/fapi/v1/positionSide/dual",
            signed=True,
        )
        return not bool(payload.get("dualSidePosition", False))

    def _require_one_way(self) -> None:
        if not self.position_mode_is_one_way():
            raise RuntimeError(
                "Hedge Mode detected. CoinStrategyLab execution requires "
                "Binance Futures One-way Mode and will not change it automatically."
            )

    def exchange_info(self) -> dict[str, Any]:
        if self._exchange_info is None:
            self._exchange_info = self._request(
                "GET",
                "/fapi/v1/exchangeInfo",
            )
        return self._exchange_info

    def tradable_symbols(self) -> set[str]:
        return {
            str(x["symbol"])
            for x in self.exchange_info().get("symbols", [])
            if x.get("status") == "TRADING"
        }

    def _symbol_info(self, symbol: str) -> dict[str, Any]:
        for item in self.exchange_info().get("symbols", []):
            if item.get("symbol") == symbol:
                return item
        raise BinanceApiError(f"Unknown testnet symbol: {symbol}", code=-1121)

    @staticmethod
    def _filter(symbol_info: dict[str, Any], name: str) -> dict[str, Any] | None:
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == name:
                return item
        return None

    @staticmethod
    def _floor_step(value: float, step: float) -> float:
        if step <= 0.0:
            raise ValueError("step must be positive")
        d_value = Decimal(str(value))
        d_step = Decimal(str(step))
        units = (d_value / d_step).to_integral_value(rounding=ROUND_DOWN)
        return float(units * d_step)

    def _market_quantity(
        self,
        symbol: str,
        target_notional: float,
        price: float,
    ) -> float:
        if target_notional <= 0.0 or price <= 0.0:
            raise ValueError("target_notional and price must be positive")
        info = self._symbol_info(symbol)
        lot = (
            self._filter(info, "MARKET_LOT_SIZE")
            or self._filter(info, "LOT_SIZE")
        )
        if lot is None:
            raise RuntimeError(f"No quantity filter for {symbol}")
        step = float(lot["stepSize"])
        min_qty = float(lot["minQty"])
        max_qty = float(lot["maxQty"])
        qty = self._floor_step(target_notional / price, step)
        if qty < min_qty:
            raise RuntimeError(
                f"{symbol} target quantity {qty} is below minQty {min_qty}"
            )
        qty = min(qty, max_qty)

        notional_filter = (
            self._filter(info, "MIN_NOTIONAL")
            or self._filter(info, "NOTIONAL")
        )
        if notional_filter:
            min_notional = float(
                notional_filter.get(
                    "notional",
                    notional_filter.get("minNotional", 0.0),
                )
            )
            if qty * price + 1e-12 < min_notional:
                raise RuntimeError(
                    f"{symbol} target notional is below minimum {min_notional}"
                )
        return qty

    def account_equity_usdt(self) -> float:
        balances = self._request(
            "GET",
            "/fapi/v3/balance",
            signed=True,
        )
        for item in balances:
            if item.get("asset") == "USDT":
                balance = float(item.get("balance", 0.0))
                cross_un_pnl = float(item.get("crossUnPnl", 0.0))
                return max(balance + cross_un_pnl, 0.0)
        raise RuntimeError("USDT balance not found on testnet account")

    def mark_price(self, symbol: str) -> float:
        payload = self._request(
            "GET",
            "/fapi/v1/premiumIndex",
            params={"symbol": symbol},
        )
        return float(payload["markPrice"])

    @staticmethod
    def _order(payload: dict[str, Any]) -> ExchangeOrder:
        return ExchangeOrder(
            symbol=str(payload.get("symbol", "")),
            order_id=str(payload.get("orderId", "")),
            client_order_id=str(payload.get("clientOrderId", "")),
            side=str(payload.get("side", "")),
            status=str(payload.get("status", "")),
            orig_qty=float(payload.get("origQty", 0.0)),
            executed_qty=float(payload.get("executedQty", 0.0)),
            reduce_only=bool(payload.get("reduceOnly", False)),
            update_time_ms=int(
                payload.get(
                    "updateTime",
                    payload.get("time", int(time.time() * 1000)),
                )
            ),
        )

    def snapshot(self) -> ExchangeSnapshot:
        equity = self.account_equity_usdt()
        raw_positions = self._request(
            "GET",
            "/fapi/v3/positionRisk",
            signed=True,
        )
        raw_orders = self._request(
            "GET",
            "/fapi/v1/openOrders",
            signed=True,
        )
        positions: list[ExchangePosition] = []
        for item in raw_positions:
            qty = float(item.get("positionAmt", 0.0))
            if abs(qty) <= 0.0:
                continue
            mark = float(item.get("markPrice", 0.0))
            notional = abs(float(item.get("notional", 0.0)))
            if notional <= 0.0:
                notional = abs(qty * mark)
            positions.append(
                ExchangePosition(
                    symbol=str(item["symbol"]),
                    side="LONG" if qty > 0.0 else "SHORT",
                    quantity=qty,
                    weight=(notional / equity) if equity > 0.0 else 0.0,
                    entry_price=float(item.get("entryPrice", 0.0)),
                    mark_price=mark,
                    unrealized_pnl=float(item.get("unRealizedProfit", 0.0)),
                )
            )
        orders = tuple(self._order(x) for x in raw_orders)
        return ExchangeSnapshot(
            equity_usdt=equity,
            positions=tuple(positions),
            open_orders=orders,
        )

    def get_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        try:
            payload = self._request(
                "GET",
                "/fapi/v1/order",
                params={
                    "symbol": symbol,
                    "origClientOrderId": client_order_id,
                },
                signed=True,
            )
        except BinanceApiError as exc:
            if exc.code == -2013:
                return None
            raise
        return self._order(payload)

    def _submit_order(self, params: dict[str, Any]) -> ExchangeOrder:
        self._require_order_arm()
        self._require_one_way()
        try:
            payload = self._request(
                "POST",
                "/fapi/v1/order",
                params=params,
                signed=True,
            )
            return self._order(payload)
        except (httpx.TimeoutException, httpx.TransportError, BinanceApiError) as exc:
            ambiguous = not isinstance(exc, BinanceApiError) or (
                exc.status_code is not None and exc.status_code >= 500
            )
            client_id = str(params.get("newClientOrderId", ""))
            symbol = str(params.get("symbol", ""))
            if ambiguous and client_id and symbol:
                existing = self.get_order_by_client_id(symbol, client_id)
                if existing is not None:
                    return existing
            raise

    def place_market_entry(
        self,
        symbol: str,
        side: str,
        target_weight: float,
        client_order_id: str,
    ) -> ExchangeOrder:
        equity = self.account_equity_usdt()
        price = self.mark_price(symbol)
        qty = self._market_quantity(
            symbol,
            target_notional=equity * float(target_weight),
            price=price,
        )
        exchange_side = "BUY" if side.upper() == "LONG" else "SELL"
        return self._submit_order(
            {
                "symbol": symbol,
                "side": exchange_side,
                "type": "MARKET",
                "quantity": format(qty, "f"),
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            }
        )

    def cancel_order(self, order: ExchangeOrder) -> ExchangeOrder:
        self._require_order_arm()
        payload = self._request(
            "DELETE",
            "/fapi/v1/order",
            params={
                "symbol": order.symbol,
                "orderId": order.order_id,
            },
            signed=True,
        )
        return self._order(payload)

    def close_position_reduce_only(
        self,
        position: ExchangePosition,
        client_order_id: str,
    ) -> ExchangeOrder | None:
        qty = abs(float(position.quantity))
        if qty <= 0.0:
            return None
        exchange_side = "SELL" if position.quantity > 0.0 else "BUY"
        return self._submit_order(
            {
                "symbol": position.symbol,
                "side": exchange_side,
                "type": "MARKET",
                "quantity": format(qty, "f"),
                "reduceOnly": "true",
                "newClientOrderId": client_order_id,
                "newOrderRespType": "RESULT",
            }
        )
