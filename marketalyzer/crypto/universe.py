"""The coins a strategy could have traded at the time: no survivorship bias.

At each month start the coins are ranked by their median daily USDT volume
over the previous 30 days; those trading for at least a year with a median of
at least 2 M $ are eligible and the top ``size`` form that month's universe.
Stablecoins, wrapped coins, fiat pairs and leveraged tokens are left out. A
symbol whose candles stop for more than a week is split into separate coins
(the old LUNA and LUNA 2.0 share one ticker).
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

QUOTE = "USDT"
STABLE = {
    "USDC", "BUSD", "TUSD", "USDP", "PAX", "DAI", "FDUSD", "UST", "USTC", "USDS", "USDSB",
    "SUSD", "EUR", "GBP", "AUD", "BRL", "TRY", "EURI", "AEUR", "USD1", "XUSD", "BFUSD",
    "RLUSD", "USDE", "PYUSD", "FRAX", "LUSD", "GUSD", "IDRT", "BIDR", "BVND", "NGN", "RUB",
    "UAH", "ZAR", "PAXG", "XAUT", "WBTC", "WBETH", "BETH", "BTCST", "USDD",
}  # fmt: skip
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")
GAP_DAYS = 7
MIN_DAYS = 365
MIN_VOLUME = 2_000_000
WINDOW = 30


def base_of(symbol: str) -> str:
    """Return the coin of a USDT pair ("BTCUSDT" -> "BTC")."""
    return symbol.removesuffix(QUOTE)


def tradable(symbols: list[str]) -> list[str]:
    """Drop stablecoins, wrapped and fiat coins and leveraged tokens."""
    bases = {base_of(s) for s in symbols}
    keep = []
    for symbol in symbols:
        base = base_of(symbol)
        if base in STABLE or not base:
            continue
        if any(base.endswith(tag) and base[: -len(tag)] in bases for tag in LEVERAGED):
            continue
        keep.append(symbol)
    return keep


def segments(symbol: str, frame: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Split a symbol's candles where they stop for more than a week.

    The first part is named after the coin ("LUNA"), later ones get "#2", "#3".
    """
    base = base_of(symbol)
    frame = frame[frame["Close"] > 0].sort_index()
    if frame.empty:
        return {}
    breaks = np.flatnonzero(
        np.diff(frame.index.to_numpy()) > np.timedelta64(GAP_DAYS, "D")
    )
    edges = [0, *(breaks + 1), len(frame)]
    parts = {}
    for k, (a, b) in enumerate(zip(edges[:-1], edges[1:], strict=True)):
        parts[base if k == 0 else f"{base}#{k + 1}"] = frame.iloc[a:b]
    return parts


def coins(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Return every coin's candles, by coin name, from the pairs' candles."""
    out = {}
    for symbol in tradable(sorted(frames)):
        out.update(segments(symbol, frames[symbol]))
    return out


def monthly(
    candles: dict[str, pd.DataFrame], start: date, end: date, size: int = 20
) -> dict[date, list[str]]:
    """Return each month start's universe, strongest volume first."""
    volume = pd.DataFrame(
        {c: f["QuoteVolume"] for c, f in candles.items()}
    ).sort_index()
    first_day = {c: f.index[0] for c, f in candles.items()}
    out = {}
    month = date(start.year, start.month, 1)
    while month <= end:
        day = pd.Timestamp(month)
        window = volume[
            (volume.index < day) & (volume.index >= day - pd.Timedelta(days=WINDOW))
        ]
        median = window.median()
        alive = window.iloc[-1].notna() if len(window) else median * 0
        old = pd.Series(
            {c: first_day[c] <= day - pd.Timedelta(days=MIN_DAYS) for c in median.index}
        )
        ranked = median[(median >= MIN_VOLUME) & alive & old].sort_values(
            ascending=False
        )
        out[month] = list(ranked.index[:size])
        month = (day + pd.offsets.MonthBegin(1)).date()
    return out


def membership(
    universe: dict[date, list[str]], index: pd.DatetimeIndex
) -> pd.DataFrame:
    """Return, per day of ``index``, which coins are in that month's universe."""
    names = sorted({c for members in universe.values() for c in members})
    table = pd.DataFrame(False, index=index, columns=names)
    months = sorted(universe)
    for k, month in enumerate(months):
        stop = (
            months[k + 1]
            if k + 1 < len(months)
            else index[-1].date() + timedelta(days=1)
        )
        rows = (index >= pd.Timestamp(month)) & (index < pd.Timestamp(stop))
        table.loc[rows, universe[month]] = True
    return table
