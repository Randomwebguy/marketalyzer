"""Technical analysis functions with Pine Script semantics, on numpy arrays.

Every function is causal: the value on a bar only depends on that bar and the
ones before it, so a script can never peek into the future. Missing values are
``nan``; moving averages are ``nan`` until they have a full window, and the
recursive ones (EMA, RMA) start from the SMA of their first window, as in Pine.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

NAN = float("nan")


def _f(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def _rolling(values, length: int) -> pd.core.window.Rolling:
    return pd.Series(_f(values)).rolling(length, min_periods=length)


def shift(values, bars: int = 1, fill=NAN) -> np.ndarray:
    """Return ``values`` delayed by ``bars`` bars (``x[bars]`` in Pine)."""
    values = np.asarray(values)
    if bars <= 0:
        return values.copy()
    if values.dtype == bool:
        fill = False if isinstance(fill, float) and math.isnan(fill) else bool(fill)
    out = np.empty_like(values, dtype=values.dtype if values.dtype == bool else float)
    out[:bars] = fill
    out[bars:] = values[:-bars] if bars < len(values) else values[:0]
    return out


def sma(source, length: int) -> np.ndarray:
    """Return the simple moving average."""
    return _rolling(source, length).mean().to_numpy()


def _seeded_ewm(source, length: int, alpha: float) -> np.ndarray:
    values = _f(source)
    out = np.full(len(values), NAN)
    seed = sma(values, length)
    valid = np.flatnonzero(~np.isnan(seed))
    if not len(valid):
        return out
    first = valid[0]
    tail = values[first:].copy()
    tail[0] = seed[first]
    out[first:] = pd.Series(tail).ewm(alpha=alpha, adjust=False).mean().to_numpy()
    out[first:][np.isnan(values[first:])] = NAN
    out[first] = seed[first]
    return out


def ema(source, length: int) -> np.ndarray:
    """Exponential moving average, alpha = 2 / (length + 1)."""
    return _seeded_ewm(source, length, 2 / (length + 1))


def rma(source, length: int) -> np.ndarray:
    """Wilder's moving average used by RSI and ATR, alpha = 1 / length."""
    return _seeded_ewm(source, length, 1 / length)


def wma(source, length: int) -> np.ndarray:
    """Linearly weighted moving average."""
    values = _f(source)
    out = np.full(len(values), NAN)
    if len(values) < length:
        return out
    weights = np.arange(1, length + 1, dtype=float)
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    out[length - 1 :] = windows @ weights / weights.sum()
    return out


def hma(source, length: int) -> np.ndarray:
    """Hull moving average."""
    half = wma(source, max(int(length / 2), 1))
    full = wma(source, length)
    return wma(2 * half - full, max(int(math.floor(math.sqrt(length))), 1))


def swma(source) -> np.ndarray:
    """Symmetrically weighted moving average over 4 bars (1, 2, 2, 1)."""
    values = _f(source)
    return (shift(values, 3) + 2 * shift(values, 2) + 2 * shift(values, 1) + values) / 6


def vwma(source, volume, length: int) -> np.ndarray:
    """Volume-weighted moving average."""
    return sma(_f(source) * _f(volume), length) / sma(volume, length)


def alma(source, length: int, offset: float = 0.85, sigma: float = 6) -> np.ndarray:
    """Arnaud Legoux moving average."""
    values = _f(source)
    out = np.full(len(values), NAN)
    if len(values) < length:
        return out
    m = offset * (length - 1)
    s = length / sigma
    weights = np.exp(-((np.arange(length) - m) ** 2) / (2 * s * s))
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    out[length - 1 :] = windows @ weights / weights.sum()
    return out


def linreg(source, length: int, offset: int = 0) -> np.ndarray:
    """Least-squares line over ``length`` bars, read ``offset`` bars back."""
    values = _f(source)
    out = np.full(len(values), NAN)
    if len(values) < length:
        return out
    x = np.arange(length, dtype=float)
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    x_mean = x.mean()
    y_mean = windows.mean(axis=1)
    slope = ((x - x_mean) * (windows - y_mean[:, None])).sum(axis=1) / (
        (x - x_mean) ** 2
    ).sum()
    intercept = y_mean - slope * x_mean
    out[length - 1 :] = intercept + slope * (length - 1 - offset)
    return out


def change(source, length: int = 1) -> np.ndarray:
    """Difference from ``length`` bars ago; for booleans, whether it changed."""
    values = np.asarray(source)
    if values.dtype == bool:
        previous = shift(values, length, fill=False)
        out = values != previous
        out[:length] = False
        return out
    return _f(values) - shift(_f(values), length)


def mom(source, length: int) -> np.ndarray:
    """Momentum: ``source - source[length]``."""
    return change(_f(source), length)


def roc(source, length: int) -> np.ndarray:
    """Rate of change in percent."""
    values = _f(source)
    previous = shift(values, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 * (values - previous) / previous
    return _clean(out)


def _clean(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    values[~np.isfinite(values)] = NAN
    return values


def highest(source, length: int) -> np.ndarray:
    """Highest value over the last ``length`` bars."""
    return _rolling(source, length).max().to_numpy()


def lowest(source, length: int) -> np.ndarray:
    """Lowest value over the last ``length`` bars."""
    return _rolling(source, length).min().to_numpy()


def _extreme_bars(source, length: int, pick) -> np.ndarray:
    values = _f(source)
    out = np.full(len(values), NAN)
    if len(values) < length:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    position = pick(windows[:, ::-1], axis=1)
    out[length - 1 :] = -position
    out[length - 1 :][np.isnan(windows).any(axis=1)] = NAN
    return out


def highestbars(source, length: int) -> np.ndarray:
    """Offset (zero or negative) to the highest bar in the window."""
    return _extreme_bars(source, length, np.argmax)


def lowestbars(source, length: int) -> np.ndarray:
    """Offset (zero or negative) to the lowest bar in the window."""
    return _extreme_bars(source, length, np.argmin)


def stdev(source, length: int, biased: bool = True) -> np.ndarray:
    """Return the standard deviation; ``biased`` uses the population formula."""
    return _rolling(source, length).std(ddof=0 if biased else 1).to_numpy()


def variance(source, length: int, biased: bool = True) -> np.ndarray:
    """Variance over the window."""
    return _rolling(source, length).var(ddof=0 if biased else 1).to_numpy()


def dev(source, length: int) -> np.ndarray:
    """Mean absolute deviation from the SMA."""
    values = _f(source)
    out = np.full(len(values), NAN)
    if len(values) < length:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, length)
    out[length - 1 :] = np.abs(windows - windows.mean(axis=1)[:, None]).mean(axis=1)
    return out


def median(source, length: int) -> np.ndarray:
    """Return the rolling median."""
    return _rolling(source, length).median().to_numpy()


def percentrank(source, length: int) -> np.ndarray:
    """Percent of the previous ``length`` values at or below the current one."""
    values = _f(source)
    out = np.full(len(values), NAN)
    if len(values) <= length:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, length + 1)
    current = windows[:, -1:]
    out[length:] = (windows[:, :-1] <= current).sum(axis=1) / length * 100
    out[length:][np.isnan(windows).any(axis=1)] = NAN
    return out


def correlation(source1, source2, length: int) -> np.ndarray:
    """Pearson correlation over the window."""
    return (
        pd.Series(_f(source1))
        .rolling(length, min_periods=length)
        .corr(pd.Series(_f(source2)))
        .to_numpy()
    )


def math_sum(source, length: int) -> np.ndarray:
    """Return the rolling sum (``math.sum`` in Pine)."""
    return _rolling(source, length).sum().to_numpy()


def cum(source) -> np.ndarray:
    """Return the running total; missing values count as zero."""
    return np.cumsum(np.nan_to_num(_f(source)))


def rsi(source, length: int) -> np.ndarray:
    """Relative strength index with Wilder's smoothing."""
    delta = change(_f(source))
    up = rma(np.where(np.isnan(delta), NAN, np.maximum(delta, 0)), length)
    down = rma(np.where(np.isnan(delta), NAN, -np.minimum(delta, 0)), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(
            down == 0, 100.0, np.where(up == 0, 0.0, 100 - 100 / (1 + up / down))
        )
    out[np.isnan(up) | np.isnan(down)] = NAN
    return out


def macd(source, fast: int, slow: int, signal: int):
    """Return ``(macd, signal, histogram)``."""
    line = ema(source, fast) - ema(source, slow)
    signal_line = ema(line, signal)
    return line, signal_line, line - signal_line


def stoch(source, high, low, length: int) -> np.ndarray:
    """Stochastic %K (unsmoothed), 0-100."""
    lowest_low = lowest(low, length)
    highest_high = highest(high, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean(100 * (_f(source) - lowest_low) / (highest_high - lowest_low))


def cci(source, length: int) -> np.ndarray:
    """Commodity channel index."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean(
            (_f(source) - sma(source, length)) / (0.015 * dev(source, length))
        )


def cmo(source, length: int) -> np.ndarray:
    """Chande momentum oscillator."""
    delta = change(_f(source))
    up = math_sum(np.maximum(delta, 0), length)
    down = math_sum(-np.minimum(delta, 0), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean(100 * (up - down) / (up + down))


def mfi(source, volume, length: int) -> np.ndarray:
    """Money flow index on ``source`` (usually hlc3)."""
    values = _f(source)
    delta = change(values)
    flow = values * _f(volume)
    upper = math_sum(np.where(delta <= 0, 0.0, flow), length)
    lower = math_sum(np.where(delta >= 0, 0.0, flow), length)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + upper / lower)
    out[np.isnan(delta)] = NAN
    return out


def tr(high, low, close, handle_na: bool = False) -> np.ndarray:
    """Return the true range; without ``handle_na`` the first bar is missing."""
    high, low, close = _f(high), _f(low), _f(close)
    previous = shift(close)
    out = np.fmax(high - low, np.fmax(np.abs(high - previous), np.abs(low - previous)))
    if len(out):
        out[0] = high[0] - low[0] if handle_na else NAN
    return out


def atr(high, low, close, length: int) -> np.ndarray:
    """Average true range (RMA of the true range)."""
    return rma(tr(high, low, close, handle_na=True), length)


def wpr(high, low, close, length: int) -> np.ndarray:
    """Williams %R, -100 to 0."""
    highest_high = highest(high, length)
    lowest_low = lowest(low, length)
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean(100 * (_f(close) - highest_high) / (highest_high - lowest_low))


def dmi(high, low, close, di_length: int, adx_smoothing: int):
    """Return ``(+DI, -DI, ADX)``."""
    up = change(_f(high))
    down = -change(_f(low))
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    plus_dm[np.isnan(up)] = NAN
    minus_dm[np.isnan(down)] = NAN
    true_range = rma(tr(high, low, close, handle_na=True), di_length)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = _clean(100 * rma(plus_dm, di_length) / true_range)
        minus = _clean(100 * rma(minus_dm, di_length) / true_range)
        total = plus + minus
        adx = 100 * rma(
            np.abs(plus - minus) / np.where(total == 0, 1, total), adx_smoothing
        )
    return plus, minus, adx


def supertrend(high, low, close, factor: float, atr_period: int):
    """Return ``(supertrend, direction)``; direction -1 is an up trend, 1 down."""
    high, low, close = _f(high), _f(low), _f(close)
    source = (high + low) / 2
    average = atr(high, low, close, atr_period)
    upper = source + factor * average
    lower = source - factor * average
    n = len(close)
    line = np.full(n, NAN)
    direction = np.full(n, NAN)
    prev_upper = prev_lower = prev_line = NAN
    for i in range(n):
        u, lo = upper[i], lower[i]
        if i:
            pl = 0.0 if math.isnan(prev_lower) else prev_lower
            pu = 0.0 if math.isnan(prev_upper) else prev_upper
            if not (lo > pl or close[i - 1] < pl):
                lo = pl
            if not (u < pu or close[i - 1] > pu):
                u = pu
        else:
            pu = NAN
        if i == 0 or math.isnan(average[i - 1]):
            d = 1.0
        elif prev_line == pu:
            d = -1.0 if close[i] > u else 1.0
        else:
            d = 1.0 if close[i] < lo else -1.0
        value = lo if d == -1 else u
        line[i], direction[i] = value, d
        prev_upper, prev_lower, prev_line = u, lo, value
    line[np.isnan(average)] = NAN
    direction[np.isnan(average)] = NAN
    return line, direction


def sar(high, low, start: float, inc: float, maximum: float) -> np.ndarray:
    """Parabolic SAR."""
    high, low = _f(high), _f(low)
    n = len(high)
    out = np.full(n, NAN)
    if n < 2:
        return out
    rising = high[1] >= high[0]
    accel = start
    extreme = high[1] if rising else low[1]
    value = low[0] if rising else high[0]
    out[1] = value
    for i in range(2, n):
        value = value + accel * (extreme - value)
        if rising:
            value = min(value, low[i - 1], low[i - 2])
            if low[i] < value:
                rising, value, extreme, accel = False, extreme, low[i], start
            elif high[i] > extreme:
                extreme, accel = high[i], min(accel + inc, maximum)
        else:
            value = max(value, high[i - 1], high[i - 2])
            if high[i] > value:
                rising, value, extreme, accel = True, extreme, high[i], start
            elif low[i] < extreme:
                extreme, accel = low[i], min(accel + inc, maximum)
        out[i] = value
    return out


def bb(source, length: int, mult: float):
    """Bollinger bands: ``(basis, upper, lower)``."""
    basis = sma(source, length)
    width = mult * stdev(source, length)
    return basis, basis + width, basis - width


def bbw(source, length: int, mult: float) -> np.ndarray:
    """Bollinger band width relative to the basis."""
    basis, upper, lower = bb(source, length, mult)
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean((upper - lower) / basis)


def kc(source, high, low, close, length: int, mult: float, use_true_range=True):
    """Keltner channels: ``(basis, upper, lower)``."""
    basis = ema(source, length)
    span = (
        tr(high, low, close, handle_na=True) if use_true_range else _f(high) - _f(low)
    )
    width = mult * ema(span, length)
    return basis, basis + width, basis - width


def crossover(a, b) -> np.ndarray:
    """Return whether ``a`` crossed above ``b`` on each bar."""
    a, b = _f(a), _f(b)
    return (a > b) & (shift(a) <= shift(b))


def crossunder(a, b) -> np.ndarray:
    """Return whether ``a`` crossed below ``b`` on each bar."""
    a, b = _f(a), _f(b)
    return (a < b) & (shift(a) >= shift(b))


def cross(a, b) -> np.ndarray:
    """Return whether ``a`` and ``b`` crossed in either direction."""
    return crossover(a, b) | crossunder(a, b)


def rising(source, length: int) -> np.ndarray:
    """``source`` is above every one of its previous ``length`` values."""
    values = _f(source)
    return values > highest(shift(values), length)


def falling(source, length: int) -> np.ndarray:
    """``source`` is below every one of its previous ``length`` values."""
    values = _f(source)
    return values < lowest(shift(values), length)


def barssince(condition) -> np.ndarray:
    """Bars since ``condition`` was last true; missing before the first time."""
    condition = np.asarray(condition, dtype=bool)
    positions = np.arange(len(condition))
    if not len(condition):
        return positions.astype(float)
    last = np.maximum.accumulate(np.where(condition, positions, -1))
    out = (positions - last).astype(float)
    out[last < 0] = NAN
    return out


def valuewhen(condition, source, occurrence: int = 0) -> np.ndarray:
    """``source`` on the ``occurrence``-th most recent bar where ``condition``."""
    condition = np.asarray(condition, dtype=bool)
    values = _f(np.broadcast_to(source, condition.shape))
    hits = np.flatnonzero(condition)
    count = np.cumsum(condition) - 1 - occurrence
    out = np.full(len(condition), NAN)
    ok = count >= 0
    out[ok] = values[hits[count[ok]]]
    return out


def _pivots(source, left: int, right: int, pick) -> np.ndarray:
    values = _f(source)
    n = len(values)
    out = np.full(n, NAN)
    size = left + right + 1
    if n < size:
        return out
    windows = np.lib.stride_tricks.sliding_window_view(values, size)
    center = windows[:, left]
    before = pick(windows[:, :left], axis=1) if left else np.full(len(center), np.nan)
    after = (
        pick(windows[:, left + 1 :], axis=1) if right else np.full(len(center), np.nan)
    )
    if pick is np.max:
        found = ((center > before) | (left == 0)) & ((center >= after) | (right == 0))
    else:
        found = ((center < before) | (left == 0)) & ((center <= after) | (right == 0))
    found &= ~np.isnan(windows).any(axis=1)
    out[size - 1 :] = np.where(found, center, NAN)
    return out


def pivothigh(source, left: int, right: int) -> np.ndarray:
    """Pivot high, reported ``right`` bars after it happens (when confirmed)."""
    return _pivots(source, left, right, np.max)


def pivotlow(source, left: int, right: int) -> np.ndarray:
    """Pivot low, reported ``right`` bars after it happens (when confirmed)."""
    return _pivots(source, left, right, np.min)


def obv(close, volume) -> np.ndarray:
    """On-balance volume."""
    delta = change(_f(close))
    return cum(np.sign(np.nan_to_num(delta)) * _f(volume))


def accdist(high, low, close, volume) -> np.ndarray:
    """Accumulation/distribution line."""
    high, low, close = _f(high), _f(low), _f(close)
    span = high - low
    with np.errstate(divide="ignore", invalid="ignore"):
        multiplier = np.where(span == 0, 0.0, ((close - low) - (high - close)) / span)
    return cum(multiplier * _f(volume))


def vwap(source, volume, sessions) -> np.ndarray:
    """Volume-weighted average price, restarting at each session (day)."""
    frame = pd.DataFrame(
        {"pv": _f(source) * _f(volume), "v": _f(volume), "day": np.asarray(sessions)}
    )
    grouped = frame.groupby("day", sort=False)
    with np.errstate(divide="ignore", invalid="ignore"):
        return _clean(
            grouped["pv"].cumsum().to_numpy() / grouped["v"].cumsum().to_numpy()
        )


def running_max(source) -> np.ndarray:
    """Highest value so far (``ta.max``)."""
    return pd.Series(_f(source)).cummax().to_numpy()


def running_min(source) -> np.ndarray:
    """Lowest value so far (``ta.min``)."""
    return pd.Series(_f(source)).cummin().to_numpy()


def fixnan(source) -> np.ndarray:
    """Replace missing values with the last known value."""
    return pd.Series(_f(source)).ffill().to_numpy()
