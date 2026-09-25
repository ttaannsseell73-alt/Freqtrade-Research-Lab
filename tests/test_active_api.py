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


def test_execution_capabilities_fail_closed_without_credentials(monkeypatch):
    monkeypatch.delenv("BINANCE_TESTNET_API_KEY", raising=False)
    monkeypatch.delenv("BINANCE_TESTNET_API_SECRET", raising=False)
    monkeypatch.delenv("CSL_ALLOW_TESTNET_ORDERS", raising=False)
    payload = client().get("/v1/execution/capabilities").json()
    assert payload["testnet_only"] is True
    assert payload["live_endpoint_blocked"] is True
    assert payload["credentials_present"] is False
    assert payload["orders_armed"] is False
    assert payload["status"] == "WAITING_FOR_TESTNET_CREDENTIALS"


def test_execution_capabilities_never_expose_secret_values(monkeypatch):
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "test-key-value")
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "test-secret-value")
    monkeypatch.setenv("CSL_ALLOW_TESTNET_ORDERS", "YES")
    response = client().get("/v1/execution/capabilities")
    assert "test-key-value" not in response.text
    assert "test-secret-value" not in response.text
    payload = response.json()
    assert payload["credentials_present"] is True
    assert payload["orders_armed"] is True
    assert payload["status"] == "ARMED_NOT_VERIFIED"


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



def _clean_primary(direction="LONG", strategy_id="squeeze_momentum", timeframe="4h"):
    return {
        "strategy_id": strategy_id,
        "timeframe": timeframe,
        "direction": direction,
        "fresh": True,
        "closed_candle": True,
        "signal_time_ms": 1790347200000,
        "trades": 30,
        "profit_factor": 1.8,
        "max_drawdown": 0.20,
        "net_return": 0.50,
        "evidence_consistent": True,
    }


def test_intent_api_collapses_same_direction_support_to_one_trade():
    payload = client().post(
        "/v1/intent/admit",
        json={
            "symbol": "SFPUSDT",
            "signals": [
                _clean_primary(),
                _clean_primary(
                    strategy_id="mavilimw",
                    timeframe="1h",
                ),
            ],
            "liquidity_status": "TRADEABLE",
        },
    ).json()
    assert payload["allowed"] is True
    assert payload["status"] == "TRADE"
    assert payload["support_count"] == 2
    assert payload["side"] == "LONG"
    assert payload["intent_id"]


def test_intent_api_blocks_direction_conflict():
    payload = client().post(
        "/v1/intent/admit",
        json={
            "symbol": "SFPUSDT",
            "signals": [
                _clean_primary(direction="LONG"),
                _clean_primary(
                    direction="SHORT",
                    strategy_id="mavilimw",
                    timeframe="1h",
                ),
            ],
        },
    ).json()
    assert payload["allowed"] is False
    assert payload["status"] == "DIRECTION_CONFLICT"


def test_intent_api_support_cannot_replace_frozen_primary():
    payload = client().post(
        "/v1/intent/admit",
        json={
            "symbol": "SFPUSDT",
            "signals": [
                _clean_primary(
                    strategy_id="mavilimw",
                    timeframe="1h",
                ),
            ],
        },
    ).json()
    assert payload["allowed"] is False
    assert payload["reason"] == "primary_setup_not_fresh"


def test_system_exposes_intent_gate_policy():
    payload = client().get("/v1/system").json()
    gate = payload["intent_gate"]
    assert gate["require_fresh_signal"] is True
    assert gate["require_closed_candle"] is True
    assert gate["same_direction_multi_strategy"] == "SINGLE_INTENT_WITH_SUPPORT"
