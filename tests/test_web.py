from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from marketalyzer.paper import feed as feed_module
from marketalyzer.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client():
    with TestClient(create_app(TOKEN)) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client


@pytest.fixture
def thyao(fake_fetch, prices, as_rows):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    return fake_fetch


def test_token_is_required():
    with TestClient(create_app(TOKEN)) as anonymous:
        assert anonymous.get("/").status_code == 401
        assert anonymous.get("/api/strategies?token=wrong").status_code == 401
        assert anonymous.get("/?token=yanlış-ğ").status_code == 401
        page = anonymous.get(f"/?token={TOKEN}")
        assert page.status_code == 200
        assert "marketalyzer" in page.text
        # The token is remembered in a cookie.
        assert anonymous.get("/api/strategies").status_code == 200


def test_strategies(client):
    strategies = client.get("/api/strategies").json()
    assert strategies["sma_cross"]["params"] == {"fast": 10, "slow": 50}
    assert "fast" in strategies["sma_cross"]["grid"]


def test_backtest_and_plot(client, thyao):
    body = {
        "symbol": "THYAO",
        "strategy": "sma_cross",
        "start": "2024-01-02",
        "end": "2025-02-21",
        "params": {"fast": 5.0, "slow": 20},
        "benchmark": False,
    }
    result = client.post("/api/backtest", json=body).json()
    assert result["params"] == {"fast": 5, "slow": 20}
    assert result["trades"] > 0
    plot = client.get(result["plot"])
    assert plot.status_code == 200
    assert plot.headers["content-type"].startswith("text/html")
    assert client.get("/api/plots/../../etc").status_code == 404


def test_optimized_backtest(client, thyao):
    body = {
        "symbol": "THYAO",
        "optimize": True,
        "benchmark": False,
        "start": "2024-01-02",
        "end": "2025-02-21",
    }
    result = client.post("/api/backtest", json=body).json()
    assert result["best_params"]
    assert result["out_of_sample"]["params"] == result["best_params"]


def test_backtest_errors_are_reported(client, fake_fetch):
    response = client.post("/api/backtest", json={"symbol": "NOPE", "benchmark": False})
    assert response.status_code == 400
    assert "NOPE" in response.json()["detail"]
    response = client.post("/api/backtest", json={"symbol": "THYAO", "strategy": "x"})
    assert response.status_code == 400


def test_walkforward(client, monkeypatch):
    days = pd.bdate_range(end="2026-09-30", periods=120, name="Date")
    close = np.r_[np.linspace(260, 200, 60), np.linspace(200, 300, 60)]
    frame = pd.DataFrame(
        {"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1e6},
        index=days,
    )
    monkeypatch.setattr(
        "marketalyzer.walkforward.load_ohlcv", lambda *args, **kwargs: frame
    )
    body = {"symbol": "THYAO", "start": "2026-01-01", "train": 60, "test": 30}
    result = client.post("/api/walkforward", json=body).json()
    assert len(result["windows"]) == 2


def test_paper_account(client, monkeypatch):
    assert client.get("/api/paper").json() == {"exists": False}
    assert (
        client.post(
            "/api/paper/order", json={"symbol": "THYAO", "side": "buy", "qty": 10}
        ).status_code
        == 404
    )

    created = client.post("/api/paper/init", json={"cash": 50_000}).json()
    assert created["cash"] == 50_000
    assert client.post("/api/paper/init", json={}).status_code == 409

    order = client.post(
        "/api/paper/order",
        json={
            "symbol": "thyao",
            "side": "buy",
            "qty": 10,
            "type": "limit",
            "price": 290.1,
        },
    )
    assert order.status_code == 400
    assert "tick grid" in order.json()["detail"]
    order = client.post(
        "/api/paper/order",
        json={
            "symbol": "thyao",
            "side": "buy",
            "qty": 10,
            "type": "limit",
            "price": 290,
        },
    ).json()
    assert (order["symbol"], order["limit_price"], order["tag"]) == (
        "THYAO",
        290,
        "web",
    )
    cancelled = client.post(f"/api/paper/cancel/{order['id']}").json()
    assert cancelled["status"] == "cancelled"
    assert client.post("/api/paper/cancel/999").status_code == 404

    # A step processes yesterday's daily bar and fills an order placed before it.
    yesterday = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday -= timedelta(days=1)
    bar = pd.DataFrame(
        {
            "Open": [300.0],
            "High": [301.0],
            "Low": [299.0],
            "Close": [300.5],
            "Volume": [1e6],
        },
        index=pd.DatetimeIndex([yesterday], name="Date"),
    )
    monkeypatch.setattr(feed_module, "_fetch_bars", lambda *args: bar)
    from marketalyzer.paper.account import PaperAccount
    from marketalyzer.paper.cli import account_path

    with PaperAccount(account_path("web")) as account:
        account.submit("THYAO", "buy", 10, now=yesterday.replace(hour=9))
    step = client.post("/api/paper/step", json={"symbols": ["THYAO"]}).json()
    assert step["bars"] == 1
    assert step["fills"][0]["qty"] == 10
    status = client.get("/api/paper").json()
    assert status["positions"][0]["qty"] == 10

    reset = client.post("/api/paper/init", json={"reset": True}).json()
    assert reset["positions"] == []
