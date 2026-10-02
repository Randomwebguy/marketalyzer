from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from marketalyzer.paper import feed as feed_module
from marketalyzer.paper.account import PaperAccount
from marketalyzer.paper.cli import account_path
from marketalyzer.web import demo
from marketalyzer.web.app import create_app

TOKEN = "test-token"


@pytest.fixture
def client():
    with TestClient(create_app(TOKEN)) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        yield client


@pytest.fixture
def anonymous():
    with TestClient(create_app(TOKEN), follow_redirects=False) as client:
        yield client


@pytest.fixture
def thyao(fake_fetch, prices, as_rows):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    return fake_fetch


class TestAccess:
    def test_pages_need_the_token(self, anonymous):
        login = anonymous.get("/")
        assert login.status_code == 401
        assert "Erişim anahtarı" in login.text
        assert anonymous.get("/api/meta").status_code == 401
        assert anonymous.get("/?token=yanlış-ğ").status_code == 401

    def test_static_files_are_public(self, anonymous):
        for path in (
            "/static/app.css",
            "/static/app.js",
            "/static/js/core.js",
            "/static/js/pages/panel.js",
            "/static/fonts/doto-latin.woff2",
            "/manifest.webmanifest",
            "/sw.js",
        ):
            assert anonymous.get(path).status_code == 200, path
        manifest = anonymous.get("/manifest.webmanifest")
        assert manifest.headers["content-type"].startswith("application/manifest+json")
        assert manifest.json()["display"] == "standalone"

    def test_login_form(self, anonymous):
        wrong = anonymous.post("/login", data={"token": "nope"})
        assert (wrong.status_code, wrong.headers["location"]) == (303, "/?error=1")
        right = anonymous.post("/login", data={"token": TOKEN})
        assert (right.status_code, right.headers["location"]) == (303, "/")
        assert "max-age" in right.headers["set-cookie"].lower()
        app = anonymous.get("/")
        assert app.status_code == 200
        assert 'id="view"' in app.text

    def test_logout_clears_the_cookie(self, anonymous):
        anonymous.get(f"/?token={TOKEN}")
        response = anonymous.get("/logout")
        assert (response.status_code, response.headers["location"]) == (303, "/")
        assert anonymous.get("/api/meta").status_code == 401

    def test_token_in_the_address_sets_the_cookie(self, anonymous):
        assert anonymous.get(f"/?token={TOKEN}").status_code == 200
        assert anonymous.get("/api/meta").status_code == 200


def test_meta(client):
    meta = client.get("/api/meta").json()
    assert meta["demo"] is False
    assert meta["account"] == "web"
    assert meta["strategies"]["sma_cross"]["params"] == {"fast": 10, "slow": 50}
    assert "3A" in meta["ranges"]
    assert client.get("/api/strategies").json() == meta["strategies"]


def test_prices(client, thyao):
    data = client.get("/api/prices?symbol=thyao.is&span=1Y").json()
    assert (data["symbol"], data["span"], data["interval"]) == ("THYAO", "1Y", "1d")
    assert data["rows"][0].keys() == {"t", "o", "h", "l", "c", "v"}
    assert data["last"] == data["rows"][-1]["c"]
    _, params = thyao.calls[-1]
    assert params["adjustment"] == "splits_only"
    assert client.get("/api/prices?symbol=THYAO&span=10Y").status_code == 400


def test_fx(client, fake_fetch, prices, as_rows):
    fake_fetch.responses[("currency", "USDTRY")] = as_rows(prices)
    body = client.get("/api/fx?pair=usdtry").json()
    assert body["pair"] == "USDTRY"
    assert body["last"] == pytest.approx(prices["Close"].iloc[-1], rel=1e-4)
    assert client.get("/api/fx?pair=NOPE").status_code == 400


def test_watchlist(client, thyao):
    quotes = client.get("/api/watchlist?symbols=THYAO,NOPE,thyao").json()
    assert [q["symbol"] for q in quotes] == ["THYAO", "NOPE"]
    assert len(quotes[0]["spark"]) == 30
    assert "error" in quotes[1]
    assert client.get("/api/watchlist?symbols=").json() == []


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
    assert result["trades"] == len(result["trade_list"]) > 0
    assert len(result["equity"]) == len(result["hold"])
    assert result["hold"][0]["v"] == result["equity"][0]["v"]
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
    assert result["equity"]


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
    assert result["equity"][0].keys() == {"t", "v"}
    assert result["equity_final"] == result["equity"][-1]["v"]


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
    assert created["settings"]["commission_rate"] == 0.002
    assert created["equity_curve"] == []
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
    with PaperAccount(account_path("web")) as account:
        account.submit("THYAO", "buy", 10, now=yesterday.replace(hour=9))
    step = client.post("/api/paper/step", json={"symbols": ["THYAO"]}).json()
    assert step["bars"] == 1
    assert step["fills"][0]["qty"] == 10
    status = client.get("/api/paper").json()
    assert status["positions"][0]["qty"] == 10
    assert len(status["equity_curve"]) == 1

    reset = client.post("/api/paper/init", json={"reset": True}).json()
    assert reset["positions"] == []


class TestDemo:
    def test_payload_is_deterministic(self):
        params = {"period1": "1735689600", "period2": "1767225600", "interval": "1d"}
        first = demo.chart_payload("THYAO.IS", params)
        assert first == demo.chart_payload("THYAO.IS", params)
        result = first["chart"]["result"][0]
        bars = result["indicators"]["quote"][0]
        assert len(result["timestamp"]) > 200
        assert all(low <= high for low, high in zip(bars["low"], bars["high"]))
        assert first != demo.chart_payload("GARAN.IS", params)

    def test_intraday_and_quote_ranges(self):
        now = datetime.now()
        params = {
            "period1": str(int((now - timedelta(days=10)).timestamp())),
            "period2": str(int(now.timestamp())),
            "interval": "5m",
        }
        intraday = demo.chart_payload("THYAO.IS", params)["chart"]["result"][0]
        steps = np.diff(intraday["timestamp"])
        assert (steps[steps < 3600] == 300).all()
        quote = demo.chart_payload("THYAO.IS", {"range": "1d", "interval": "1d"})
        assert len(quote["chart"]["result"][0]["timestamp"]) == 1

    def test_enable_feeds_the_provider(self, monkeypatch):
        from marketalyzer.backtest.data import load_ohlcv
        from openbb_bist.utils import yahoo

        monkeypatch.setattr(yahoo, "get_chart", yahoo.get_chart)
        demo.enable()
        frame = load_ohlcv("THYAO", "2025-01-02", "2025-03-31", cache=False)
        assert 55 <= len(frame) <= 65
        assert (
            demo.chart_payload("XU100.IS", {"range": "1d", "interval": "1d"})["chart"][
                "result"
            ][0]["meta"]["instrumentType"]
            == "INDEX"
        )


class TestAccessToken:
    @pytest.fixture(autouse=True)
    def no_env_token(self, monkeypatch):
        monkeypatch.delenv("MARKETALYZER_TOKEN", raising=False)

    def test_token_is_kept_between_runs(self):
        from marketalyzer.web.cli import access_token, token_path

        first = access_token()
        assert len(first) >= 16
        assert access_token() == first
        assert token_path().read_text().strip() == first

    def test_new_token_replaces_the_stored_one(self):
        from marketalyzer.web.cli import access_token

        first = access_token()
        second = access_token(rotate=True)
        assert second != first
        assert access_token() == second

    def test_environment_wins(self, monkeypatch):
        from marketalyzer.web.cli import access_token, token_path

        monkeypatch.setenv("MARKETALYZER_TOKEN", "  from-env-token-123  ")
        assert access_token() == "from-env-token-123"
        assert not token_path().exists()

    def test_short_or_broken_file_gets_a_new_token(self):
        from marketalyzer.web.cli import access_token, token_path

        token_path().parent.mkdir(parents=True)
        token_path().write_text("short")
        token = access_token()
        assert token != "short" and len(token) >= 16

    def test_print_token(self, capsys):
        from marketalyzer.web.cli import access_token, main

        assert main(["--print-token"]) == 0
        assert capsys.readouterr().out.strip() == access_token()
