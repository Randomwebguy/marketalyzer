from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest
from backtesting import Backtest
from openbb_core.provider.utils.errors import EmptyDataError

from marketalyzer.backtest.strategies import SmaCross
from marketalyzer.paper import feed as feed_module
from marketalyzer.paper.feed import (
    FrameFeed,
    ProviderFeed,
    bar_bounds,
    completed_bars,
    completed_daily,
    last_complete_day,
)
from marketalyzer.paper.signals import wants_long

IST = ZoneInfo("Europe/Istanbul")


def intraday(day="2026-10-01", periods=6, start=300.0):
    index = pd.date_range(f"{day} 10:00", periods=periods, freq="5min", name="Date")
    close = start + np.arange(periods) * 0.25
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 1e4,
        },
        index=index,
    )


def daily(closes, end="2026-10-01"):
    index = pd.bdate_range(end=end, periods=len(closes), name="Date")
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1e6},
        index=index,
    )


class TestCompletedBars:
    def test_only_bars_that_ended_before_the_delayed_cutoff(self):
        # At 10:42 a 15-minute delay puts the cutoff at 10:27: the 10:20 bar
        # (ending 10:25) is complete, the 10:25 bar is not.
        now = datetime(2026, 10, 1, 10, 42, tzinfo=IST)
        bars = completed_bars(intraday(), "5m", None, now, timedelta(minutes=15))
        starts = [b.start.strftime("%H:%M") for b in bars]
        assert starts == ["10:00", "10:05", "10:10", "10:15", "10:20"]
        assert bars[0].start.tzinfo is not None
        assert bars[0].end - bars[0].start == timedelta(minutes=5)

    def test_skips_bars_already_processed(self):
        now = datetime(2026, 10, 1, 12, 0, tzinfo=IST)
        since = datetime(2026, 10, 1, 10, 15, tzinfo=IST)
        bars = completed_bars(intraday(), "5m", since, now, timedelta(0))
        assert [b.start.strftime("%H:%M") for b in bars] == ["10:15", "10:20", "10:25"]


def test_last_complete_day():
    delay = timedelta(minutes=15)
    assert last_complete_day(datetime(2026, 10, 1, 18, 0, tzinfo=IST), delay) == date(
        2026, 9, 30
    )
    assert last_complete_day(datetime(2026, 10, 1, 18, 25, tzinfo=IST), delay) == date(
        2026, 10, 1
    )


def test_completed_daily_drops_the_running_session():
    frame = daily([1, 2, 3])
    during = completed_daily(
        frame, datetime(2026, 10, 1, 15, 0, tzinfo=IST), timedelta(0)
    )
    after = completed_daily(
        frame, datetime(2026, 10, 1, 18, 30, tzinfo=IST), timedelta(0)
    )
    assert len(during) == 2
    assert len(after) == 3


def test_frame_feed_clock_includes_session_closes():
    feed = FrameFeed({"THYAO": intraday(periods=2)}, interval="5m")
    times = [t.strftime("%H:%M") for t in feed.times(include_closes=True)]
    assert times == ["10:05", "10:10", "18:10"]
    assert len(feed.times()) == 2


class TestProviderFeed:
    def test_bars(self, monkeypatch):
        calls = []

        def fake(symbol, interval, start, end):
            calls.append((symbol, interval, start, end))
            return intraday()

        monkeypatch.setattr(feed_module, "_fetch_bars", fake)
        feed = ProviderFeed("5m", timedelta(minutes=15))
        now = datetime(2026, 10, 1, 10, 40, tzinfo=IST)
        assert len(feed.bars("THYAO", None, now)) == 5
        since = datetime(2026, 9, 30, 18, 0, tzinfo=IST)
        feed.bars("THYAO", since, now)
        assert calls == [
            ("THYAO", "5m", date(2026, 10, 1), date(2026, 10, 1)),
            ("THYAO", "5m", date(2026, 9, 30), date(2026, 10, 1)),
        ]

    def test_no_bars_before_the_open(self, monkeypatch):
        def empty(*args, **kwargs):
            raise EmptyDataError()

        monkeypatch.setattr(feed_module, "load_ohlcv", empty)
        now = datetime(2026, 10, 1, 9, 0, tzinfo=IST)
        assert ProviderFeed().bars("THYAO", None, now) == []

    def test_daily_bars_are_fetched_once_per_day(self, monkeypatch):
        calls = []

        def fake(symbol, start, end, **kwargs):
            calls.append((symbol, end))
            return daily([1, 2, 3])

        monkeypatch.setattr(feed_module, "load_ohlcv", fake)
        feed = ProviderFeed()
        morning = datetime(2026, 10, 1, 11, 0, tzinfo=IST)
        for symbol in ("THYAO", "GARAN", "THYAO"):
            feed.daily(symbol, morning)
        feed.daily("THYAO", morning + timedelta(hours=1))
        assert calls == [("THYAO", date(2026, 9, 30)), ("GARAN", date(2026, 9, 30))]
        feed.daily("THYAO", morning.replace(hour=18, minute=30))
        assert calls[-1] == ("THYAO", date(2026, 10, 1))

    def test_rejects_unknown_intervals(self):
        with pytest.raises(ValueError):
            ProviderFeed("1W")

    def test_daily_bars_carry_dividends(self, monkeypatch):
        calls = []

        def fake(symbol, start, end, interval, adjustment, **kwargs):
            calls.append((interval, adjustment, kwargs))
            frame = daily([300.0, 310.0], end="2026-10-01")
            frame["Dividend"] = [0.0, 2.5]
            return frame

        monkeypatch.setattr(feed_module, "load_ohlcv", fake)
        now = datetime(2026, 10, 1, 18, 30, tzinfo=IST)
        first, second = ProviderFeed("1d").bars("THYAO", None, now)
        assert calls == [
            ("1d", "splits_only", {"cache": False, "include_actions": True})
        ]
        assert second.dividend == 2.5
        assert second.start == datetime(2026, 10, 1, 10, 0, tzinfo=IST)
        assert second.end == datetime(2026, 10, 1, 18, 10, tzinfo=IST)


def test_bar_bounds():
    stamp = pd.Timestamp("2026-10-01")
    assert bar_bounds(stamp, "1d") == (
        datetime(2026, 10, 1, 10, 0, tzinfo=IST),
        datetime(2026, 10, 1, 18, 10, tzinfo=IST),
    )
    start, end = bar_bounds(pd.Timestamp("2026-10-01 10:05"), "5m")
    assert (start.hour, start.minute, end.minute) == (10, 5, 10)


def test_daily_frame_feed_clock_and_signal_window():
    frames = {"THYAO": daily([1.0, 2.0, 3.0])}
    feed = FrameFeed(frames, {"THYAO": daily(range(1, 11))}, "1d", signal_bars=4)
    times = feed.times(include_closes=True)
    assert [t.strftime("%m-%d %H:%M") for t in times] == [
        "09-29 18:10",
        "09-30 18:10",
        "10-01 18:10",
    ]
    assert len(feed.times(start=times[1], end=times[1])) == 1
    signals = feed.daily("THYAO", datetime(2026, 10, 1, 12, 0, tzinfo=IST))
    assert len(signals) == 4
    assert signals.index[-1] == pd.Timestamp("2026-09-30")


class TestWantsLong:
    def test_follows_the_position(self):
        rising = daily(np.r_[np.linspace(100, 80, 40), np.linspace(80, 130, 40)])
        falling = daily(np.r_[np.linspace(80, 130, 40), np.linspace(130, 90, 40)])
        params = {"fast": 5, "slow": 20}
        assert wants_long("sma_cross", rising, params) is True
        assert wants_long("sma_cross", falling, params) is False

    def test_counts_an_order_placed_on_the_last_bar(self):
        closes = np.r_[np.linspace(100, 80, 60), np.linspace(80, 130, 60)]
        frame = daily(closes, end="2026-12-31")
        for n in range(25, len(frame)):
            window = frame.iloc[:n]
            stats = Backtest(window, SmaCross, cash=1e12).run(fast=5, slow=20)
            if stats["_strategy"].orders:
                assert stats["_strategy"].position.size == 0
                assert wants_long(SmaCross, window, {"fast": 5, "slow": 20}) is True
                return
        pytest.fail("No window ended on a signal bar.")
