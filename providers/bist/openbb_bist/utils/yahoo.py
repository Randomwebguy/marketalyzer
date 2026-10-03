"""Yahoo Finance chart API client for Borsa Istanbul data.

Every network call goes through ``get_chart``, so swapping the data source or
mocking it in tests only needs that one function replaced.
"""

import asyncio
import json
import threading
import time as clock
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import quote
from warnings import warn
from zoneinfo import ZoneInfo

from curl_cffi import requests as curl_requests
from openbb_core.app.model.abstract.error import OpenBBError
from openbb_core.provider.utils.errors import EmptyDataError

from openbb_bist.utils.constants import (
    CHART_HOSTS,
    CHART_URL,
    INTRADAY_DEFAULT_DAYS,
    INTRADAY_LOOKBACK_DAYS,
    INTRADAY_MAX_SPAN_DAYS,
    TIMEZONE,
    YAHOO_INTERVALS,
)
from openbb_bist.utils.symbols import to_bist_symbol

IST = ZoneInfo(TIMEZONE)
PRICE_DECIMALS = 4


# Yahoo answers HTTP 429 ("Too Many Requests") to clients that do not look like
# a browser, even on the first request, so requests impersonate Chrome. They are
# also limited in number, briefly cached and retried after a pause.
IMPERSONATE = "chrome"
MAX_CONCURRENT = 4
CACHE_SECONDS = 60.0
CACHE_SIZE = 512
RETRY_DELAYS = (1.0, 3.0)
COOLDOWN_SECONDS = 30.0
RATE_LIMITED = "Yahoo Finance rate limit reached (HTTP 429). Try again later."

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_lock = threading.Lock()
_local = threading.local()
_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
_blocked_until = 0.0


def _session() -> curl_requests.Session:
    """Return this thread's session, so connections and cookies are reused."""
    session = getattr(_local, "session", None)
    if session is None:
        session = _local.session = curl_requests.Session(impersonate=IMPERSONATE)
    return session


def _send(url: str, params: dict[str, str]) -> tuple[int, bytes, str | None]:
    """Make one GET request; return the status, body and Retry-After header."""
    with _slots:
        response = _session().get(url, params=params, timeout=15)
    return response.status_code, response.content, response.headers.get("retry-after")


def _wait_seconds(retry_after: str | None, default: float) -> float:
    try:
        return min(max(float(retry_after or default), 0.0), 10.0)
    except ValueError:
        return default


def clear_cache() -> None:
    """Forget cached payloads and any rate-limit pause."""
    global _blocked_until  # noqa: PLW0603
    with _lock:
        _cache.clear()
        _blocked_until = 0.0


def _cached(key: tuple) -> dict[str, Any] | None:
    with _lock:
        hit = _cache.get(key)
        if hit and clock.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        return None


def _remember(key: tuple, payload: dict[str, Any]) -> None:
    with _lock:
        if len(_cache) >= CACHE_SIZE:
            del _cache[min(_cache, key=lambda k: _cache[k][0])]
        _cache[key] = (clock.monotonic(), payload)


def _fetch_chart(symbol: str, params: dict[str, str]) -> dict[str, Any]:
    """Request a chart payload, retrying on a rate limit; blocking."""
    global _blocked_until  # noqa: PLW0603
    if clock.monotonic() < _blocked_until:
        raise OpenBBError(RATE_LIMITED)
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        host = CHART_HOSTS[attempt % len(CHART_HOSTS)]
        url = CHART_URL.format(host=host, symbol=quote(symbol))
        try:
            status, body, retry_after = _send(url, params)
        except curl_requests.RequestsError as error:
            raise OpenBBError(
                f"Could not reach Yahoo Finance for {symbol}: {error}"
            ) from error
        if status != 429:
            try:
                return json.loads(body)
            except ValueError as error:
                raise OpenBBError(
                    f"Unexpected response from Yahoo Finance for {symbol}"
                    f" (HTTP {status})."
                ) from error
        if attempt < len(RETRY_DELAYS):
            clock.sleep(_wait_seconds(retry_after, RETRY_DELAYS[attempt]))
    # Stop asking for a while: more requests would only extend the block.
    _blocked_until = clock.monotonic() + COOLDOWN_SECONDS
    raise OpenBBError(RATE_LIMITED)


async def get_chart(symbol: str, params: dict[str, str]) -> dict[str, Any]:
    """Request one chart payload for a Yahoo ticker."""
    key = (symbol, tuple(sorted(params.items())))
    payload = _cached(key)
    if payload is None:
        payload = await asyncio.to_thread(_fetch_chart, symbol, params)
        if not (payload.get("chart") or {}).get("error"):
            _remember(key, payload)
    return payload


def chart_result(payload: dict[str, Any], label: str) -> dict[str, Any]:
    """Return the single chart result in a payload, or raise its error."""
    chart = (payload or {}).get("chart") or {}
    results = chart.get("result") or []
    error = chart.get("error")
    if error or not results:
        description = (error or {}).get("description") or "No data returned."
        raise EmptyDataError(f"{label}: {description}")
    return results[0]


def resolve_window(
    interval: str,
    start_date: date | None,
    end_date: date | None,
    today: date | None = None,
) -> tuple[date, date]:
    """Fill in default dates and clamp intraday ranges to what Yahoo serves."""
    today = today or datetime.now(IST).date()
    end = end_date or today
    lookback = INTRADAY_LOOKBACK_DAYS.get(interval)
    if start_date is None:
        start = end - timedelta(days=INTRADAY_DEFAULT_DAYS if lookback else 365)
    else:
        start = start_date
    if start > end:
        raise OpenBBError(f"start_date ({start}) must not be after end_date ({end}).")
    if lookback:
        earliest = today - timedelta(days=lookback)
        if end < earliest:
            raise OpenBBError(
                f"Yahoo Finance only serves {interval} bars for the last"
                f" {lookback} days; end_date {end} is too old."
            )
        if start < earliest:
            warn(
                f"Yahoo Finance only serves {interval} bars for the last {lookback}"
                f" days; start_date moved from {start} to {earliest}."
            )
            start = earliest
    return start, end


def split_window(start: date, end: date, interval: str) -> list[tuple[date, date]]:
    """Split an inclusive date range into the chunks Yahoo accepts per request."""
    span = INTRADAY_MAX_SPAN_DAYS.get(interval)
    if not span:
        return [(start, end)]
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=span - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def chart_params(interval: str, start: date, end: date) -> dict[str, str]:
    """Build chart query parameters for an inclusive Istanbul date range."""
    period1 = datetime.combine(start, time(), IST)
    period2 = datetime.combine(end + timedelta(days=1), time(), IST)
    return {
        "period1": str(int(period1.timestamp())),
        "period2": str(int(period2.timestamp())),
        "interval": YAHOO_INTERVALS[interval],
        "includePrePost": "false",
        "includeAdjustedClose": "true",
        "events": "div,splits",
    }


def _at(values: list | None, index: int) -> Any:
    if values is None or index >= len(values):
        return None
    return values[index]


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), PRICE_DECIMALS)


def _event_date(timestamp: int) -> date:
    return datetime.fromtimestamp(timestamp, IST).date()


def _day(value: date | datetime) -> date:
    """Return the Istanbul trading date of a daily key or an intraday time."""
    return value.date() if isinstance(value, datetime) else value


def chart_records(
    result: dict[str, Any],
    interval: str,
    adjustment: str = "splits_only",
    include_actions: bool = False,
) -> list[dict[str, Any]]:
    """Convert a chart result into OHLCV rows, oldest first.

    Daily and longer bars are keyed by their Istanbul trading date; intraday bars
    keep a timezone-aware Istanbul timestamp. Rows with a missing price are
    dropped. Yahoo's prices are already split-adjusted; ``splits_and_dividends``
    scales each bar by Yahoo's adjusted close.
    """
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    bars = (indicators.get("quote") or [{}])[0]
    adjclose = ((indicators.get("adjclose") or [{}])[0]).get("adjclose")
    intraday = interval in INTRADAY_LOOKBACK_DAYS

    rows: dict[Any, dict[str, Any]] = {}
    for i, timestamp in enumerate(timestamps):
        prices = [_at(bars.get(key), i) for key in ("open", "high", "low", "close")]
        if any(price is None for price in prices):
            continue
        moment = datetime.fromtimestamp(timestamp, IST)
        key = moment if intraday else moment.date()
        factor = 1.0
        if adjustment == "splits_and_dividends":
            adjusted = _at(adjclose, i)
            if adjusted is not None and prices[3]:
                factor = adjusted / prices[3]
        volume = _at(bars.get("volume"), i)
        open_, high, low, close = (_round(price * factor) for price in prices)
        # Yahoo can send a second bar for the running session; the last one wins.
        rows[key] = {
            "date": key,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": None if volume is None else int(volume),
        }

    records = sorted(rows.values(), key=lambda row: row["date"])
    if include_actions:
        _attach_actions(records, result.get("events") or {})
    return records


def _attach_actions(records: list[dict[str, Any]], events: dict[str, Any]) -> None:
    """Add dividend and split columns, assigning each event to its bar."""
    from bisect import bisect_right

    dates = [row["date"] for row in records]
    for row in records:
        row["dividend"] = 0.0
        row["split_ratio"] = None

    for event in (events.get("dividends") or {}).values():
        index = bisect_right(dates, _event_date(event["date"])) - 1
        if index >= 0 and event.get("amount") is not None:
            records[index]["dividend"] += _round(event["amount"]) or 0.0

    for event in (events.get("splits") or {}).values():
        index = bisect_right(dates, _event_date(event["date"])) - 1
        if index >= 0 and event.get("denominator"):
            records[index]["split_ratio"] = event["numerator"] / event["denominator"]


async def fetch_history(
    symbols: list[str],
    interval: str,
    start_date: date | None,
    end_date: date | None,
    adjustment: str = "splits_only",
    include_actions: bool = False,
    label: Callable[[str], str] = to_bist_symbol,
) -> list[dict[str, Any]]:
    """Fetch OHLCV rows for one or more Yahoo tickers.

    A ``symbol`` column is added when more than one ticker is requested. A ticker
    that fails is reported as a warning unless it is the only one requested.
    """
    start, end = resolve_window(interval, start_date, end_date)
    windows = split_window(start, end, interval)
    if include_actions and interval in INTRADAY_LOOKBACK_DAYS:
        warn("Dividends and splits are only attached to daily or longer bars.")
        include_actions = False

    async def _one(symbol: str) -> list[dict[str, Any]]:
        payloads = await asyncio.gather(
            *[get_chart(symbol, chart_params(interval, s, e)) for s, e in windows],
            return_exceptions=True,
        )
        records: list[dict[str, Any]] = []
        for payload in payloads:
            if isinstance(payload, BaseException):
                raise payload
            try:
                result = chart_result(payload, label(symbol))
            except EmptyDataError:
                # A chunk with no sessions (a holiday week) is not an error.
                if len(windows) == 1:
                    raise
                continue
            records.extend(chart_records(result, interval, adjustment, include_actions))
        # Yahoo appends the latest bar even when period2 is long past.
        records = [row for row in records if _day(row["date"]) <= end]
        if not records:
            raise EmptyDataError(
                f"{label(symbol)}: no price data in the requested range."
            )
        return records

    results = await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)
    data: list[dict[str, Any]] = []
    errors: list[Exception] = []
    for symbol, result in zip(symbols, results):
        if isinstance(result, Exception):
            if len(symbols) == 1:
                raise result
            warn(str(result))
            errors.append(result)
            continue
        if isinstance(result, BaseException):
            raise result
        if len(symbols) > 1:
            for row in result:
                row["symbol"] = label(symbol)
        data.extend(result)

    if not data:
        raise errors[0] if errors else EmptyDataError()
    data.sort(key=lambda row: (row["date"], row.get("symbol") or ""))
    return data


def quote_record(result: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Build a quote row from the metadata of a one-day chart."""
    meta = result.get("meta") or {}
    bars = ((result.get("indicators") or {}).get("quote") or [{}])[0]

    def _last(key: str) -> Any:
        values = [value for value in (bars.get(key) or []) if value is not None]
        return values[-1] if values else None

    price = meta.get("regularMarketPrice")
    prev_close = meta.get("previousClose") or meta.get("chartPreviousClose")
    change = price - prev_close if price is not None and prev_close else None
    timestamp = meta.get("regularMarketTime")
    volume = meta.get("regularMarketVolume") or _last("volume")
    return {
        "symbol": to_bist_symbol(meta.get("symbol") or symbol),
        "asset_type": meta.get("instrumentType"),
        "name": meta.get("longName") or meta.get("shortName"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
        "currency": meta.get("currency"),
        "last_price": _round(price),
        "last_timestamp": datetime.fromtimestamp(timestamp, IST) if timestamp else None,
        "open": _round(_last("open")),
        "high": _round(meta.get("regularMarketDayHigh") or _last("high")),
        "low": _round(meta.get("regularMarketDayLow") or _last("low")),
        "volume": None if volume is None else int(volume),
        "prev_close": _round(prev_close),
        "change": _round(change),
        "change_percent": None if change is None else round(change / prev_close, 6),
        "year_high": _round(meta.get("fiftyTwoWeekHigh")),
        "year_low": _round(meta.get("fiftyTwoWeekLow")),
    }


async def fetch_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    """Fetch the latest quote for one or more Yahoo tickers."""

    async def _one(symbol: str) -> dict[str, Any]:
        payload = await get_chart(symbol, {"range": "1d", "interval": "1d"})
        return quote_record(chart_result(payload, to_bist_symbol(symbol)), symbol)

    results = await asyncio.gather(*[_one(s) for s in symbols], return_exceptions=True)
    data: list[dict[str, Any]] = []
    errors: list[Exception] = []
    for result in results:
        if isinstance(result, Exception):
            if len(symbols) == 1:
                raise result
            warn(str(result))
            errors.append(result)
            continue
        if isinstance(result, BaseException):
            raise result
        data.append(result)

    if not data:
        raise errors[0] if errors else EmptyDataError()
    return data
