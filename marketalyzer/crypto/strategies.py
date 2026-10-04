"""Crypto strategies on the point-in-time universe, as target weights per day.

Every function decides on a day's close from that close and earlier ones and
returns the share of equity each coin should hold after that close. The
weights go through ``neural.simulate``, which trades them with fees and
slippage. A coin whose candles end (delisted) is sold at its last close.

- ``rule_held``: the paper account's rule (quarterly ranking, supertrend,
  BTC filter) or, without a ranking, supertrend on every universe coin.
- ``donchian``: the multi-lookback Donchian ensemble of Zarattini, Pagani
  and Barbon (2025): long when the close breaks the highest close of the past
  L days, out below a trailing stop at the channel middle; the signal is the
  average over nine lookbacks.
- ``risk_weights``: positions sized by inverse volatility and scaled to a
  portfolio volatility target, with a cap on gross exposure.
- ``weekly_momentum``: the strongest coins over four weeks, kept until they
  fall below a rank buffer.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from marketalyzer.crypto import momentum, neural
from marketalyzer.scripting import ta

YEAR = 365
LOOKBACKS = (5, 10, 20, 30, 60, 90, 150, 250, 360)
VOL_WINDOW = 90
REGIME_AVERAGES = (20, 50, 100, 200)


# --- Data ---------------------------------------------------------------------------


def table(candles: dict[str, pd.DataFrame], column: str = "Close") -> pd.DataFrame:
    """Return one column of every coin side by side on a daily calendar.

    Gaps of up to three days keep the last value; nothing is filled past a
    coin's last candle.
    """
    out = pd.DataFrame({c: f[column] for c, f in candles.items()}).sort_index()
    out = out.reindex(pd.date_range(out.index[0], out.index[-1], freq="D"))
    last = {c: f.index[-1] for c, f in candles.items()}
    filled = out.ffill(limit=3)
    for coin, end in last.items():
        filled.loc[filled.index > end, coin] = np.nan
    return filled


def exit_at_end(targets: np.ndarray, closes: pd.DataFrame) -> np.ndarray:
    """Zero each coin's target from its last close on (sold before delisting)."""
    out = targets.copy()
    valid = closes.notna().to_numpy()
    rows = len(closes)
    for j in range(closes.shape[1]):
        seen = np.flatnonzero(valid[:, j])
        if len(seen) and seen[-1] < rows - 1:
            out[seen[-1] :, j] = 0.0
    return out


def realized_vol(closes: pd.DataFrame, window: int = VOL_WINDOW) -> pd.DataFrame:
    """Return each coin's annualized volatility of daily log returns."""
    logs = np.log(closes).diff()
    return logs.rolling(window, min_periods=window // 2).std() * np.sqrt(YEAR)


def btc_regime(close: pd.Series) -> np.ndarray:
    """Return the share of BTC's 20/50/100/200-day averages it closed above."""
    values = close.to_numpy(dtype=float)
    above = [
        values > np.nan_to_num(ta.sma(values, n), nan=np.inf) for n in REGIME_AVERAGES
    ]
    return np.mean(above, axis=0)


# --- Signals ------------------------------------------------------------------------


def donchian(close: np.ndarray, lookbacks: tuple[int, ...] = LOOKBACKS) -> np.ndarray:
    """Return the share of lookbacks whose Donchian trend is long on each close."""
    close = np.asarray(close, dtype=float)
    series = pd.Series(close)
    votes = np.zeros(len(close))
    for length in lookbacks:
        upper = series.rolling(length).max().shift(1).to_numpy()
        lower = series.rolling(length).min().shift(1).to_numpy()
        middle = (upper + lower) / 2
        long, stop = False, -np.inf
        for t in range(len(close)):
            if np.isnan(close[t]) or np.isnan(upper[t]):
                long, stop = False, -np.inf
                continue
            if long:
                stop = max(stop, middle[t])
                if close[t] < stop:
                    long = False
            elif close[t] > upper[t]:
                long, stop = True, middle[t]
            votes[t] += long
    return votes / len(lookbacks)


def rule_held(
    closes: pd.DataFrame,
    members: pd.DataFrame,
    flags: dict[str, tuple[np.ndarray, np.ndarray]],
    btc_ok: np.ndarray,
    start: date,
    top: int | None,
    lookback: int = 90,
    offset_days: int = 0,
) -> np.ndarray:
    """Return which coins the trend rule holds after each close (0/1).

    With ``top``, the universe's ``top`` strongest coins over ``lookback``
    days are picked at each quarter start (shifted by ``offset_days``); without
    it every universe coin is a candidate. A candidate is bought on a
    supertrend entry while ``btc_ok`` and sold on an exit or when it stops
    being a candidate.
    """
    codes = list(closes.columns)
    member = members.reindex(
        index=closes.index, columns=codes, fill_value=False
    ).to_numpy()
    prices = closes.to_numpy(dtype=float)
    held = np.zeros(closes.shape)
    first = int(np.searchsorted(closes.index, pd.Timestamp(start)))
    picks: set[int] = set()
    quarter = None
    state = np.zeros(len(codes), dtype=bool)
    for t in range(first, len(closes)):
        day = closes.index[t].date()
        if top is None:
            picks = set(np.flatnonzero(member[t]))
        else:
            q = momentum.quarter_start(day - timedelta(days=offset_days))
            if q != quarter:
                quarter = q
                past = (
                    prices[t - lookback - 1]
                    if t > lookback
                    else np.full(len(codes), np.nan)
                )
                score = prices[t - 1] / past - 1
                ranked = [j for j in np.argsort(-np.nan_to_num(score, nan=-np.inf))
                          if member[t, j] and not np.isnan(score[j])]  # fmt: skip
                picks = set(ranked[:top])
        for j in np.flatnonzero(state):
            if j not in picks or flags[codes[j]][1][t]:
                state[j] = False
        if btc_ok[t]:
            for j in picks:
                if flags[codes[j]][0][t]:
                    state[j] = True
        held[t] = state
    return held


def weekly_momentum(
    closes: pd.DataFrame,
    members: pd.DataFrame,
    btc_ok: np.ndarray,
    start: date,
    top: int = 5,
    keep: int = 8,
    lookback: int = 28,
) -> np.ndarray:
    """Hold the ``top`` strongest coins over four weeks, re-ranked every 7 days.

    A held coin stays until it falls below rank ``keep`` or leaves the
    universe; everything is sold while BTC is below its average.
    """
    codes = list(closes.columns)
    member = members.reindex(
        index=closes.index, columns=codes, fill_value=False
    ).to_numpy()
    prices = closes.to_numpy(dtype=float)
    held = np.zeros(closes.shape)
    first = int(np.searchsorted(closes.index, pd.Timestamp(start)))
    state: list[int] = []
    for t in range(first, len(closes)):
        if (t - first) % 7 == 0:
            score = prices[t] / prices[t - lookback] - 1
            ranked = [j for j in np.argsort(-np.nan_to_num(score, nan=-np.inf))
                      if member[t, j] and not np.isnan(score[j])]  # fmt: skip
            if not btc_ok[t]:
                state = []
            else:
                state = [j for j in state if j in ranked[:keep]]
                state += [j for j in ranked if j not in state][: top - len(state)]
        state = [j for j in state if member[t, j]]
        held[t, state] = 1 / top
    return held


# --- Sizing -------------------------------------------------------------------------


def risk_weights(
    signal: np.ndarray,
    closes: pd.DataFrame,
    target_vol: float,
    gross_cap: float = 1.0,
    window: int = VOL_WINDOW,
) -> np.ndarray:
    """Size ``signal`` (0-1 per coin and day) by inverse volatility.

    Each coin's raw weight is its signal over its volatility; the day's
    weights are scaled so the portfolio's volatility, from the last
    ``window`` days' covariance, meets ``target_vol``, with gross exposure at
    most ``gross_cap``.
    """
    logs = np.log(closes).diff().to_numpy()
    vol = realized_vol(closes, window).to_numpy()
    raw = np.where((signal > 0) & (vol > 0), signal / np.where(vol > 0, vol, 1.0), 0.0)
    raw = np.nan_to_num(raw)
    out = np.zeros_like(raw)
    for t in np.flatnonzero(raw.sum(axis=1) > 0):
        names = np.flatnonzero(raw[t] > 0)
        w = raw[t, names]
        recent = np.nan_to_num(logs[max(0, t - window + 1) : t + 1][:, names])
        cov = np.atleast_2d(np.cov(recent, rowvar=False)) * YEAR
        portfolio = float(np.sqrt(max(w @ cov @ w, 1e-12)))
        scale = min(target_vol / portfolio, gross_cap / w.sum())
        out[t, names] = w * scale
    return out


def crowded(funding: pd.DataFrame, index: pd.DatetimeIndex) -> np.ndarray:
    """Return where longs are crowded: the week's funding in its top tenth of a year.

    ``funding`` holds each coin's daily funding rate (columns as the closes);
    a coin without a perpetual is never crowded.
    """
    week = funding.reindex(index).rolling(7, min_periods=5).mean()
    high = week.rolling(YEAR, min_periods=120).quantile(0.9).shift(1)
    return ((week > high) & (week > 0)).to_numpy()


def equal_slots(held: np.ndarray, slots: int) -> np.ndarray:
    """Give every held coin 1/``slots`` of the equity."""
    return held / slots


def run(closes: pd.DataFrame, targets: np.ndarray, first: int, cost: float = 1.0,
        band: float = np.inf, relative: float = 0.0) -> neural.Book:  # fmt: skip
    """Trade ``targets`` (sold at each coin's last close) with ``cost`` times the costs.

    By default only entries and exits trade; ``band`` and ``relative`` turn on
    rebalancing toward the targets (see ``neural.simulate``).
    """
    return neural.simulate(
        closes, exit_at_end(targets, closes), band, first, relative,
        neural.FEE * cost, neural.SLIPPAGE * cost,
    )  # fmt: skip
