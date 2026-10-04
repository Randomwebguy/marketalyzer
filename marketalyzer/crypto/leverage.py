"""The crypto rule with leverage: isolated-margin perpetuals, funding, liquidation.

Each position puts up 1/``slots`` of the equity as margin and holds
``leverage`` times that in the coin. Longs pay funding on the position's
value every day. When a day's low brings a position's margin down to the
maintenance level, the position is liquidated and its whole margin is lost;
the coin is not bought again before its next fresh entry signal. Fees and
slippage are paid on the position's value, not the margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Terms:
    """Contract terms; the defaults are a large exchange's perpetuals."""

    leverage: float = 1.0
    fee: float = 0.0005  # per side, taker, on the position's value
    slippage: float = 0.001  # round trip, half on each side
    funding: float = 0.10  # per year, paid by longs on the position's value
    maintenance: float = 0.01  # margin kept back, as a share of the value


@dataclass
class Outcome:
    """Daily equity and what it cost."""

    values: np.ndarray
    trades: int = 0
    liquidations: list[tuple[str, str]] = field(default_factory=list)
    fees: float = 0.0
    funding: float = 0.0


@dataclass
class _Position:
    margin: float
    units: float
    entry: float

    def value(self, price: float) -> float:
        return self.margin + self.units * (price - self.entry)


def simulate(
    closes: pd.DataFrame,
    lows: pd.DataFrame,
    targets: np.ndarray,
    slots: int,
    terms: Terms,
    first: int = 0,
) -> Outcome:
    """Trade the rule's positions (``targets`` > 0 means held) with ``terms``."""
    codes = list(closes.columns)
    close = closes.to_numpy(dtype=float)
    low = lows.reindex_like(closes).to_numpy(dtype=float)
    half = terms.slippage / 2
    cash = 1.0
    book: dict[int, _Position] = {}
    locked: set[int] = set()
    out = Outcome(np.ones(len(close)))
    for t in range(first, len(close)):
        for i, pos in list(book.items()):
            bottom = low[t, i] if not np.isnan(low[t, i]) else close[t, i]
            if pos.value(bottom) <= terms.maintenance * pos.units * bottom:
                del book[i]
                locked.add(i)
                out.liquidations.append((str(closes.index[t].date()), codes[i]))
        for i, pos in book.items():
            if not np.isnan(close[t, i]):
                cost = pos.units * close[t, i] * terms.funding / 365
                pos.margin -= cost
                out.funding += cost
        for i in list(book):
            if targets[t, i] <= 0 and not np.isnan(close[t, i]):
                pos = book.pop(i)
                price = close[t, i] * (1 - half)
                fee = pos.units * price * terms.fee
                cash += max(pos.value(price) - fee, 0.0)
                out.fees += fee
                out.trades += 1
        locked = {i for i in locked if targets[t, i] > 0}
        equity = cash + sum(p.value(close[t, i]) for i, p in book.items())
        for i in np.flatnonzero(targets[t] > 0):
            if i in book or i in locked or np.isnan(close[t, i]):
                continue
            margin = min(equity / slots, cash)
            if margin <= 1e-9:
                continue
            price = close[t, i] * (1 + half)
            size = margin * terms.leverage
            fee = size * terms.fee
            cash -= margin
            book[i] = _Position(margin - fee, size / price, price)
            out.fees += fee
            out.trades += 1
        out.values[t] = cash + sum(
            max(p.value(close[t, i]), 0.0)
            for i, p in book.items()
            if not np.isnan(close[t, i])
        )
    return out


def lows_of(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the daily lows side by side, aligned like ``neural.closes_of``."""
    lows = pd.DataFrame({code: frame["Low"] for code, frame in frames.items()})
    lows.index = pd.DatetimeIndex(lows.index).normalize()
    return lows[~lows.index.duplicated(keep="last")].sort_index()
