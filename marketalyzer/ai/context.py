"""What a decision may know beyond its own bars: the daily picture and the market.

A view on hourly bars only covers the last days of the stock. This adds the
stock's daily trend and, for every interval, the market's state: XU100's
trend and the share of large caps above their 50-day average (breadth).
Everything is cut at the decision. An hourly decision on day D sees the
daily closes before D, and its own daily reading ends at the current price;
a daily decision sees D's close too.
"""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from marketalyzer import research
from marketalyzer.rotation import LARGE_CAPS
from marketalyzer.scripting import ta
from marketalyzer.services import load_bars, number

BREADTH_AVERAGE = 50
MIN_BREADTH = 5  # fewer stocks with an average: no breadth


def _days(index: Any) -> np.ndarray:
    """Return an index's dates as ``datetime64[D]``, without time zones."""
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize().to_numpy(dtype="datetime64[D]")


def _distance(price: float, level: float) -> float | None:
    if not math.isfinite(level) or not level:
        return None
    return number((price / level - 1) * 100, 2)


class Context:
    """Daily closes of the stocks and the index, read at decision times."""

    def __init__(
        self,
        daily: dict[str, pd.DataFrame],
        index: pd.DataFrame | None,
        *,
        intraday: bool,
    ):
        self.intraday = intraday
        self._closes = {
            code: (_days(frame.index), frame["Close"].to_numpy(dtype=float))
            for code, frame in daily.items()
            if frame is not None and len(frame)
        }
        self._index = None
        if index is not None and len(index):
            close = index["Close"].to_numpy(dtype=float)
            self._index = (
                _days(index.index),
                close,
                ta.sma(close, 50),
                ta.sma(close, 200),
            )
        self._breadth = self._breadth_series()

    def _breadth_series(self) -> tuple[np.ndarray, np.ndarray] | None:
        columns = []
        for days, close in self._closes.values():
            average = ta.sma(close, BREADTH_AVERAGE)
            above = np.where(np.isnan(average), np.nan, (close > average) * 1.0)
            columns.append(pd.Series(above, index=pd.to_datetime(days)))
        if not columns:
            return None
        # A stock's holiday keeps its last reading for a few days.
        frame = pd.concat(columns, axis=1).sort_index().ffill(limit=5)
        share = frame.mean(axis=1, skipna=True) * 100
        share[frame.notna().sum(axis=1) < MIN_BREADTH] = np.nan
        return _days(share.index), share.to_numpy(dtype=float)

    def _known(self, days: np.ndarray, when: Any) -> int:
        """Count the daily rows known at ``when``: before its day if intraday."""
        day = np.datetime64(pd.Timestamp(when).date(), "D")
        return int(
            np.searchsorted(days, day, side="left" if self.intraday else "right")
        )

    def market(self, when: Any) -> dict[str, Any] | None:
        """Describe XU100 and the breadth known at ``when``."""
        out: dict[str, Any] = {}
        if self._index is not None:
            days, close, sma50, sma200 = self._index
            n = self._known(days, when)
            if n > 20:
                last = close[n - 1]
                out["xu100_20_gun_%"] = number((last / close[n - 21] - 1) * 100, 2)
                out["xu100_sma50_uzaklik_%"] = _distance(last, sma50[n - 1])
                if math.isfinite(sma200[n - 1]):
                    out["xu100_sma200_ustunde"] = bool(last > sma200[n - 1])
        if self._breadth is not None:
            days, share = self._breadth
            n = self._known(days, when)
            if n and math.isfinite(share[n - 1]):
                out["genislik_sma50_ustu_%"] = number(share[n - 1], 1)
        return out or None

    def daily(self, code: str, when: Any, price: float) -> dict[str, Any] | None:
        """Describe the stock's daily trend for an intraday decision at ``price``.

        Daily views need nothing more and get None.
        """
        if not self.intraday or code not in self._closes:
            return None
        days, close = self._closes[code]
        n = self._known(days, when)
        if n < 21:
            return None
        closes = np.append(close[:n], price)

        def change(bars: int) -> float | None:
            if len(closes) <= bars or not closes[-1 - bars]:
                return None
            return number((price / closes[-1 - bars] - 1) * 100, 2)

        def average(bars: int) -> float:
            return float(closes[-bars:].mean()) if len(closes) >= bars else math.nan

        rsi = ta.rsi(closes[-300:], 14)[-1]
        return {
            "getiri_5_gun_%": change(5),
            "getiri_20_gun_%": change(20),
            "getiri_60_gun_%": change(60),
            "sma50_uzaklik_%": _distance(price, average(50)),
            "sma200_uzaklik_%": _distance(price, average(200)),
            "rsi": number(rsi, 1) if math.isfinite(rsi) else None,
        }


def load_context(codes: list[str], start: date, end: date, interval: str) -> Context:
    """Load daily bars of ``codes``, the large caps and XU100 from ``start`` to ``end``.

    A stock that fails to load is left out; the views then go without it.
    """
    universe = sorted(set(codes) | set(LARGE_CAPS))

    def one(code: str) -> tuple[str, pd.DataFrame | None]:
        try:
            return code, load_bars(code, start, end, "1d", warmup=True)[0]
        except Exception:  # A reference stock is optional.  # noqa: BLE001
            return code, None

    with ThreadPoolExecutor(max_workers=6) as pool:
        daily = dict(pool.map(one, [*universe, research.BENCHMARK]))
    index = daily.pop(research.BENCHMARK)
    return Context(daily, index, intraday=interval == "1h")
