"""Market data feeds that hand completed bars to the paper trader."""

from datetime import date, datetime, timedelta
from typing import Any, Protocol

import pandas as pd

from marketalyzer.backtest.data import load_ohlcv
from marketalyzer.paper.account import SESSION_CLOSE, SESSION_OPEN
from marketalyzer.paper.models import IST, Bar, as_istanbul

INTERVALS = {
    "1m": timedelta(minutes=1),
    "2m": timedelta(minutes=2),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
}
# Yahoo Finance delays BIST prices by about 15 minutes.
DEFAULT_DELAY = timedelta(minutes=15)
# Daily bars handed to a strategy; enough to warm up its slowest indicator.
SIGNAL_BARS = 400


class Feed(Protocol):
    """Where the paper trader gets prices from."""

    def bars(self, symbol: str, since: datetime | None, now: datetime) -> list[Bar]:
        """Return completed bars that end after ``since``, oldest first."""
        ...

    def daily(self, symbol: str, now: datetime) -> pd.DataFrame:
        """Return completed daily OHLCV bars for strategy signals."""
        ...


def bar_bounds(stamp: Any, interval: str) -> tuple[datetime, datetime]:
    """Return the Istanbul start and end of the bar labelled ``stamp``.

    A daily bar runs from the opening of continuous trading to the end of the
    closing session, so an order placed in the evening fills at the next open.
    """
    moment = as_istanbul(pd.Timestamp(stamp).to_pydatetime())
    if interval == "1d":
        day = moment.date()
        return (
            datetime.combine(day, SESSION_OPEN, IST),
            datetime.combine(day, SESSION_CLOSE, IST),
        )
    return moment, moment + INTERVALS[interval]


def completed_bars(
    frame: pd.DataFrame,
    interval: str,
    since: datetime | None,
    now: datetime,
    delay: timedelta,
) -> list[Bar]:
    """Turn an OHLCV frame into bars that ended after ``since`` and before now.

    A bar counts as complete once its end is older than ``now - delay``: the
    latest bar a delayed feed returns is usually still forming.
    """
    cutoff = as_istanbul(now) - delay
    bars = []
    for stamp, row in frame.iterrows():
        start, end = bar_bounds(stamp, interval)
        if end > cutoff or (since is not None and end <= as_istanbul(since)):
            continue
        bars.append(
            Bar(
                start=start,
                end=end,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0.0)),
                dividend=float(row.get("Dividend", 0.0)),
            )
        )
    return bars


def last_complete_day(now: datetime, delay: timedelta) -> date:
    """Return the latest date whose daily bar is final at ``now``."""
    now = as_istanbul(now)
    closed = datetime.combine(now.date(), SESSION_CLOSE, IST) + delay
    return now.date() if now >= closed else now.date() - timedelta(days=1)


def completed_daily(
    frame: pd.DataFrame, now: datetime, delay: timedelta
) -> pd.DataFrame:
    """Drop today's daily bar while the session is still running."""
    last = pd.Timestamp(last_complete_day(now, delay))
    return frame[frame.index <= last]


class ProviderFeed:
    """Live bars from the openbb-bist provider (Yahoo Finance, delayed).

    With ``interval="1d"`` the trader works end of day: run it once after the
    close and orders fill at the next session's open.
    """

    def __init__(
        self,
        interval: str = "5m",
        delay: timedelta = DEFAULT_DELAY,
        lookback_days: int = 600,
    ):
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}.")
        self.interval = interval
        self.delay = delay
        self.lookback_days = lookback_days
        self._daily: dict[tuple[str, date], pd.DataFrame] = {}

    def bars(self, symbol: str, since: datetime | None, now: datetime) -> list[Bar]:
        """Fetch today's (or since ``since``) bars and keep the completed ones."""
        now = as_istanbul(now)
        start = as_istanbul(since).date() if since else now.date()
        frame = _fetch_bars(symbol, self.interval, start, now.date())
        return completed_bars(frame, self.interval, since, now, self.delay)

    def daily(self, symbol: str, now: datetime) -> pd.DataFrame:
        """Fetch daily bars once per completed trading day."""
        last = last_complete_day(now, self.delay)
        key = (symbol, last)
        if key not in self._daily:
            # Drop the previous days' frames; they are superseded by this one.
            self._daily = {k: v for k, v in self._daily.items() if k[1] == last}
            start = last - timedelta(days=self.lookback_days)
            frame = load_ohlcv(symbol, start, last, cache=False)
            self._daily[key] = completed_daily(frame, now, self.delay)
        return self._daily[key]


def _fetch_bars(symbol: str, interval: str, start: date, end: date) -> pd.DataFrame:
    """Load split-adjusted bars for order matching.

    Dividends are not folded into these prices; daily bars carry them in a
    ``Dividend`` column instead, so the account can pay them out. Returns an
    empty frame when the market has not traded yet.
    """
    from openbb_core.provider.utils.errors import EmptyDataError

    try:
        return load_ohlcv(
            symbol,
            start,
            end,
            interval,
            "splits_only",
            cache=False,
            include_actions=interval == "1d",
        )
    except EmptyDataError:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


class FrameFeed:
    """Bars served from OHLCV frames, for replays and tests.

    ``frames`` are the bars orders are matched against, at ``interval``; with
    daily bars they can carry a ``Dividend`` column. ``daily`` are the bars
    strategies see, normally dividend-adjusted as in a backtest. Frames use the
    index produced by ``load_ohlcv``: naive Istanbul times.
    """

    def __init__(
        self,
        frames: dict[str, pd.DataFrame],
        daily: dict[str, pd.DataFrame] | None = None,
        interval: str = "5m",
        delay: timedelta = timedelta(0),
        signal_bars: int = SIGNAL_BARS,
    ):
        if interval not in INTERVALS:
            raise ValueError(f"interval must be one of {', '.join(INTERVALS)}.")
        self.frames = frames
        self.daily_frames = daily or {}
        self.interval = interval
        self.delay = delay
        self.signal_bars = signal_bars

    def bars(self, symbol: str, since: datetime | None, now: datetime) -> list[Bar]:
        """Return the frame's completed bars."""
        return completed_bars(
            self.frames[symbol], self.interval, since, now, self.delay
        )

    def daily(self, symbol: str, now: datetime) -> pd.DataFrame:
        """Return the last ``signal_bars`` daily bars known at ``now``."""
        frame = completed_daily(self.daily_frames[symbol], now, self.delay)
        return frame.iloc[-self.signal_bars :]

    def times(
        self,
        include_closes: bool = False,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[datetime]:
        """Every bar end across all symbols, in order: the clock of a replay.

        With ``include_closes``, each trading day's session close (plus the feed
        delay) is added too: the moment that day's daily bar becomes final.
        ``start`` and ``end`` limit the clock to that span, inclusive.
        """
        moments: set[Any] = set()
        for frame in self.frames.values():
            bounds = [bar_bounds(stamp, self.interval) for stamp in frame.index]
            moments.update(end for _, end in bounds)
            if include_closes:
                moments.update(
                    datetime.combine(day, SESSION_CLOSE, IST) + self.delay
                    for day in {begin.date() for begin, _ in bounds}
                )
        return sorted(
            moment
            for moment in moments
            if (start is None or moment >= as_istanbul(start))
            and (end is None or moment <= as_istanbul(end))
        )
