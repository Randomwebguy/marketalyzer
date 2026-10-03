"""Momentum plus trend: trade only the strongest coins, with supertrend_sik and the BTC filter.

    uv run python scripts/crypto/momentum_trend.py

At every quarter start the coins are ranked by their return over the last 90
days (known at that date); the top N share the portfolio equally and each one
is traded by supertrend_sik with entries only while BTC is above its 50-day
average. Nothing is fitted per coin. Amounts are from a 100,000 $ start;
quarters as in adaptive.py.
"""

from __future__ import annotations

import json
import types
from datetime import date, timedelta

import numpy as np
import pandas as pd

import crypto_lab as lab
from marketalyzer import blind, services
from marketalyzer.scripting import ta

START, END = date(2022, 1, 1), date(2026, 10, 3)
config = blind.BlindConfig(symbols=["BTC-USD"], start=START, end=END, script="supertrend",
                           mode="signals", costs=lab.M["costs"], slippage=lab.M["slippage"], cash=lab.CASH)
script = blind._script("supertrend", None)
frames, results = {}, {}
for code in lab.mx.CRYPTO_15:
    frames[code] = services.load_bars(code, date(2019, 1, 1), END, "1d")[0]
btc = frames["BTC-USD"]["Close"]
btc_up = pd.Series(btc.to_numpy() > ta.sma(btc.to_numpy(float), 50), index=btc.index)
for code, frame in frames.items():
    raw = script.run(frame, {"factor": 2.0, "atrPeriod": 10}, symbol=code, interval="1d")
    up = btc_up.reindex(frame.index).fillna(False).to_numpy()
    results[code] = types.SimpleNamespace(
        entries=np.asarray(raw.entries, dtype=bool) & up, exits=raw.exits, stops=raw.stops,
        limits=raw.limits, loss_ticks=raw.loss_ticks, profit_ticks=raw.profit_ticks)


def traded(code, a, b):
    frame = frames[code]
    days = pd.DatetimeIndex(frame.index).normalize()
    cut = frame[days <= pd.Timestamp(b)]
    first = int(np.searchsorted(pd.DatetimeIndex(cut.index), pd.Timestamp(a)))
    if first >= len(cut) - 1:
        return 0.0
    sim = blind.Simulation(blind.Data(code, cut, first, first, results[code], {}), config, 1e9).run()
    return (sim.equity[-1][1] / 1e9 - 1) * 100


def momentum(code, a):
    close = frames[code]["Close"]
    before = close[close.index < pd.Timestamp(a)]
    past = before[before.index <= pd.Timestamp(a - timedelta(days=90))]
    if len(before) < 120 or past.empty:
        return None
    return float(before.iloc[-1] / past.iloc[-1] - 1)


rows = []
for start in pd.date_range(START, END, freq="QS"):
    a, b = start.date(), min((start + pd.offsets.QuarterEnd(0)).date(), END)
    scores = {c: m for c in frames if (m := momentum(c, a)) is not None
              and frames[c].index[0] <= pd.Timestamp(a - timedelta(days=365))}
    ranked = sorted(scores, key=scores.get, reverse=True)
    row = {"çeyrek": f"{a:%Y-%m}", "seçilen": [c.removesuffix("-USD") for c in ranked[:5]]}
    for n in (3, 5, len(ranked)):
        label = "hepsi" if n == len(ranked) else f"en güçlü {n}"
        row[label] = round(float(np.mean([traded(c, a, b) for c in ranked[:n]])), 2)
    rows.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
table = pd.DataFrame(rows).set_index("çeyrek")
for label in ("en güçlü 3", "en güçlü 5", "hepsi"):
    money = 100_000 * float(np.prod(1 + table[label] / 100))
    last3 = 100_000 * float(np.prod(1 + table[label].iloc[-2:] / 100))
    yearly = table[label].groupby(table.index.str[:4]).apply(lambda r: (np.prod(1 + r / 100) - 1) * 100).round(1)
    print(f"{label:11} {money:>12,.0f} $ | son 3 ay {last3:>9,.0f} $ | yıllar " + " ".join(f"{y}:{v}" for y, v in yearly.items()))
table.to_csv("momentum_trend.csv")
