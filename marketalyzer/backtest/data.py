"""Load BIST price data from the openbb-bist provider as backtesting.py frames."""

import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Europe/Istanbul")
COLUMNS = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def default_cache_dir() -> Path:
    """Return MARKETALYZER_CACHE_DIR, or ~/.cache/marketalyzer."""
    configured = os.environ.get("MARKETALYZER_CACHE_DIR")
    return Path(configured) if configured else Path.home() / ".cache" / "marketalyzer"


def _date_index(values: Any) -> pd.DatetimeIndex:
    """Build the index with one resolution, so cached and fresh frames match."""
    return pd.DatetimeIndex(values, name="Date").as_unit("ns")


def to_frame(rows: list[Any]) -> pd.DataFrame:
    """Convert provider rows into an OHLCV frame with backtesting.py's columns.

    Intraday timestamps become naive Istanbul wall-clock times. A ``dividend``
    field, present when actions were requested, becomes a ``Dividend`` column.
    """
    records = [row.model_dump() if hasattr(row, "model_dump") else row for row in rows]
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        raise ValueError("No price rows to convert.")
    index = pd.to_datetime(frame["date"])
    if index.dt.tz is not None:
        index = index.dt.tz_convert(IST).dt.tz_localize(None)
    frame = frame.set_index(_date_index(index))
    columns = dict(COLUMNS)
    if "dividend" in frame:
        columns["dividend"] = "Dividend"
    frame = frame[list(columns)].rename(columns=columns)
    frame["Volume"] = frame["Volume"].fillna(0)
    if "Dividend" in frame:
        frame["Dividend"] = frame["Dividend"].fillna(0)
    frame = frame.dropna().astype(float).sort_index()
    return frame[~frame.index.duplicated(keep="last")]


def _as_date(value: date | str | None) -> date | None:
    if value is None or isinstance(value, date):
        return value.date() if isinstance(value, datetime) else value
    return date.fromisoformat(value)


def _fetch(kind: str, params: dict[str, Any]) -> list[Any]:
    """Fetch rows through an openbb-bist fetcher. Tests replace this function."""
    from openbb_core.provider.utils.helpers import run_async

    if kind == "currency":
        from openbb_bist.models.currency_historical import (
            BistCurrencyHistoricalFetcher as Fetcher,
        )
    else:
        from openbb_bist.models.equity_historical import (
            BistEquityHistoricalFetcher as Fetcher,
        )
    return run_async(Fetcher.fetch_data, params)


def _load(
    kind: str,
    params: dict[str, Any],
    start: date | None,
    end: date | None,
    cache: bool,
    cache_dir: Path | None,
) -> pd.DataFrame:
    if start:
        params["start_date"] = start
    if end:
        params["end_date"] = end
    # Only closed date ranges are cached: a range that reaches today still changes.
    path = None
    if cache and start and end and end < datetime.now(IST).date():
        name = "_".join(str(value) for value in (kind, *params.values()))
        name = re.sub(r"[^A-Za-z0-9._-]+", "-", name)
        path = (cache_dir or default_cache_dir()) / f"{name}.csv"
        if path.exists():
            cached = pd.read_csv(path, index_col="Date", parse_dates=["Date"])
            cached.index = _date_index(cached.index)
            return cached
    frame = to_frame(_fetch(kind, params))
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path)
    return frame


def load_ohlcv(
    symbol: str,
    start: date | str | None = None,
    end: date | str | None = None,
    interval: str = "1d",
    adjustment: str = "splits_and_dividends",
    cache: bool = True,
    cache_dir: Path | None = None,
    include_actions: bool = False,
) -> pd.DataFrame:
    """Load OHLCV bars for a BIST stock or index (THYAO, XU100, ...).

    Prices are adjusted for splits, bonus issues and, by default, dividends, so a
    backtest sees total returns. With ``adjustment="splits_only"`` and
    ``include_actions``, the cash dividend per share is added as a ``Dividend``
    column instead, for simulations that pay dividends out. Closed date ranges
    are cached on disk.
    """
    params: dict[str, Any] = {
        "symbol": symbol.upper(),
        "interval": interval,
        "adjustment": adjustment,
    }
    if include_actions:
        params["include_actions"] = True
    return _load("equity", params, _as_date(start), _as_date(end), cache, cache_dir)


def load_fx(
    pair: str = "USDTRY",
    start: date | str | None = None,
    end: date | str | None = None,
    cache: bool = True,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """Load daily bars for a currency pair such as USDTRY."""
    params = {"symbol": pair.upper(), "interval": "1d"}
    return _load("currency", params, _as_date(start), _as_date(end), cache, cache_dir)


def suspicious_jumps(frame: pd.DataFrame, threshold: float = 0.2) -> pd.Series:
    """Return close-to-close moves larger than ``threshold`` (0.2 = 20%).

    BIST's daily price limit is ±10% for most stocks, so on daily or intraday bars
    a larger jump usually means a split or bonus issue that was not adjusted.
    """
    change = frame["Close"].pct_change()
    return change[change.abs() > threshold]
