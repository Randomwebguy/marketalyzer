"""The volatility-sized paper account on Binance data, with fake market data."""

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import live, runner

NOW = datetime(2026, 10, 4, 0, 10, tzinfo=timezone.utc)
PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "NEWUSDT"]


def candles(seed, days=1200, end=date(2026, 10, 5), drift=0.002, volume=50e6):
    rng = np.random.default_rng(seed)
    index = pd.date_range(end - timedelta(days=days - 1), end, freq="D")
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.02, len(index))))
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1.0, "QuoteVolume": volume},
                        index=index)  # fmt: skip


MARKET = {
    "BTCUSDT": candles(1, volume=900e6),
    "ETHUSDT": candles(2, volume=500e6),
    "SOLUSDT": candles(3, volume=300e6),
    "XRPUSDT": candles(4, volume=1e6),  # too little volume
    "NEWUSDT": candles(5, days=200, volume=800e6),  # too young
}


def fetch(symbol, start, today):
    frame = MARKET[symbol]
    return frame[
        (frame.index >= pd.Timestamp(start)) & (frame.index < pd.Timestamp(today))
    ]


def prices(symbols):
    return {
        s: float(fetch(s, date(2000, 1, 1), NOW.date())["Close"].iloc[-1])
        for s in symbols
    }


@pytest.fixture
def account():
    plan = live.Plan(signal="donchian", target_vol=0.25, size=3)
    with live.open_account(runner.ledger_path("trend"), plan, now=NOW) as ledger:
        yield ledger


def go(ledger, now=NOW):
    return live.step(ledger, now, fetch, lambda: PAIRS, prices)


def test_the_first_step_picks_the_universe_and_buys_toward_the_targets(account):
    report = go(account)
    assert report["selected"] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert report["days"] == ["2026-10-03"]
    goal = account.get("targets")
    assert (
        goal and set(goal) <= set(report["selected"]) and sum(goal.values()) <= 1 + 1e-9
    )
    assert {f["symbol"] for f in report["fills"]} == set(goal)
    assert all(f["side"] == "buy" for f in report["fills"])
    value = account.value(prices(list(goal)))
    for pair, want in goal.items():
        held = account.positions()[pair].units * prices([pair])[pair] / value
        assert held == pytest.approx(want, rel=0.01)
    again = go(account, NOW + timedelta(minutes=50))  # same day: nothing new
    assert again["days"] == [] and again["fills"] == []


def test_positions_are_trimmed_and_closed_toward_new_targets(account, monkeypatch):
    go(account)
    goal = account.get("targets")
    first, *rest = sorted(goal, key=goal.get, reverse=True)
    halved = {first: goal[first] / 2}
    monkeypatch.setattr(live, "targets", lambda candles, members, plan: halved)
    report = go(account, NOW + timedelta(days=1))
    sides = {(f["symbol"], f["side"]) for f in report["fills"]}
    assert (first, "sell") in sides and all((p, "sell") in sides for p in rest)
    assert set(account.positions()) == {first}
    trimmed = [f for f in report["fills"] if f["symbol"] == first][0]
    assert trimmed["reason"].startswith("hedefe indir")


def test_ranked_tranches_hold_only_the_strongest_and_split_the_weight(monkeypatch):
    frames = {p: fetch(p, date(2023, 1, 1), NOW.date()) for p in PAIRS[:3]}
    members = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    always = (
        np.ones(len(frames["BTCUSDT"]), bool),
        np.zeros(len(frames["BTCUSDT"]), bool),
    )
    monkeypatch.setattr(
        live.neural, "_flags", lambda *a, **k: {p: always for p in members}
    )
    monkeypatch.setattr(
        live.neural, "_above", lambda close, n: np.ones(len(close), bool)
    )
    one = live.targets(frames, members, live.Plan(top=1, target_vol=5.0))
    three = live.targets(frames, members, live.Plan(top=1, tranches=3, target_vol=5.0))
    assert len(one) == 1 and sum(one.values()) == pytest.approx(1.0)  # capped at 1x
    assert 1 <= len(three) <= 3 and sum(three.values()) == pytest.approx(1.0)
    assert live.Plan(top=5, tranches=3, target_vol=0.4).label() == (
        "En güçlü 5 · 3 dilim · oynaklık %40"
    )


def test_the_runner_sends_planned_accounts_to_binance_and_reports_them(
    account, monkeypatch
):
    monkeypatch.setattr(live, "listed_pairs", lambda: PAIRS)
    monkeypatch.setattr(live, "latest_prices", prices)
    report = runner.step(account, NOW, fetch=fetch)
    assert report["fills"]
    state = runner.status(account)
    assert state["kind"] == "sized" and state["plan"]["signal"] == "donchian"
    assert state["label"].startswith("Donchian") and state["targets"]
    assert state["selection"] == report["selected"]
    assert runner.accounts() == ["trend"]
