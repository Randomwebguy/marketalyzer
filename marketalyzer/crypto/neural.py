"""NeuralEngine: learn from results which tested crypto rule to follow.

Every expert is a tested, model-free rule that trades its own shadow book
with fees and slippage. Each day the engine scores the experts by the log
return their books actually made and moves weight toward the ones that earned
it (Hedge, multiplicative weights), separately for each market state. Old
results fade, so the engine can change its mind when the market does, and
every expert keeps a floor weight, so none is forgotten for good. The engine's
own book holds the weight-blended positions of the experts.

Everything is decided on a day's close from that close and earlier ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from marketalyzer.crypto import momentum
from marketalyzer.scripting import load_script, ta

FEE = 0.001
SLIPPAGE = 0.001  # round trip, half on each side
CONTEXTS = ("none", "btc", "btc_breadth", "btc_vol")


@dataclass(frozen=True)
class Settings:
    """How fast the engine learns and forgets, and what it tells apart."""

    eta: float = 3.0  # weight of one day's log return in the scores
    floor: float = 0.05  # share spread evenly over all experts
    forget: float = 1 / 60  # part of the past scores dropped on each update
    context: str = "btc"  # one of CONTEXTS
    band: float = 0.03  # rebalance a coin only when it is this far off


# --- Data ---------------------------------------------------------------------------


def closes_of(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Return the daily closes side by side; short gaps keep the last close."""
    closes = pd.DataFrame({code: frame["Close"] for code, frame in frames.items()})
    closes.index = pd.DatetimeIndex(closes.index).normalize()
    closes = closes[~closes.index.duplicated(keep="last")].sort_index()
    return closes.ffill(limit=3)


def _flags(frames, closes, script, inputs) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Return each coin's entry and exit flags aligned to ``closes``."""
    out = {}
    for code, frame in frames.items():
        result = load_script(script).run(frame, inputs, interval="1d")
        index = pd.DatetimeIndex(frame.index).normalize()
        entries = pd.Series(np.asarray(result.entries, dtype=bool), index=index)
        exits = pd.Series(np.asarray(result.exits, dtype=bool), index=index)
        out[code] = tuple(
            s[~s.index.duplicated(keep="last")]
            .reindex(closes.index, fill_value=False)
            .to_numpy()
            for s in (entries, exits)
        )
    return out


def _above(close: pd.Series, length: int) -> np.ndarray:
    values = close.to_numpy(dtype=float)
    average = ta.sma(values, length)
    return values > np.nan_to_num(average, nan=np.inf)


# --- Experts ------------------------------------------------------------------------


def trend_targets(
    closes: pd.DataFrame,
    flags: dict[str, tuple[np.ndarray, np.ndarray]],
    btc_ok: np.ndarray,
    start: date,
    top: int,
    slots: int,
) -> np.ndarray:
    """Apply the paper account's rule with any trend signal and number of coins.

    At each quarter the ``top`` strongest coins are picked; a picked coin is
    bought (1/``slots`` of the book) on an entry flag while BTC is in its
    uptrend and sold on an exit flag or when it drops out of the pick.
    """
    codes = list(closes.columns)
    target = np.zeros(closes.shape)
    first = int(np.searchsorted(closes.index, pd.Timestamp(start)))
    rule = momentum.Rule(top=top)
    series = {code: closes[code].dropna() for code in codes}
    quarter, chosen, held = None, [], set()
    for t in range(first, len(closes)):
        day = closes.index[t].date()
        if momentum.quarter_start(day) != quarter:
            quarter = momentum.quarter_start(day)
            chosen = momentum.select(series, quarter, rule)
            held &= set(chosen)
        for code in list(held):
            if flags[code][1][t]:
                held.discard(code)
        if btc_ok[t]:
            held |= {code for code in chosen if flags[code][0][t]}
        for code in held:
            target[t, codes.index(code)] = 1 / slots
    return target


def rotation_targets(
    closes: pd.DataFrame,
    btc_ok: np.ndarray,
    start: date,
    top: int = 3,
    lookback: int = 63,
    skip: int = 21,
) -> np.ndarray:
    """Monthly momentum rotation, all cash while BTC is below its 200-day average.

    On the first close and each month's last close the coins are ranked by
    their return from ``lookback`` to ``skip`` days earlier; the ``top`` share
    the book. Coins that stay are left alone.
    """
    target = np.zeros(closes.shape)
    first = int(np.searchsorted(closes.index, pd.Timestamp(start)))
    prices = closes.to_numpy(dtype=float)
    month = closes.index.month
    picks: list[int] = []
    for t in range(first, len(closes)):
        if t in (first, len(closes) - 1) or month[t + 1] != month[t]:
            score = prices[t - skip] / prices[t - lookback] - 1
            ranked = [i for i in np.argsort(-np.nan_to_num(score, nan=-np.inf))
                      if not np.isnan(score[i])]  # fmt: skip
            picks = ranked[:top] if btc_ok[t] else []
        target[t, picks] = 1 / top
    return target


def experts_of(
    frames: dict[str, pd.DataFrame], start: date
) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Return the closes and each expert's daily target weights from ``start``."""
    closes = closes_of(frames)
    btc = closes[momentum.BTC]
    up50, up200 = _above(btc, 50), _above(btc, 200)
    trend = _flags(frames, closes, "supertrend", {"factor": 2.0, "atrPeriod": 10})
    sma = _flags(frames, closes, "sma_cross", {"fastLength": 20, "slowLength": 100})
    count = len(closes.columns)
    experts = {
        "en güçlü 5 + supertrend": trend_targets(closes, trend, up50, start, 5, 5),
        "en güçlü 3 + supertrend": trend_targets(closes, trend, up50, start, 3, 3),
        "hepsi + supertrend": trend_targets(closes, trend, up50, start, count, count),
        "rotasyon 3 ay/en iyi 3": rotation_targets(closes, up200, start),
        "hepsi + SMA 20/100": trend_targets(closes, sma, up50, start, count, count),
        "nakit": np.zeros(closes.shape),
    }
    return closes, experts


# --- Books --------------------------------------------------------------------------


@dataclass
class Book:
    """A simulated book: value and weights after each close's trades."""

    values: np.ndarray
    weights: np.ndarray
    traded: float  # total traded value, as a multiple of the starting cash

    def returns(self) -> np.ndarray:
        """Return each day's log return (zero on the first day)."""
        out = np.zeros(len(self.values))
        out[1:] = np.log(self.values[1:] / self.values[:-1])
        return out


def simulate(
    closes: pd.DataFrame, targets: np.ndarray, band: float = np.inf, first: int = 0
) -> Book:
    """Trade toward ``targets`` on each close from row ``first``, with costs.

    A coin is sold out when its target is zero and bought when it is not held
    and its target is not; otherwise it is traded back to its target only when
    it is more than ``band`` away. Sells come first; buys are capped at cash.
    """
    prices = closes.to_numpy(dtype=float)
    rows, n = prices.shape
    units, cash, traded = np.zeros(n), 1.0, 0.0
    values = np.ones(rows)
    weights = np.zeros((rows, n))
    buy_cost = (1 + SLIPPAGE / 2) * (1 + FEE)
    sell_keep = (1 - SLIPPAGE / 2) * (1 - FEE)
    for t in range(first, rows):
        price = np.nan_to_num(prices[t], nan=0.0)
        live = price > 0
        value = cash + float(units @ price)
        held = units * price / value
        goal = np.where(live, targets[t], 0.0)
        out = live & (units > 0) & ((goal == 0) | (held - goal > band))
        for i in np.flatnonzero(out):
            sold = units[i] if goal[i] == 0 else (held[i] - goal[i]) * value / price[i]
            cash += sold * price[i] * sell_keep
            traded += sold * price[i]
            units[i] -= sold
        into = live & (goal > 0) & ((units == 0) | (goal - held > band))
        for i in np.flatnonzero(into):
            spend = min((goal[i] - held[i]) * value, cash)
            if spend <= 0:
                continue
            units[i] += spend / (price[i] * buy_cost)
            cash -= spend
            traded += spend
        values[t] = cash + float(units @ price)
        weights[t] = units * price / values[t]
    return Book(values, weights, traded)


# --- The engine ---------------------------------------------------------------------


def contexts(closes: pd.DataFrame, kind: str) -> np.ndarray:
    """Return each day's market state (0-3, or 0 for ``none``) from its close."""
    if kind not in CONTEXTS:
        raise ValueError(f"unknown context: {kind}")
    btc = closes[momentum.BTC]
    if kind == "none":
        return np.zeros(len(closes), dtype=int)
    up200 = _above(btc, 200).astype(int)
    if kind == "btc":
        return 2 * up200 + _above(btc, 50).astype(int)
    if kind == "btc_breadth":
        above = np.column_stack([_above(closes[c], 50) for c in closes.columns])
        listed = closes.notna().to_numpy().sum(axis=1)
        breadth = above.sum(axis=1) / np.maximum(listed, 1)
        return 2 * up200 + (breadth > 0.5).astype(int)
    vol = np.log(btc).diff().rolling(30).std()
    calm = (vol < vol.rolling(365, min_periods=120).median()).to_numpy()
    return 2 * up200 + calm.astype(int)


def learn(rewards: np.ndarray, states: np.ndarray, settings: Settings) -> np.ndarray:
    """Return the experts' weights decided on each close (Hedge per state).

    ``rewards[t, k]`` is expert k's log return over day t. Day t's result is
    credited to the state the decision was made in (day t-1's), and the
    weights for day t+1 are read from day t's state.
    """
    rows, count = rewards.shape
    scores = np.zeros((int(states.max()) + 1, count))
    weights = np.empty((rows, count))
    for t in range(rows):
        if t:
            s = states[t - 1]
            scores[s] = (1 - settings.forget) * scores[s] + settings.eta * rewards[t]
        z = scores[states[t]]
        w = np.exp(z - z.max())
        weights[t] = (1 - settings.floor) * w / w.sum() + settings.floor / count
    return weights


def run(
    closes: pd.DataFrame,
    experts: dict[str, np.ndarray],
    settings: Settings,
    first: int = 0,
    books: dict[str, Book] | None = None,
) -> tuple[Book, np.ndarray, dict[str, Book]]:
    """Run every expert's shadow book and the engine's book from row ``first``.

    Returns the engine's book, the experts' weights per day and their books.
    """
    books = books or {name: simulate(closes, target, first=first)
                      for name, target in experts.items()}  # fmt: skip
    rewards = np.column_stack([book.returns() for book in books.values()])
    states = contexts(closes, settings.context)
    weights = learn(rewards[first:], states[first:], settings)
    mix = np.zeros(closes.shape)
    held = np.stack([book.weights for book in books.values()], axis=1)
    mix[first:] = np.einsum("tk,tkn->tn", weights, held[first:])
    full = np.zeros((len(closes), len(books)))
    full[first:] = weights
    return simulate(closes, mix, settings.band, first), full, books
