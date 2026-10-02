from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from backtesting import Strategy

from marketalyzer.backtest.costs import BistCosts
from marketalyzer.paper.account import PaperAccount
from marketalyzer.paper.feed import FrameFeed
from marketalyzer.paper.trader import PaperTrader, replay

IST = ZoneInfo("Europe/Istanbul")
FREE = BistCosts(commission_rate=0, bsmv_rate=0)


class AboveLevel(Strategy):
    """Long while the close is above ``level``."""

    level = 100

    def init(self):
        pass

    def next(self):
        if self.data.Close[-1] > self.level and not self.position:
            self.buy()
        elif self.data.Close[-1] <= self.level and self.position:
            self.position.close()


def frame(index, closes):
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1e4},
        index=pd.DatetimeIndex(index, name="Date"),
    )


def session(day, opens):
    """Three 5-minute bars from 10:00 opening at the given prices."""
    index = pd.date_range(f"{day} 10:00", periods=len(opens), freq="5min")
    return frame(index, opens)


@pytest.fixture
def market():
    """Daily closes above 100 until 1 October, when the close drops to 80."""
    days = pd.bdate_range("2026-09-01", "2026-10-02")
    closes = [120.0] * len(days)
    closes[-2] = 80.0  # 2026-10-01
    closes[-1] = 80.0
    intraday = pd.concat(
        [
            session("2026-09-30", [120.0, 120.5, 121.0]),
            session("2026-10-01", [110.0, 100.0, 90.0]),
            session("2026-10-02", [81.0, 80.5, 80.0]),
        ]
    )
    return FrameFeed({"THYAO": intraday}, {"THYAO": frame(days, closes)})


@pytest.fixture
def account(tmp_path):
    with PaperAccount.create(tmp_path / "paper.sqlite", 100_000, FREE, 0.0) as account:
        yield account


def test_replay_follows_the_backtest_timing(account, market):
    reports = replay(account, market, ["THYAO"], AboveLevel)
    buy, sell = account.fills()

    # Long from the start: bought at the first bar after the first step (10:05).
    assert (buy.side, buy.price, buy.time) == (
        "buy",
        120.5,
        datetime(2026, 9, 30, 10, 5, tzinfo=IST),
    )
    assert buy.qty == 98_000 // 120.0  # 2% of the cash is kept as a buffer
    # The 1 October close turns the strategy flat; it sells at the next open.
    assert (sell.side, sell.price, sell.time) == (
        "sell",
        81.0,
        datetime(2026, 10, 2, 10, 0, tzinfo=IST),
    )
    flat = next(r for r in reports if r.signals.get("THYAO") is False)
    assert flat.time == datetime(2026, 10, 1, 18, 10, tzinfo=IST)
    assert account.positions() == []
    assert account.summary()["realized_pnl"] == pytest.approx(buy.qty * (81.0 - 120.5))
    assert account.equity_curve()


def test_no_duplicate_orders_while_one_is_open(account, market):
    trader = PaperTrader(account, ["THYAO"], market, AboveLevel)
    first = trader.step(datetime(2026, 9, 30, 10, 5, tzinfo=IST))
    assert len(first.orders) == 1
    second = trader.step(datetime(2026, 9, 30, 10, 5, tzinfo=IST))
    assert second.orders == []
    assert len(account.orders(status="open")) == 1


def test_weights_size_the_position(account, market):
    trader = PaperTrader(
        account, ["THYAO"], market, AboveLevel, weights={"thyao": 0.25}
    )
    (order,) = trader.step(datetime(2026, 9, 30, 10, 5, tzinfo=IST)).orders
    assert order.qty == 24_500 // 120.0


def test_rejects_weights_above_one(account, market):
    with pytest.raises(ValueError, match="Weights"):
        PaperTrader(account, ["THYAO"], market, weights={"THYAO": 1.5})


def test_feed_errors_are_reported_per_symbol(account, market):
    trader = PaperTrader(account, ["THYAO", "GARAN"], market, AboveLevel)
    report = trader.step(datetime(2026, 9, 30, 10, 5, tzinfo=IST))
    assert "KeyError" in report.errors["GARAN"]
    assert report.signals == {"THYAO": True}
    assert report.bars == 1
    assert report.to_dict()["orders"][0]["symbol"] == "THYAO"


def test_without_a_strategy_it_only_processes_bars(account, market):
    account.submit("THYAO", "buy", 10, now=datetime(2026, 9, 30, 9, 0, tzinfo=IST))
    report = PaperTrader(account, ["THYAO"], market).step(
        datetime(2026, 9, 30, 10, 15, tzinfo=IST)
    )
    assert report.bars == 3
    assert [fill.price for fill in report.fills] == [120.0]
    assert report.signals == {}
    assert account.mark("THYAO") == 121.0


def test_run_steps(account, market, monkeypatch):
    trader = PaperTrader(account, ["THYAO"], market)
    seen = []
    monkeypatch.setattr("marketalyzer.paper.trader.clock.sleep", seen.append)
    trader.run(poll=5, steps=3, on_step=seen.append)
    assert [x for x in seen if isinstance(x, (int, float))] == [5, 5]
    assert len([x for x in seen if not isinstance(x, (int, float))]) == 3
