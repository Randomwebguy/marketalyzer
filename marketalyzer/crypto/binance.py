"""Binance spot daily candles, delisted coins included, cached on disk.

The public archive (data.binance.vision) keeps one zip per symbol and month,
also for pairs that were delisted long ago, so a backtest can see the coins
that existed at the time and not only today's survivors. Months not archived
yet come from the public REST API. No key is needed.

Days are UTC. Only finished days are kept.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

from marketalyzer.paper.cli import default_home

ARCHIVE = "https://data.binance.vision"
LISTING = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
REST = "https://data-api.binance.vision/api/v3/klines"
COLUMNS = ["open_time", "Open", "High", "Low", "Close", "Volume", "close_time",
           "QuoteVolume", "Trades", "TakerBase", "TakerQuote", "Ignore"]  # fmt: skip
KEEP = ["Open", "High", "Low", "Close", "Volume", "QuoteVolume"]
TIMEOUT = 30


def cache_dir(interval: str = "1d") -> Path:
    """Return where the candles of ``interval`` are kept."""
    return default_home() / "binance" / f"spot-{interval}"


def _get(url: str, **params) -> requests.Response:
    response = requests.get(url, params=params or None, timeout=TIMEOUT)
    response.raise_for_status()
    return response


def _listing(prefix: str, delimiter: str | None = "/") -> list[str]:
    """Return the archive's sub-prefixes (with a delimiter) or keys under ``prefix``."""
    found, marker = [], ""
    tag = "Prefix" if delimiter else "Key"
    while True:
        params = {"prefix": prefix, "marker": marker}
        if delimiter:
            params["delimiter"] = delimiter
        text = _get(LISTING, **params).text
        items = re.findall(rf"<{tag}>([^<]+)</{tag}>", text)
        found += [i for i in items if i != prefix]
        if "<IsTruncated>true</IsTruncated>" not in text:
            return found
        marker = (re.findall(r"<NextMarker>([^<]+)</NextMarker>", text) or [items[-1]])[
            0
        ]


def usdt_symbols() -> list[str]:
    """Return every USDT spot pair the archive has daily candles for."""
    prefixes = _listing("data/spot/monthly/klines/")
    symbols = [p.rstrip("/").rsplit("/", 1)[-1] for p in prefixes]
    return sorted(s for s in symbols if s.endswith("USDT"))


def archived_months(symbol: str, interval: str = "1d") -> list[str]:
    """Return the months ("2021-01") archived for ``symbol``."""
    keys = _listing(f"data/spot/monthly/klines/{symbol}/{interval}/", delimiter=None)
    found = re.findall(rf"{symbol}-{interval}-(\d{{4}}-\d{{2}})\.zip", " ".join(keys))
    return sorted(set(found))


def _frame(rows: Iterable[list], daily: bool = True) -> pd.DataFrame:
    frame = pd.DataFrame(list(rows), columns=COLUMNS)
    if frame.empty:
        return pd.DataFrame(columns=KEEP, index=pd.DatetimeIndex([], name="date"))
    stamps = frame["open_time"].astype("int64")
    unit = stamps.map(lambda v: "us" if v > 10**14 else "ms")
    days = [pd.Timestamp(int(v), unit=u) for v, u in zip(stamps, unit, strict=True)]
    if daily:
        days = [d.normalize() for d in days]
    out = frame[KEEP].astype(float)
    out.index = pd.DatetimeIndex(days, name="date")
    return out


def month_candles(symbol: str, month: str, interval: str = "1d") -> pd.DataFrame:
    """Return one archived month of candles."""
    name = f"{symbol}-{interval}-{month}.zip"
    url = f"{ARCHIVE}/data/spot/monthly/klines/{symbol}/{interval}/{name}"
    with zipfile.ZipFile(io.BytesIO(_get(url).content)) as archive:
        text = archive.read(archive.namelist()[0]).decode()
    rows = [line.split(",") for line in text.splitlines() if line and line[0].isdigit()]
    return _frame(rows, daily=interval == "1d")


def recent_candles(
    symbol: str, start: date, today: date, interval: str = "1d"
) -> pd.DataFrame:
    """Return the bars from ``start`` that began before ``today`` (REST API)."""
    rows: list[list] = []
    since = int(
        datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp()
        * 1000
    )
    while True:
        batch = _get(
            REST, symbol=symbol, interval=interval, startTime=since, limit=1000
        ).json()
        rows += batch
        if len(batch) < 1000:
            break
        since = int(batch[-1][0]) + 1
    frame = _frame(rows, daily=interval == "1d")
    return frame[frame.index < pd.Timestamp(today)]


def history(
    symbol: str, today: date | None = None, refresh: bool = True, interval: str = "1d"
) -> pd.DataFrame:
    """Return ``symbol``'s candles, fetching what the cache lacks.

    Archived months are fetched once; days after the last archived month come
    from the REST API and are replaced when their month is archived.
    """
    today = today or datetime.now(timezone.utc).date()
    folder = cache_dir(interval)
    folder.mkdir(parents=True, exist_ok=True)
    data_file, meta_file = folder / f"{symbol}.csv", folder / f"{symbol}.json"
    meta = json.loads(meta_file.read_text()) if meta_file.exists() else {}
    cached = (pd.read_csv(data_file, index_col="date", parse_dates=["date"])
              if data_file.exists() else _frame([]))  # fmt: skip
    if not refresh:
        return cached
    have = set(meta.get("archived", []))
    months = archived_months(symbol, interval)
    parts = [cached] + [
        month_candles(symbol, m, interval) for m in months if m not in have
    ]
    have |= set(months)
    last_month = max(have) if have else None
    recent = meta.get("recent_until")
    if last_month and (
        recent is None or recent < (today - timedelta(days=1)).isoformat()
    ):
        first = (pd.Timestamp(last_month + "-01") + pd.offsets.MonthBegin(1)).date()
        try:
            parts.append(recent_candles(symbol, first, today, interval))
            meta["recent_until"] = (today - timedelta(days=1)).isoformat()
        except requests.HTTPError:  # delisted: the archive is all there is
            meta["recent_until"] = None
    frame = (
        pd.concat([p for p in parts if not p.empty])
        if any(not p.empty for p in parts)
        else _frame([])
    )
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame[frame.index < pd.Timestamp(today)]
    frame.to_csv(data_file, index_label="date")
    meta["archived"] = sorted(have)
    meta_file.write_text(json.dumps(meta))
    return frame


FUTURES = "https://fapi.binance.com/fapi/v1"


def futures_bars(
    symbol: str, interval: str, start: datetime | None = None, limit: int = 1000,
    now: datetime | None = None,
) -> pd.DataFrame:  # fmt: skip
    """Return a USDT perpetual's finished bars (index = bar start, UTC, naive)."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start is not None:
        params["startTime"] = int(start.timestamp() * 1000)
    rows = _get(f"{FUTURES}/klines", **params).json()
    if not rows:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    frame = pd.DataFrame([r[:6] for r in rows],
                         columns=["open_time", "Open", "High", "Low", "Close", "Volume"])  # fmt: skip
    ends = pd.to_datetime([int(r[6]) + 1 for r in rows], unit="ms")
    frame.index = pd.to_datetime(frame.pop("open_time").astype("int64"), unit="ms")
    frame = frame.astype(float)
    cutoff = pd.Timestamp((now or datetime.now(timezone.utc)).replace(tzinfo=None))
    return frame[ends <= cutoff]


def futures_active(count: int = 10, exclude: set[str] | None = None) -> list[str]:
    """Return the USDT perpetuals with the most 24-hour volume."""
    rows = _get(f"{FUTURES}/ticker/24hr").json()
    exclude = exclude or set()
    ranked = sorted(
        (r for r in rows
         if r["symbol"].endswith("USDT") and "_" not in r["symbol"]
         and r["symbol"].removesuffix("USDT") not in exclude),
        key=lambda r: float(r["quoteVolume"]), reverse=True,
    )  # fmt: skip
    return [r["symbol"] for r in ranked[:count]]


def futures_prices() -> dict[str, float]:
    """Return every USDT perpetual's last price."""
    return {
        r["symbol"]: float(r["price"]) for r in _get(f"{FUTURES}/ticker/price").json()
    }


def futures_funding_rate(symbol: str) -> float:
    """Return the perpetual's latest funding rate (per eight hours)."""
    return float(
        _get(f"{FUTURES}/premiumIndex", symbol=symbol).json()["lastFundingRate"]
    )


FUNDING = "https://fapi.binance.com/fapi/v1/fundingRate"


def funding(symbol: str, today: date | None = None) -> pd.Series:
    """Return the perpetual's funding paid by longs per UTC day (sum of the day's rates).

    Cached; an empty series when the pair never had a perpetual.
    """
    today = today or datetime.now(timezone.utc).date()
    folder = default_home() / "binance" / "funding"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbol}.csv"
    rows = (
        pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["time", "rate"])
    )
    since = (
        int(rows["time"].max()) + 1 if len(rows) else 1_567_296_000_000
    )  # 2019-09-01
    fresh = []
    while True:
        try:
            batch = _get(FUNDING, symbol=symbol, startTime=since, limit=1000).json()
        except requests.HTTPError:
            break
        fresh += [(int(r["fundingTime"]), float(r["fundingRate"])) for r in batch]
        if len(batch) < 1000:
            break
        since = int(batch[-1]["fundingTime"]) + 1
    if fresh:
        rows = pd.concat([rows, pd.DataFrame(fresh, columns=["time", "rate"])])
        rows = rows.drop_duplicates("time").sort_values("time")
        rows.to_csv(path, index=False)
    if rows.empty:
        return pd.Series(dtype=float)
    days = pd.to_datetime(rows["time"].astype("int64"), unit="ms").dt.normalize()
    daily = rows["rate"].astype(float).groupby(days.to_numpy()).sum()
    return daily[daily.index < pd.Timestamp(today)]


def download(
    symbols: list[str],
    today: date | None = None,
    workers: int = 12,
    interval: str = "1d",
) -> dict[str, pd.DataFrame]:
    """Fetch many symbols in parallel; symbols that fail are left out."""

    def one(symbol):
        try:
            return symbol, history(symbol, today, interval=interval)
        except (requests.RequestException, zipfile.BadZipFile, ValueError):
            return symbol, None

    with ThreadPoolExecutor(workers) as pool:
        return {s: f for s, f in pool.map(one, symbols) if f is not None and len(f)}
