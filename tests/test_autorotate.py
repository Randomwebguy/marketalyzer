"""Monthly momentum rotation run on a paper account, live or replayed.

Prices come from the demo generator (synthetic, deterministic).
"""

from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

# Serve demo bars instead of Yahoo; keep the model list off the network.
from test_ai_lab import TOKEN, no_models, synthetic  # noqa: F401

from marketalyzer import services
from marketalyzer.paper import autorotate
from marketalyzer.paper.account import PaperAccount
from marketalyzer.paper.cli import account_path
from marketalyzer.paper.feed import FrameFeed
from marketalyzer.paper.models import IST
from marketalyzer.web.app import create_app

TODAY = services.today()
SYMBOLS = ["THYAO", "GARAN", "ASELS", "BIMAS", "AKBNK", "EREGL"]


def feed_for(start, end, symbols=SYMBOLS):
    hourly = {
        c: services.load_bars(c, start - timedelta(days=3), end, "1h")[0]
        for c in symbols
    }
    daily = {
        c: services.load_bars(c, start - timedelta(days=600), end, "1d")[0]
        for c in [*symbols, "XU100"]
    }
    return FrameFeed(hourly, daily, interval="1h")


def plan(**changes):
    values = {"symbols": SYMBOLS, "top": 2, "lookback_months": 6}
    values.update(changes)
    return autorotate.Plan(**values)


def month_start(day):
    return day.replace(day=1)


@pytest.fixture
def replayed(tmp_path):
    start, end = TODAY - timedelta(days=100), TODAY - timedelta(days=5)
    account = PaperAccount.create(tmp_path / "paper.sqlite", 100_000)
    feed = feed_for(start, end)
    result = autorotate.replay(account, feed, plan(), start, end)
    yield account, result, feed
    account.close()


def test_replay_rebalances_monthly_with_hourly_fills(replayed):
    account, result, feed = replayed
    history = result["history"]
    # The first activation, then the start of every later month.
    months = {row["at"][:7] for row in history}
    assert len(history) >= 3 and len(months) == len(history)
    assert history[0]["first"] is True
    for row in history[1:]:
        decided = date.fromisoformat(row["decided_on"][:10])
        assert decided < month_start(date.fromisoformat(row["at"][:10]))
    fills = account.fills()
    assert fills
    for fill in fills:  # Every fill is at the open of an hourly bar.
        opened = fill.time.replace(tzinfo=None)
        assert opened in feed.frames[fill.symbol].index
    for row in history:
        sells = [f for f in fills if f.symbol in row["sold"] and f.side == "sell"
                 and f.time.date().isoformat() >= row["at"][:10]]  # fmt: skip
        buys = [f for f in fills if f.symbol in row["bought"] and f.side == "buy"
                and f.time.date().isoformat() >= row["at"][:10]]  # fmt: skip
        if sells and buys:
            assert max(f.time for f in sells[: len(row["sold"])]) <= min(
                f.time for f in buys[: len(row["bought"])]
            )
    final = result["plan"]
    assert sorted(final["owned"]) == sorted(p["symbol"] for p in final["picks"])
    assert sorted(p.symbol for p in account.positions()) == sorted(final["owned"])


def test_the_rotation_never_sells_other_positions(tmp_path):
    start, end = TODAY - timedelta(days=100), TODAY - timedelta(days=5)
    feed = feed_for(start, end, [*SYMBOLS, "SISE"])
    account = PaperAccount.create(tmp_path / "paper.sqlite", 100_000)
    before = datetime.combine(start - timedelta(days=2), datetime.min.time(), IST)
    order = account.submit("SISE", "buy", 10, now=before)
    for bar in feed.bars("SISE", None, before + timedelta(days=2)):
        account.process_bar("SISE", bar)
    assert account.order(order.id).status == "filled"
    result = autorotate.replay(account, feed, plan(), start, end)
    assert account.held("SISE") == 10
    assert "SISE" not in result["plan"]["owned"]
    account.close()


def test_replay_reports_benchmarks(replayed):
    _, result, _feed = replayed
    assert result["summary"]["return_pct"] is not None
    assert result["equal"]["return_pct"] is not None
    assert result["benchmark"]["symbol"] == "XU100"
    assert len(result["equity"]) > 10


def test_plan_is_saved_and_checked():
    saved = autorotate.save_plan("web", plan(top=3))
    assert autorotate.load_plan("web") == saved
    with pytest.raises(ValueError, match="hisse sayısından"):
        autorotate.save_plan("web", plan(top=9))


def test_web_turns_the_rotation_on_and_steps(monkeypatch):
    app = create_app(TOKEN)
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {TOKEN}"
        assert client.get("/api/rotation/auto").json()["plan"] is None
        client.post("/api/paper/init", json={"cash": 100_000})
        body = {"enabled": True, "symbols": SYMBOLS, "top": 2, "lookback_months": 6}
        state = client.post("/api/rotation/auto", json=body).json()
        assert state["plan"]["enabled"] is True and state["plan"]["top"] == 2
        start = TODAY - timedelta(days=60)
        feed = feed_for(start, TODAY)
        monkeypatch.setattr(autorotate, "live_feed", lambda: feed)
        report = client.post("/api/rotation/auto/step").json()
        assert report["decision"]["picks"]
        assert {o["side"] for o in report["orders"]} == {"buy"}
        off = client.post("/api/rotation/auto", json={**body, "enabled": False}).json()
        assert off["plan"]["enabled"] is False
        assert client.post("/api/rotation/auto/step").json()["skipped"] is True
    assert account_path("web").exists()
