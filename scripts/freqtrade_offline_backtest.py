from __future__ import annotations

import argparse
import json
from pathlib import Path

from freqtrade.commands.arguments import Arguments
from freqtrade.commands.optimize_commands import setup_optimize_configuration
from freqtrade.enums import RunMode
from freqtrade.optimize.backtesting import Backtesting
from freqtrade.resolvers import ExchangeResolver


def make_market(pair: str) -> dict:
    base, rest = pair.split("/", 1)
    quote, settle = rest.split(":", 1)
    return {
        "id": f"{base}{quote}",
        "symbol": pair,
        "base": base,
        "quote": quote,
        "settle": settle,
        "baseId": base,
        "quoteId": quote,
        "settleId": settle,
        "type": "swap",
        "spot": False,
        "margin": False,
        "swap": True,
        "future": True,
        "option": False,
        "active": True,
        "contract": True,
        "linear": True,
        "inverse": False,
        "tierBased": True,
        "percentage": True,
        "taker": 0.0005,
        "maker": 0.0005,
        "contractSize": 1.0,
        "expiry": None,
        "expiryDatetime": None,
        "strike": None,
        "optionType": None,
        "precision": {
            "amount": 0.00000001,
            "price": 0.00000001,
        },
        "limits": {
            "leverage": {"min": 1.0, "max": 1.0},
            "amount": {"min": 0.00000001, "max": None},
            "price": {"min": 0.00000001, "max": None},
            "cost": {"min": 0.0, "max": None},
        },
        "info": {
            "symbol": f"{base}{quote}",
            "pair": f"{base}{quote}",
            "contractType": "PERPETUAL",
            "status": "TRADING",
            "deliveryDate": "4133404800000",
        },
    }


def build_offline_exchange(config: dict, pairs: list[str]):
    exchange = ExchangeResolver.load_exchange(
        config,
        validate=False,
        load_leverage_tiers=False,
    )
    markets = {pair: make_market(pair) for pair in pairs}
    exchange._markets = markets
    exchange._api.markets = markets
    exchange._api_async.markets = markets
    exchange._leverage_tiers = {
        pair: [
            {
                "minNotional": 0.0,
                "maxNotional": 1_000_000_000_000.0,
                "maintenanceMarginRate": 0.005,
                "maxLeverage": 1.0,
                "maintAmt": 0.0,
            }
        ]
        for pair in pairs
    }
    return exchange


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--strategy-path", required=True)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--timeframe", required=True)
    parser.add_argument("--timerange", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--fee", type=float, default=0.0005)
    parser.add_argument("--pairs", nargs="+", required=True)
    args = parser.parse_args()

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)

    cli = [
        "backtesting",
        "--config", args.config,
        "--data-dir", args.data_dir,
        "--strategy-path", args.strategy_path,
        "--strategy", args.strategy,
        "--timeframe", args.timeframe,
        "--timerange", args.timerange,
        "--cache", "none",
        "--fee", str(args.fee),
        "--max-open-trades", str(max(1, len(args.pairs))),
        "--stake-amount", "1000",
        "--dry-run-wallet", "1000000000",
        "--export", "trades",
        "--backtest-directory", str(output),
        "--pairs", *args.pairs,
    ]
    parsed = Arguments(cli).get_parsed_arg()
    config = setup_optimize_configuration(parsed, RunMode.BACKTEST)
    exchange = build_offline_exchange(config, args.pairs)

    manifest = {
        "engine": "freqtrade-2026.8",
        "exchange_mode": "offline-binance-metadata",
        "market_data": "Binance Vision",
        "pairs": args.pairs,
        "timeframe": args.timeframe,
        "strategy": args.strategy,
        "timerange": args.timerange,
        "fee_per_side": args.fee,
        "leverage": 1.0,
        "maintenance_margin_rate": 0.005,
    }
    (output / "OFFLINE_EXCHANGE_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    try:
        backtesting = Backtesting(config, exchange=exchange)
        backtesting.start()
    finally:
        exchange.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
