"""A 0-100 confidence score for a long or a short, read from 15- and 30-minute bars.

The score is computed on each finished 15-minute bar from that bar and
earlier ones; the 30-minute bars are built from the 15-minute ones and only
those already closed count. Its parts (each 0-1, weights in ``WEIGHTS``):

- ``trend30``: the 30-minute EMA 20 above (long) or below (short) the EMA 50,
  in ATRs.
- ``trend15``: the 15-minute close and EMA 20 against the EMA 50, in ATRs.
- ``momentum``: RSI 14 leaning the trade's way, less when stretched.
- ``macd``: the MACD histogram on the trade's side and growing.
- ``breakout``: the close beyond the middle of the past 20 bars' range.
- ``volume``: a bar in the trade's direction on above-average volume.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from marketalyzer.scripting import ta

WEIGHTS = {"trend30": 25, "trend15": 20, "momentum": 20, "macd": 15, "breakout": 10,
           "volume": 10}  # fmt: skip
BAR = pd.Timedelta(minutes=15)


def thirty(frame: pd.DataFrame) -> pd.DataFrame:
    """Return 30-minute bars built from 15-minute ones (index = bar start)."""
    return frame.resample("30min").agg({"Open": "first", "High": "max", "Low": "min",
                                        "Close": "last", "Volume": "sum"}).dropna()  # fmt: skip


def _smooth(x: np.ndarray) -> np.ndarray:
    return 0.5 + 0.5 * np.tanh(np.nan_to_num(x))


def scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Return per 15-minute bar the long and short scores and their parts.

    ``frame`` has Open/High/Low/Close/Volume indexed by bar start (UTC).
    Columns: ``long``, ``short``, ``atr`` (15-minute ATR 14) and
    ``long_<part>`` / ``short_<part>`` for every part.
    """
    o, h, low, c, v = (
        frame[k].to_numpy(dtype=float)
        for k in ("Open", "High", "Low", "Close", "Volume")
    )
    atr = ta.atr(h, low, c, 14)
    ema20, ema50 = ta.ema(c, 20), ta.ema(c, 50)
    rsi = ta.rsi(c, 14)
    hist = ta.macd(c, 12, 26, 9)[2]
    upper = pd.Series(h).rolling(20).max().shift(1).to_numpy()
    lower = pd.Series(low).rolling(20).min().shift(1).to_numpy()
    middle = (upper + lower) / 2
    ratio = v / pd.Series(v).rolling(20).mean().shift(1).to_numpy()

    half = thirty(frame)
    h30, l30, c30 = (half[k].to_numpy(dtype=float) for k in ("High", "Low", "Close"))
    spread30 = (ta.ema(c30, 20) - ta.ema(c30, 50)) / ta.atr(h30, l30, c30, 14)
    closed30 = pd.Series(
        spread30, index=half.index + 2 * BAR
    )  # known once the bar closes
    ends = frame.index + BAR
    x30 = closed30.reindex(closed30.index.union(ends)).ffill().reindex(ends).to_numpy()

    with np.errstate(invalid="ignore", divide="ignore"):
        x15 = ((c - ema20) + (ema20 - ema50)) / atr / 2
        reach_up = (c - middle) / (upper - middle)
        reach_down = (middle - c) / (middle - lower)
    out = {"atr": atr}
    for side, d in (("long", 1), ("short", -1)):
        lean = (rsi - 45) / 20 if d > 0 else (55 - rsi) / 20
        stretched = rsi > 78 if d > 0 else rsi < 22
        parts = {
            "trend30": _smooth(d * x30),
            "trend15": _smooth(d * x15),
            "momentum": np.clip(np.nan_to_num(lean), 0, 1)
            * np.where(stretched, 0.5, 1.0),
            "macd": 0.5 * (d * np.nan_to_num(hist) > 0)
            + 0.5 * (d * np.diff(np.nan_to_num(hist), prepend=np.nan) > 0),
            "breakout": np.clip(np.nan_to_num(reach_up if d > 0 else reach_down), 0, 1),
            "volume": np.clip((np.nan_to_num(ratio) - 0.8) / 0.8, 0, 1)
            * (d * (c - o) > 0),
        }
        total = sum(WEIGHTS[k] * parts[k] for k in WEIGHTS)
        warm = ~np.isnan(atr) & ~np.isnan(x30) & ~np.isnan(ema50)
        out[side] = np.where(warm, total, np.nan)
        out.update({f"{side}_{k}": p for k, p in parts.items()})
    return pd.DataFrame(out, index=frame.index)
