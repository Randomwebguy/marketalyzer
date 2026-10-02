"""Synthetic market data, for trying the interface without network access.

``enable()`` replaces the provider's Yahoo call with a deterministic random walk
per symbol, in Yahoo's chart format. Every price it produces is made up.
"""

import hashlib
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

IST = ZoneInfo("Europe/Istanbul")
FIRST_DAY = date(2018, 1, 1)
STEPS = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60}


def _seed(*parts: object) -> int:
    digest = hashlib.sha256("|".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _start_price(symbol: str) -> float:
    if symbol.startswith("XU"):
        return 1_000.0
    if symbol.endswith("=X"):
        return 5.0
    return 5.0 + _seed(symbol) % 60


@lru_cache(maxsize=64)
def _daily(symbol: str, today: date) -> tuple[list[date], np.ndarray, np.ndarray]:
    """Return trading days with their opens and closes up to ``today``."""
    days = []
    day = FIRST_DAY
    while day <= today:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    rng = np.random.default_rng(_seed(symbol))
    drift, vol = (0.0009, 0.004) if symbol.endswith("=X") else (0.0012, 0.012)
    returns = rng.normal(drift, vol * 1.6, len(days))
    closes = _start_price(symbol) * np.exp(np.cumsum(returns))
    gaps = rng.normal(0, vol / 2, len(days))
    opens = np.r_[closes[0], closes[:-1]] * np.exp(gaps)
    return days, opens, closes


def _tick(price: float) -> float:
    for floor, step in (
        (2500, 2.5),
        (1000, 1.0),
        (500, 0.5),
        (250, 0.25),
        (100, 0.1),
        (50, 0.05),
        (20, 0.02),
    ):
        if price >= floor:
            return round(round(price / step) * step, 2)
    return round(price, 2)


def _daily_rows(symbol: str, p1: int, p2: int) -> list[tuple]:
    days, opens, closes = _daily(symbol, datetime.now(IST).date())
    rows = []
    rng = np.random.default_rng(_seed(symbol, "wicks"))
    wicks = np.abs(rng.normal(0, 0.008, (len(days), 2)))
    for i, day in enumerate(days):
        stamp = int(datetime.combine(day, time(10), IST).timestamp())
        if not p1 <= stamp < p2:
            continue
        o, c = opens[i], closes[i]
        high = max(o, c) * (1 + wicks[i, 0])
        low = min(o, c) * (1 - wicks[i, 1])
        rows.append((stamp, _tick(o), _tick(high), _tick(low), _tick(c), 1_000_000 + i))
    return rows


def _intraday_rows(symbol: str, minutes: int, p1: int, p2: int) -> list[tuple]:
    days, opens, closes = _daily(symbol, datetime.now(IST).date())
    now = datetime.now(IST)
    rows = []
    count = (8 * 60) // minutes
    for i, day in enumerate(days):
        first = datetime.combine(day, time(10), IST)
        if int(first.timestamp()) >= p2 or first + timedelta(
            hours=8
        ) < datetime.fromtimestamp(p1, IST):
            continue
        rng = np.random.default_rng(_seed(symbol, day, minutes))
        steps = rng.normal(0, 0.002, count).cumsum()
        bridge = steps - np.linspace(0, steps[-1], count)
        path = opens[i] * np.exp(
            np.linspace(0, np.log(closes[i] / opens[i]), count) + bridge
        )
        previous = opens[i]
        for k, price in enumerate(path):
            start = first + timedelta(minutes=minutes * k)
            if start > now or not p1 <= int(start.timestamp()) < p2:
                previous = price
                continue
            high = max(previous, price) * 1.0008
            low = min(previous, price) * 0.9992
            rows.append(
                (
                    int(start.timestamp()),
                    _tick(previous),
                    _tick(high),
                    _tick(low),
                    _tick(price),
                    20_000,
                )
            )
            previous = price
    return rows


def chart_payload(symbol: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return a Yahoo chart payload of synthetic bars for ``params``."""
    now = datetime.now(IST)
    interval = params.get("interval", "1d")
    if "range" in params:
        p1 = int((now - timedelta(days=5)).timestamp())
        p2 = int(now.timestamp()) + 1
    else:
        p1, p2 = int(params["period1"]), int(params["period2"])
    code = symbol.removesuffix(".IS")
    if interval in STEPS:
        rows = _intraday_rows(code, STEPS[interval], p1, p2)
    else:
        rows = _daily_rows(code, p1, p2)
        if interval in ("1wk", "1mo"):
            rows = _resample(rows, interval)
    if "range" in params:
        rows = rows[-1:]
    if not rows:
        return {
            "chart": {
                "result": None,
                "error": {"code": "Not Found", "description": "No data found"},
            }
        }
    last = rows[-1]
    previous_close = _daily_rows(code, p1 - 7 * 86_400, last[0])[-1:] or [last]
    return {
        "chart": {
            "error": None,
            "result": [
                {
                    "meta": {
                        "symbol": symbol,
                        "currency": "TRY",
                        "exchangeName": "IST",
                        "fullExchangeName": "Istanbul (demo)",
                        "instrumentType": "INDEX"
                        if code.startswith("XU")
                        else "EQUITY",
                        "regularMarketPrice": last[4],
                        "regularMarketTime": last[0],
                        "previousClose": previous_close[0][4],
                        "shortName": f"{code} (demo)",
                    },
                    "timestamp": [row[0] for row in rows],
                    "indicators": {
                        "quote": [
                            {
                                "open": [row[1] for row in rows],
                                "high": [row[2] for row in rows],
                                "low": [row[3] for row in rows],
                                "close": [row[4] for row in rows],
                                "volume": [row[5] for row in rows],
                            }
                        ],
                        "adjclose": [{"adjclose": [row[4] for row in rows]}],
                    },
                }
            ],
        }
    }


def _resample(rows: list[tuple], interval: str) -> list[tuple]:
    groups: dict[tuple, list[tuple]] = {}
    for row in rows:
        day = datetime.fromtimestamp(row[0], IST).date()
        key = day.isocalendar()[:2] if interval == "1wk" else (day.year, day.month)
        groups.setdefault(key, []).append(row)
    return [
        (
            g[0][0],
            g[0][1],
            max(r[2] for r in g),
            min(r[3] for r in g),
            g[-1][4],
            sum(r[5] for r in g),
        )
        for g in groups.values()
    ]


def enable() -> None:
    """Serve synthetic data instead of calling Yahoo Finance."""
    from openbb_bist.utils import yahoo

    async def get_chart(symbol: str, params: dict[str, str]) -> dict[str, Any]:
        return chart_payload(symbol, params)

    yahoo.get_chart = get_chart
