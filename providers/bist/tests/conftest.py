"""Shared fixtures.

Payloads follow the Yahoo Finance chart API format, but every price in them is
synthetic test data, not a real market quote.
"""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from openbb_bist.utils import yahoo

IST = ZoneInfo("Europe/Istanbul")


def ts(year: int, month: int, day: int, hour: int = 10, minute: int = 0) -> int:
    """Return the epoch seconds of an Istanbul wall-clock time."""
    return int(datetime(year, month, day, hour, minute, tzinfo=IST).timestamp())


def make_chart(symbol, rows, *, adjclose=None, events=None, **meta):
    """Build a chart payload from (timestamp, open, high, low, close, volume) rows."""
    quote = {
        key: [row[index] for row in rows]
        for index, key in enumerate(["open", "high", "low", "close", "volume"], 1)
    }
    indicators = {"quote": [quote]}
    if adjclose is not None:
        indicators["adjclose"] = [{"adjclose": adjclose}]
    result = {
        "meta": {
            "symbol": symbol,
            "currency": "TRY",
            "exchangeName": "IST",
            "fullExchangeName": "Istanbul",
            "instrumentType": "EQUITY",
            "exchangeTimezoneName": "Europe/Istanbul",
            **meta,
        },
        "timestamp": [row[0] for row in rows],
        "indicators": indicators,
    }
    if events:
        result["events"] = events
    return {"chart": {"result": [result], "error": None}}


def not_found():
    """Return Yahoo's payload for an unknown ticker."""
    return {
        "chart": {
            "result": None,
            "error": {
                "code": "Not Found",
                "description": "No data found, symbol may be delisted",
            },
        }
    }


THYAO_DAILY = [
    (ts(2026, 9, 28), 300.0, 305.5, 298.25, 304.0, 21_000_000),
    (ts(2026, 9, 29), 304.0, 307.0, 301.0, 302.5, 18_500_000),
    (ts(2026, 9, 30), None, None, None, None, None),
    (ts(2026, 10, 1), 302.5, 310.0, 302.0, 309.75, 25_000_000),
    (ts(2026, 10, 2), 309.0, 311.0, 305.0, 306.0000305175781, 19_000_000),
]


@pytest.fixture
def fake_chart(monkeypatch):
    """Replace the network call with canned payloads keyed by Yahoo ticker.

    A value may be a payload or a callable taking the request params. Every call
    is recorded in ``calls`` as (ticker, params).
    """
    fake = SimpleNamespace(responses={}, calls=[])

    async def _get_chart(symbol, params):
        fake.calls.append((symbol, dict(params)))
        response = fake.responses.get(symbol, not_found())
        return response(params) if callable(response) else response

    monkeypatch.setattr(yahoo, "get_chart", _get_chart)
    return fake
