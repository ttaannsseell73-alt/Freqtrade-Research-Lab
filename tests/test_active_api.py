from pathlib import Path

from fastapi.testclient import TestClient

from coin_strategy_lab.api import create_app


def client() -> TestClient:
    app = create_app(
        Path("config/active_pool_v1.json"),
        Path("config/active_system_v1.json"),
    )
    return TestClient(app)


def test_health_reports_frozen_30_setup_runtime():
    response = client().get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "paper_only"
    assert payload["setup_count"] == 30


def test_system_reports_10_core_20_active():
    payload = client().get("/v1/system").json()
    assert payload["setup_count"] == 30
    assert payload["core_count"] == 10
    assert payload["active_count"] == 20
    assert round(payload["configured_weight"], 6) == 0.68


def test_unknown_symbol_defaults_to_no_route():
    response = client().get("/v1/routes/NOTREALUSDT")
    assert response.status_code == 404
    assert response.json()["detail"] == "symbol_not_in_active_pool"


def test_direction_gate_is_visible_through_api():
    # BIGTIME is frozen as SHORT_ONLY in active-pool v1.
    long = client().post(
        "/v1/admit",
        json={"symbol": "BIGTIMEUSDT", "side": "LONG"},
    ).json()
    assert long["allowed"] is False
    assert long["reason"] == "direction_not_enabled"

    short = client().post(
        "/v1/admit",
        json={"symbol": "BIGTIMEUSDT", "side": "SHORT"},
    ).json()
    assert short["allowed"] is True
    assert short["weight"] == 0.015


def test_kill_switch_is_visible_through_api():
    payload = client().post(
        "/v1/admit",
        json={
            "symbol": "SFPUSDT",
            "side": "LONG",
            "daily_pnl": -0.031,
        },
    ).json()
    assert payload["allowed"] is False
    assert payload["status"] == "KILL_SWITCH"
    assert payload["reason"] == "daily_loss_limit"
