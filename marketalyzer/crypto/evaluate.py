"""Judge a crypto backtest honestly: period results and the deflated Sharpe ratio.

Crypto trades every day, so a year is 365 bars. The deflated Sharpe ratio
(Bailey & López de Prado, 2014) asks how likely the best of ``trials`` tried
settings has a true Sharpe above zero, given how much the trials' Sharpe
ratios spread and how skewed and fat-tailed its returns are.
"""

from __future__ import annotations

import math
from itertools import combinations
from statistics import NormalDist

import numpy as np
import pandas as pd

YEAR = 365
EULER = 0.5772156649015329
MONEY = 100_000


def measure(values: np.ndarray, index: pd.DatetimeIndex, a: int, b: int) -> dict:
    """Return money from 100 k $, drawdown, Sharpe and yearly returns of rows [a, b).

    The base is the close before row ``a`` (row ``a - 1``).
    """
    curve = np.asarray(values[a - 1 : b], dtype=float) / float(values[a - 1])
    logs = np.diff(np.log(np.maximum(curve, 1e-12)))
    peak = np.maximum.accumulate(curve)
    days = pd.Series(curve[1:], index=index[a:b])
    last = days.groupby(days.index.year).last()
    yearly = last / last.shift(1).fillna(1.0)
    std = float(logs.std())
    return {
        "money": round(MONEY * float(curve[-1])),
        "drawdown": round(float((curve / peak - 1).min()) * 100, 1),
        "sharpe": round(float(logs.mean()) / std * math.sqrt(YEAR), 2) if std else 0.0,
        "years": {int(y): round((v - 1) * 100, 1) for y, v in yearly.items()},
    }


def sharpe(returns: np.ndarray) -> float:
    """Return the per-bar Sharpe ratio of ``returns`` (not annualized)."""
    returns = np.asarray(returns, dtype=float)
    std = float(returns.std(ddof=1))
    return float(returns.mean()) / std if std else 0.0


def deflated_sharpe(returns: np.ndarray, trial_sharpes: np.ndarray) -> float:
    """Return the probability that the chosen strategy's true Sharpe is above zero.

    ``returns`` are the chosen strategy's per-bar returns; ``trial_sharpes``
    are the per-bar Sharpe ratios of every setting tried, the chosen included.
    """
    returns = np.asarray(returns, dtype=float)
    trials = np.asarray(trial_sharpes, dtype=float)
    n, count = len(returns), len(trials)
    observed = sharpe(returns)
    normal = NormalDist()
    if count > 1:
        spread = math.sqrt(float(trials.var(ddof=1)))
        expected = spread * ((1 - EULER) * normal.inv_cdf(1 - 1 / count)
                             + EULER * normal.inv_cdf(1 - 1 / (count * math.e)))  # fmt: skip
    else:
        expected = 0.0
    centred = returns - returns.mean()
    std = float(returns.std())
    skew = float((centred**3).mean() / std**3) if std else 0.0
    kurt = float((centred**4).mean() / std**4) if std else 3.0
    denominator = 1 - skew * observed + (kurt - 1) / 4 * observed**2
    if denominator <= 0:
        return 0.0
    z = (observed - expected) * math.sqrt(n - 1) / math.sqrt(denominator)
    return normal.cdf(z)


def overfit_probability(returns: np.ndarray, blocks: int = 16) -> float:
    """Return the probability of backtest overfitting (Bailey et al., CSCV).

    ``returns`` holds one column of per-bar returns per candidate. The rows
    are cut into ``blocks`` pieces; for every way of picking half of them as
    the training sample, the candidate with the best training Sharpe is
    ranked among all candidates on the other half. The result is how often it
    lands in the bottom half.
    """
    returns = np.asarray(returns, dtype=float)
    rows, count = returns.shape
    size = rows // blocks
    cut = returns[: size * blocks].reshape(blocks, size, count)
    sums, squares = cut.sum(axis=1), (cut**2).sum(axis=1)
    below = 0
    total = 0
    for chosen in combinations(range(blocks), blocks // 2):
        inside = np.zeros(blocks, dtype=bool)
        inside[list(chosen)] = True
        best = int(np.argmax(_sharpes(sums[inside], squares[inside], size)))
        outside = _sharpes(sums[~inside], squares[~inside], size)
        rank = (outside < outside[best]).sum() + 1  # 1 = worst
        omega = rank / (count + 1)
        below += np.log(omega / (1 - omega)) <= 0
        total += 1
    return below / total


def _sharpes(sums: np.ndarray, squares: np.ndarray, size: int) -> np.ndarray:
    n = sums.shape[0] * size
    mean = sums.sum(axis=0) / n
    var = np.maximum(squares.sum(axis=0) / n - mean**2, 1e-18)
    return mean / np.sqrt(var)
