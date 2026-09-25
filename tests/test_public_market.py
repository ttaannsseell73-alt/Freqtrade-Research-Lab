import httpx

from coin_strategy_lab.public_market import (
    BinanceFuturesPublicMarket,
    TradabilitySnapshot,
    classify_tradability,
)


POLICY = {
    "strong": {
        "min_quote_volume_24h": 20_000_000,
        "max_spread_bps": 3,
        "min_side_depth_10bps": 10_000,
        "min_open_interest_notional": 10_000_000,
    },
    "tradeable": {
        "min_quote_volume_24h": 3_000_000,
        "max_spread_bps": 8,
        "min_side_depth_10bps": 1_000,
        "min_open_interest_notional": 2_000_000,
    },
    "hard_block": {
        "max_spread_bps": 25,
        "min_quote_volume_24h": 500_000,
        "min_side_depth_10bps": 250,
        "min_open_interest_notional": 500_000,
    },
}


def snap(volume, spread, depth, oi):
    return TradabilitySnapshot(
        symbol="AAAUSDT",
        quote_volume_24h=volume,
        spread_bps=spread,
        bid_depth_10bps=depth,
        ask_depth_10bps=depth,
        open_interest_notional=oi,
        mark_price=100.0,
    )


def test_tradability_policy_strong_tradeable_review_and_block():
    assert classify_tradability(
        snap(30_000_000, 2.0, 20_000, 20_000_000), POLICY
    ) == "STRONG"
    assert classify_tradability(
        snap(5_000_000, 5.0, 2_000, 3_000_000), POLICY
    ) == "TRADEABLE"
    assert classify_tradability(
        snap(2_000_000, 10.0, 700, 1_500_000), POLICY
    ) == "REVIEW"
    assert classify_tradability(
        snap(400_000, 5.0, 2_000, 3_000_000), POLICY
    ) == "BLOCK"


def test_kline_parser_marks_only_confirmed_candles_closed():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/klines":
            return httpx.Response(
                200,
                json=[
                    [1000, "1", "2", "0.5", "1.5", "10", 1999, "15", 1, "0", "0", "0"],
                    [2000, "1.5", "2", "1", "1.8", "11", 2999, "20", 1, "0", "0", "0"],
                ],
            )
        raise AssertionError(request.url.path)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    market = BinanceFuturesPublicMarket(client=client)
    frame = market.klines(
        "AAAUSDT",
        "1h",
        limit=2,
        server_time_ms=2500,
    )
    assert frame["is_closed"].tolist() == [True, False]
    assert frame.iloc[0]["quote_volume"] == 15.0
