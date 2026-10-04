"""The 4-hour trend accounts: signals, fixed-risk sizing, trailing stops, liquidation."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from marketalyzer.crypto import levrun, swing

FREE = {"fee": 0.0, "slippage": 0.0}
LONG = swing.Rules(side="long", **FREE)
SHORT = swing.Rules(side="short", **FREE)


def bars(closes, start="2026-01-01"):
    index = pd.date_range(start, periods=len(closes), freq="4h")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c * 1.002, "Low": c * 0.998, "Close": c}, index=index
    )


def daily(values, start="2025-10-01"):
    return pd.Series(
        np.asarray(values, dtype=float), index=pd.date_range(start, periods=len(values))
    )


def test_a_breakout_enters_only_with_the_daily_trend():
    closes = np.r_[np.full(40, 100.0), np.linspace(100, 120, 20)]
    frame = bars(closes)
    rising = daily(np.linspace(50, 100, 120))  # through 2026-01-28, above its average
    falling = daily(np.linspace(100, 50, 120))
    up = swing.signals(frame, rising, LONG)
    assert up["enter"].iloc[41] and not up["enter"].iloc[35]
    assert not swing.signals(frame, falling, LONG)["enter"].any()
    assert swing.signals(frame, falling, swing.Rules(daily_filter=False))["enter"].iloc[
        41
    ]
    cut = swing.signals(frame.iloc[:45], rising, LONG)
    assert cut.equals(up.iloc[:45])  # causal


def test_each_trade_risks_one_percent_and_the_limits_hold():
    account = swing.Account(10_000.0, 10_000.0)
    swing.open_positions(account, LONG, [{"symbol": "A", "close": 100.0, "atr": 1.0, "strength": 1}],
                         {}, "t")  # fmt: skip
    a = account.positions["A"]
    assert (
        a.stop == 98
        and a.units == pytest.approx(50)
        and a.risk_usd == pytest.approx(100)
    )
    assert a.margin == pytest.approx(5000 / 3)
    many = [
        {"symbol": s, "close": 100.0, "atr": 0.1, "strength": k}
        for k, s in enumerate("BCDEF")
    ]
    swing.open_positions(account, LONG, many, {"A": 100.0}, "t")
    sizes = {s: p.units * p.entry for s, p in account.positions.items()}
    assert sizes["F"] == pytest.approx(10_000)  # capped at 1x equity, strongest first
    assert sum(sizes.values()) <= 30_000 + 1e-6 and len(account.positions) <= 5


def test_the_stop_trails_and_costs_about_one_r():
    account = swing.Account(10_000.0, 10_000.0)
    swing.open_positions(account, LONG, [{"symbol": "A", "close": 100.0, "atr": 1.0, "strength": 1}],
                         {}, "t")  # fmt: skip
    swing.trail(account, LONG, "A", 99.0)
    swing.trail(account, LONG, "A", 97.0)  # never loosens
    assert account.positions["A"].stop == 99
    out = swing.check(account, LONG, "A", 100.0, 100.5, 98.5, "t")
    assert out["reason"] == "iz süren stop" and out["r"] == pytest.approx(-0.5)
    swing.open_positions(account, LONG, [{"symbol": "A", "close": 100.0, "atr": 1.0, "strength": 1}],
                         {}, "t")  # fmt: skip
    gap = swing.check(account, LONG, "A", 96.0, 96.5, 95.0, "t")  # opens below the stop
    assert gap["r"] == pytest.approx(-2.0)


class FakeMarket(levrun.Market):
    """A steadily rising coin, a steadily falling one and BTC, 4-hour bars."""

    def __init__(self):
        index = pd.date_range("2026-07-01", periods=600, freq="4h")
        steps = np.arange(600)
        self.four = {
            "UPUSDT": 100 * np.exp(0.004 * steps),
            "DNUSDT": 100 * np.exp(-0.004 * steps),
            "BTCUSDT": 100 * np.exp(0.001 * steps + 0.01 * np.sin(steps)),
        }
        self.four = {s: pd.DataFrame({"Open": c, "High": c * 1.001, "Low": c * 0.999,
                                      "Close": c, "Volume": 1.0}, index=index)
                     for s, c in self.four.items()}  # fmt: skip

    def bars(self, symbol, interval, start=None, limit=1000, now=None):
        frame = self.four[symbol]
        size = {"1m": "1min", "4h": "4h", "1d": "1D"}[interval]
        if interval == "1d":
            frame = frame.resample("1D").agg({"Open": "first", "High": "max", "Low": "min",
                                              "Close": "last", "Volume": "sum"})  # fmt: skip
        elif interval == "1m":
            lo = pd.Timestamp(start.replace(tzinfo=None)).floor("4h")
            frame = frame[frame.index >= lo]
            frame = frame.reindex(
                pd.date_range(lo, frame.index[-1], freq="1min")
            ).ffill()
            frame[["Open", "High", "Low"]] = np.c_[
                frame["Close"], frame["Close"], frame["Close"]
            ]
        ends = frame.index + pd.Timedelta(size)
        keep = ends <= pd.Timestamp(now.replace(tzinfo=None))
        if start is not None:
            keep &= frame.index >= pd.Timestamp(start.replace(tzinfo=None))
            return frame[keep].iloc[:limit]
        return frame[keep].iloc[-limit:]

    def prices(self):
        return {s: float(f["Close"].iloc[-1]) for s, f in self.four.items()}

    def funding(self, symbol):
        return 0.0001

    def universe(self, month):
        return ["UPUSDT", "DNUSDT", "BTCUSDT"]


def test_the_live_accounts_open_the_breakouts_and_exit_on_the_stop():
    levrun.main(["init"])
    market = FakeMarket()
    now = datetime(2026, 9, 15, 8, 0, 5, tzinfo=timezone.utc)
    market.prices = lambda: {s: float(f["Close"].asof(pd.Timestamp("2026-09-15 04:00")))
                             for s, f in market.four.items()}  # fmt: skip
    report = levrun.step(now, market)
    assert (
        "UPUSDT" in report["long"]["opened"] and "DNUSDT" in report["short"]["opened"]
    )
    long_state = levrun.status("long")
    p = {x["symbol"]: x for x in long_state["positions"]}["UPUSDT"]
    assert p["risk_usd"] <= 0.01 * 10_000 + 1e-6  # at most 1 % of equity at the stop
    assert p["units"] * p["entry"] <= 10_000 * 1.0001  # and at most 1x equity per coin
    assert long_state["board"]["UPUSDT"]["enter"] and long_state["universe"]["perps"]
    with levrun.Store(
        levrun.store_path("long")
    ) as store:  # pull the stop above the price
        account = store.account()
        account.positions["UPUSDT"].stop = p["price"] * 1.05
        store.save(account)
    later = levrun.step(now + timedelta(minutes=3), market)
    assert [t["reason"] for t in later["long"]["closed"]] == ["iz süren stop"]
    assert levrun.status("long")["stats"]["trades"] == 1


def test_the_web_shows_both_accounts_at_live_prices(monkeypatch):
    from fastapi.testclient import TestClient

    from marketalyzer.web.app import create_app

    app = create_app("test-token")
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get("/api/leverage").json() == {"exists": False, "accounts": []}
        levrun.main(["init"])
        market = FakeMarket()
        market.prices = lambda: {s: float(f["Close"].asof(pd.Timestamp("2026-09-15 04:00")))
                                 for s, f in market.four.items()}  # fmt: skip
        levrun.step(datetime(2026, 9, 15, 8, 0, 5, tzinfo=timezone.utc), market)
        up = market.prices()["UPUSDT"]
        monkeypatch.setattr(
            levrun.binance, "futures_prices", lambda: {"UPUSDT": up * 1.1}
        )
        data = client.get("/api/leverage").json()
        longs, shorts = data["accounts"]
        assert data["exists"] and longs["side"] == "long" and shorts["side"] == "short"
        mine = {p["symbol"]: p for p in longs["positions"]}["UPUSDT"]
        assert (
            mine["price"] == pytest.approx(up * 1.1)
            and mine["pnl"] > 0
            and mine["r_now"] > 0
        )
        assert longs["value"] > longs["wallet"] and longs["board"]


def test_shorts_mirror_receive_funding_and_a_crash_liquidates():
    account = swing.Account(10_000.0, 10_000.0)
    swing.open_positions(account, SHORT, [{"symbol": "A", "close": 100.0, "atr": 1.0, "strength": 1}],
                         {}, "t")  # fmt: skip
    p = account.positions["A"]
    assert p.stop == 102 and p.liquidation == pytest.approx(100 * (1 + 1 / 3 - 0.005))
    swing.fund(account, SHORT, "A", 0.0001, 100.0)
    assert account.stats["funding"] == pytest.approx(-50 * 100 * 0.0001)
    longs = swing.Account(10_000.0, 10_000.0)
    swing.open_positions(longs, LONG, [{"symbol": "A", "close": 100.0, "atr": 20.0, "strength": 1}],
                         {}, "t")  # stop at 60, beyond the liquidation price  # fmt: skip
    gone = swing.check(longs, LONG, "A", 100.0, 100.0, 65.0, "t")
    assert gone["reason"] == "tasfiye" and longs.stats["liquidations"] == 1
