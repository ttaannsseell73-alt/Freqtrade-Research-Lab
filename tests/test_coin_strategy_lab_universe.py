from coin_strategy_lab.universe import parse_usdt_perpetuals


def test_parse_usdt_perpetuals_filters_and_sorts():
    payload = {
        "symbols": [
            {"symbol":"ZZZUSDT","baseAsset":"ZZZ","quoteAsset":"USDT","contractType":"PERPETUAL","status":"TRADING","onboardDate":2},
            {"symbol":"AAAUSDT","baseAsset":"AAA","quoteAsset":"USDT","contractType":"PERPETUAL","status":"TRADING","onboardDate":1},
            {"symbol":"OLDUSDT","baseAsset":"OLD","quoteAsset":"USDT","contractType":"PERPETUAL","status":"BREAK","onboardDate":3},
            {"symbol":"COINUSDC","baseAsset":"COIN","quoteAsset":"USDC","contractType":"PERPETUAL","status":"TRADING","onboardDate":4},
            {"symbol":"QUARTERUSDT","baseAsset":"Q","quoteAsset":"USDT","contractType":"CURRENT_QUARTER","status":"TRADING","onboardDate":5},
        ]
    }
    rows = parse_usdt_perpetuals(payload)
    assert [row.symbol for row in rows] == ["AAAUSDT", "ZZZUSDT"]
    assert all(row.quote_asset == "USDT" for row in rows)
    assert all(row.contract_type == "PERPETUAL" for row in rows)
    assert all(row.status == "TRADING" for row in rows)
