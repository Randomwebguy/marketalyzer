"""The crypto rule: the strongest coins, a fast supertrend and a BTC trend filter.

Tested in ``scripts/crypto/momentum_trend.py`` (2022 to 2026-10): at each
quarter start the coins are ranked by their last 90 days; the top five are
traded by supertrend (factor 2, ATR 10), bought only while BTC is above its
50-day average.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from marketalyzer.scripting import load_script, ta

UNIVERSE = [
    "BTC-USD", "ETH-USD", "BNB-USD", "SOL-USD", "XRP-USD", "ADA-USD", "DOGE-USD",
    "TRX-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD", "BCH-USD", "XLM-USD",
    "ATOM-USD",
]  # fmt: skip
BTC = "BTC-USD"


@dataclass(frozen=True)
class Rule:
    """The rule's settings, as tested."""

    top: int = 5
    lookback_days: int = 90
    factor: float = 2.0
    atr_period: int = 10
    btc_average: int = 50
    min_history_days: int = 365


def quarter_start(day: date) -> date:
    """Return the first day of ``day``'s quarter."""
    return date(day.year, 3 * ((day.month - 1) // 3) + 1, 1)


def select(closes: dict[str, pd.Series], start: date, rule: Rule = Rule()) -> list[str]:
    """Rank coins by their return over ``rule.lookback_days`` before ``start``.

    Only closes before ``start`` count, and only coins with a year of history.
    """
    scores = {}
    for code, close in closes.items():
        before = close[close.index < pd.Timestamp(start)]
        if before.empty or before.index[0] > pd.Timestamp(
            start - timedelta(days=rule.min_history_days)
        ):
            continue
        past = before[
            before.index <= pd.Timestamp(start - timedelta(days=rule.lookback_days))
        ]
        if past.empty:
            continue
        scores[code] = float(before.iloc[-1] / past.iloc[-1] - 1)
    return sorted(scores, key=scores.get, reverse=True)[: rule.top]


def signals(frame: pd.DataFrame, rule: Rule = Rule()) -> tuple[np.ndarray, np.ndarray]:
    """Return the supertrend entry and exit flags of every bar of ``frame``.

    Each flag is set on the bar whose close turned the trend; the signal is
    causal, so a bar's flag does not change when later bars arrive.
    """
    result = load_script("supertrend").run(
        frame, {"factor": rule.factor, "atrPeriod": rule.atr_period}, interval="1d"
    )
    return np.asarray(result.entries, dtype=bool), np.asarray(result.exits, dtype=bool)


def btc_up(close: pd.Series, rule: Rule = Rule()) -> pd.Series:
    """Return, per day, whether BTC closed above its ``rule.btc_average``-day average."""
    average = ta.sma(close.to_numpy(dtype=float), rule.btc_average)
    return pd.Series(
        close.to_numpy(dtype=float) > np.nan_to_num(average, nan=np.inf),
        index=close.index,
    )
