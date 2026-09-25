from pathlib import Path

from fastapi.testclient import TestClient

from coin_strategy_lab.api import create_app


def client() -> TestClient:
    return TestClient(
        create_app(
            Path("config/active_pool_v1.json"),
            Path("config/active_system_v1.json"),
        )
    )


def test_empty_portfolio_is_ready():
    payload = client().post(
        "/v1/portfolio/evaluate",
        json={},
    ).json()
    assert payload["status"] == "READY"
    assert payload["gross_exposure"] == 0.0
    assert round(payload["remaining_exposure"], 6) == 0.70


def test_portfolio_detects_overweight_position():
    payload = client().post(
        "/v1/portfolio/evaluate",
        json={
            "open_positions": [
                {"symbol": "SFPUSDT", "weight": 0.05}
            ]
        },
    ).json()
    assert payload["status"] == "INVALID_STATE"
    assert "overweight_symbol:SFPUSDT" in payload["violations"]


def test_portfolio_daily_loss_kill_switch():
    payload = client().post(
        "/v1/portfolio/evaluate",
        json={"daily_pnl": -0.031},
    ).json()
    assert payload["status"] == "KILL_SWITCH"
    assert payload["kill_switch"] is True
    assert payload["kill_reason"] == "daily_loss_limit"


def test_portfolio_drawdown_kill_switch():
    payload = client().post(
        "/v1/portfolio/evaluate",
        json={"portfolio_drawdown": -0.101},
    ).json()
    assert payload["status"] == "KILL_SWITCH"
    assert payload["kill_reason"] == "portfolio_drawdown_limit"
